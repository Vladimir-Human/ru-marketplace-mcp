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
