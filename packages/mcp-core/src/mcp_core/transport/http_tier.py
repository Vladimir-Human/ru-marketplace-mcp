"""Anonymous HTTP tier: polite, bounded, retry-aware reads of public endpoints.

This is tier 1 of the two-tier transport model. It suits marketplaces whose
catalog endpoints answer plain HTTPS requests (Wildberries is the reference
case). When a source rejects datacenter traffic outright, the connector falls
through to ``mcp_core.transport.chrome_cdp`` instead.

Three invariants every connector inherits from here:

**Politeness over speed.** A minimum gap between requests is enforced per client,
because unofficial endpoints are shared infrastructure and hammering them is
both rude and the fastest way to get an IP banned.

**Bounded bodies.** Responses are read in chunks against a hard byte cap, so a
compromised CDN or MITM cannot exhaust memory by streaming an endless body.

**Retry only what a retry can fix.** Connect/read timeouts, resets, and gateway
statuses (502/503/504) get a bounded retry with backoff. A 429 never does —
retrying a rate limit deepens the hole — and neither does any other 4xx, since a
repeat request cannot change a client error.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# Gateway/overload statuses worth one more attempt. 429 is deliberately absent:
# retrying a rate limit makes it worse. 4xx are absent because a repeat request
# cannot change a client error.
RETRYABLE_STATUSES = frozenset({502, 503, 504})

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def proxy_from_env(*env_names: str) -> str | None:
    """First non-empty proxy URL among ``env_names``, then the standard vars.

    Lets a connector expose its own knob (e.g. ``WB_PROXY``) while still
    honouring the conventional ``HTTPS_PROXY``/``ALL_PROXY`` settings that users
    already have configured.
    """
    candidates = (*env_names, "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
    for name in candidates:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


class PoliteGate(Protocol):
    """Anything that can space out requests.

    Structural, not nominal, so a connector that already owns a polite gate (WB
    keeps one as module state its tests monkeypatch) can hand it to
    ``get_text_budgeted`` without inheriting from ``RateLimiter`` or giving up
    those test seams.
    """

    async def wait(self) -> None: ...


@dataclass(slots=True)
class RateLimiter:
    """Serialises requests so consecutive calls stay ``min_gap_s`` apart."""

    min_gap_s: float
    _last_ts: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def wait(self) -> None:
        if self.min_gap_s <= 0:
            return
        async with self._lock:
            elapsed = time.monotonic() - self._last_ts
            remaining = self.min_gap_s - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_ts = time.monotonic()


class BodyTooLargeError(Exception):
    """A response body exceeded the configured byte cap and was abandoned."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        super().__init__(f"response body exceeded {limit_bytes} bytes")


async def read_capped_text(response: httpx.Response, max_bytes: int) -> str:
    """Stream ``response`` into text, refusing to buffer past ``max_bytes``.

    The response must have been issued with ``stream=True`` semantics (i.e. via
    ``client.send(..., stream=True)`` or ``client.stream(...)``).
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise BodyTooLargeError(max_bytes)
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


async def get_text_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    retries: int = 2,
    backoff_s: float = 0.6,
    limiter: RateLimiter | None = None,
    headers: dict[str, str] | None = None,
    error_body_max_bytes: int | None = 64_000,
    retry_statuses: frozenset[int] | None = RETRYABLE_STATUSES,
) -> tuple[int, str]:
    """GET ``url`` and return ``(status_code, body_text)``.

    Retries genuine transport faults (``httpx.TransportError``) and, by default,
    the gateway statuses in ``RETRYABLE_STATUSES`` — a 502 from an overloaded
    upstream is a transient blip, and Detsky Mir emits them regularly.

    Notably absent from that set: **429**. Retrying a rate-limit response digs the
    hole deeper, so it is always returned to the caller, which is expected to
    surface it as ``RateLimitedError``. Same for every other 4xx: a retry cannot
    change the answer. Pass ``retry_statuses=None`` to disable status retries.

    Error responses are truncated to ``error_body_max_bytes`` by default, since a
    4xx/5xx body is only ever read for diagnostics. Pass ``None`` when an error
    status can still carry real content — Detsky Mir's search route, for one,
    answers 404 while rendering a full page of results.
    """
    retryable = retry_statuses or frozenset()
    attempt = 0
    last_exc: Exception | None = None
    last_result: tuple[int, str] | None = None
    while attempt <= retries:
        if limiter is not None:
            await limiter.wait()
        try:
            request = client.build_request("GET", url, headers=headers)
            response = await client.send(request, stream=True)
            try:
                if response.status_code >= 400 and error_body_max_bytes is not None:
                    limit = min(max_bytes, error_body_max_bytes)
                else:
                    limit = max_bytes
                text = await read_capped_text(response, limit)
                result = (response.status_code, text)
            finally:
                await response.aclose()

            if response.status_code in retryable and attempt < retries:
                last_result = result
                await asyncio.sleep(backoff_s * (2**attempt))
                attempt += 1
                continue
            return result
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == retries:
                break
            await asyncio.sleep(backoff_s * (2**attempt))
            attempt += 1
    if last_result is not None:
        # Retries exhausted on a retryable status: hand the caller the real
        # response so it can classify the failure precisely.
        return last_result
    raise last_exc if last_exc else RuntimeError("request failed without an exception")


async def get_text_budgeted(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    wall_timeout_s: float,
    retries: int = 2,
    backoff_s: float = 0.8,
    limiter: PoliteGate | None = None,
    headers: dict[str, str] | None = None,
    chunk_size: int = 64 * 1024,
) -> tuple[int, str | None, str | None]:
    """GET ``url`` under a hard wall-clock budget, returning ``(status, text, err)``.

    The sibling of ``get_text_with_retries``, for callers that need three things
    it cannot offer:

    **A whole-operation deadline.** ``retries`` bounds the *number* of attempts
    but not the time they consume: a per-request timeout of 15s with two retries
    can still block an agent for 45s plus backoff. ``wall_timeout_s`` caps the
    total, so a tool call has a predictable worst case no matter how the failures
    arrange themselves.

    **Errors returned, not raised.** A connector that fans out over several hosts
    (WB probes its basket CDN mirrors in order) needs to inspect a failure and
    try the next candidate. Exceptions force that control flow into try/except
    around every call site; a classified ``err`` string keeps it as a value. The
    prefix is stable and meaningful: ``timeout:``, ``network:``, ``http_status:``,
    or a body-cap message.

    **Politeness preserved across retries.** The rate limiter is re-entered
    before each retry. Skipping it would burst a marketplace precisely when it
    has already signalled trouble — the surest way to earn an IP ban.

    HTTP statuses are never retried here, deliberately. Retrying a 429 deepens a
    rate limit, and no repeat request changes a 4xx. Gateway-status retries are
    ``get_text_with_retries``' business; this function hands every status straight
    back to the caller.
    """
    deadline = time.monotonic() + wall_timeout_s
    budget_exhausted = f"timeout: global {wall_timeout_s}s budget exhausted"
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0, None, budget_exhausted

        async def _drain(response: Any) -> tuple[int, str | None, str | None]:
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                total += len(chunk)
                if total > max_bytes:
                    return response.status_code, None, f"body exceeds {max_bytes} bytes"
                chunks.append(chunk)
            raw = b"".join(chunks)
            try:
                text = raw.decode(response.encoding or "utf-8", errors="replace")
            except (LookupError, TypeError):
                text = raw.decode("utf-8", errors="replace")
            return response.status_code, text, None

        async def _once() -> tuple[int, str | None, str | None]:
            # Two client shapes are supported on purpose. ``build_request``/``send``
            # is the path that carries per-request headers, which the plain
            # ``stream()`` context manager cannot. Callers with a client whose
            # headers are already set (and test doubles that only implement
            # ``stream``) still work through the second branch.
            if headers is not None and hasattr(client, "build_request"):
                request = client.build_request("GET", url, headers=headers)
                response = await client.send(request, stream=True)
                try:
                    return await _drain(response)
                finally:
                    await response.aclose()
            async with client.stream("GET", url) as response:
                return await _drain(response)

        try:
            return await asyncio.wait_for(_once(), timeout=remaining)
        except httpx.HTTPStatusError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", 0) or 0
            return status_code, None, f"http_status: {exc.__class__.__name__}: {exc}"
        except (TimeoutError, httpx.TransportError) as exc:
            last_exc = exc
            # A builtin/asyncio TimeoutError here is wait_for firing, i.e. the wall
            # clock ran out mid-attempt. httpx's own timeouts are TransportError
            # subclasses and stay retryable.
            if isinstance(exc, asyncio.TimeoutError):
                return 0, None, f"timeout: {exc.__class__.__name__}: {exc} (after {attempt + 1} attempt)"
            if attempt < retries:
                delay = backoff_s * (attempt + 1)
                if (deadline - time.monotonic()) <= delay:
                    return 0, None, budget_exhausted
                await asyncio.sleep(delay)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return 0, None, budget_exhausted
                if limiter is not None:
                    try:
                        await asyncio.wait_for(limiter.wait(), timeout=remaining)
                    except TimeoutError:
                        return 0, None, budget_exhausted
                continue
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
            return 0, None, f"{kind}: {exc.__class__.__name__}: {exc} (after {attempt + 1} attempts)"

    return 0, None, f"network: {last_exc}"


def build_client(
    *,
    timeout_s: float,
    connect_s: float = 5.0,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
    follow_redirects: bool = False,
    http2: bool = False,
) -> httpx.AsyncClient:
    """Construct an ``AsyncClient`` configured for marketplace catalog reads.

    ``follow_redirects`` defaults to False on purpose: several Russian
    marketplaces answer datacenter IPs with a self-referential 307 loop, and
    following it burns the request budget instead of surfacing the block.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=connect_s),
        headers=headers or {"User-Agent": DEFAULT_USER_AGENT},
        proxy=proxy,
        follow_redirects=follow_redirects,
        http2=http2,
    )
