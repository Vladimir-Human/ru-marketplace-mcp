"""The connector assembly the bricks in mcp-core were missing.

mcp-core already carried the bricks — ``pacing``, ``errors``, ``cache``,
``dom``, ``resilience``, ``transport.chrome_cdp`` — but every connector still
assembled them the same way by hand, and the assembly is what got copied:
``_polite_wait`` was byte-identical in nine connectors, ``_blocked_error``'s
tail in three, ``_sync_curl_get``'s streaming body-cap loop in two, and the
render-one-page-and-parse-JSON skeleton in every CDP connector. A bug fixed in
one copy stayed in the others; the audit of 2026-11-01 measured the cost.

This module is that assembly, once. It deliberately holds functions and not a
``SourceKit`` class: a connector's per-source state (``_cache``, ``_pacer``,
``_cdp_lock``, ``_min_gap``) is module-level in its own server and several
tests patch those names, so the honest shared part is the mechanism, not the
ownership. Each connector keeps its own thin wrapper with the same module-level
name it always had — the names the tests patch — and the wrapper body is the
call into here.

Two imports are deliberately not this module's. ``chrome_cdp`` (Playwright)
belongs to mcp-core's ``cdp`` extra and is imported inside :func:`render_json`,
so a connector that only needs :func:`polite_wait` never pays for it; curl_cffi
is not imported here at all, because the connectors that fetch that way already
have it and pass their own ``get`` into :func:`sync_curl_get`, which also keeps
a connector test that patches its module's ``cffi.get`` on the seam it knows.
"""
# START_MODULE_CONTRACT
# PURPOSE: Provide the shared connector assembly — pacing, refusal errors, capped reads and paced CDP extraction.
# SCOPE: Mechanism only; per-source state stays in each connector, curl_cffi arrives as an argument and chrome_cdp imports lazily.
# DEPENDS: mcp_core.pacing,errors; stdlib asyncio,json,re,urllib.parse; the caller's curl_cffi get; M-CHROME-CDP inside render_json.
# LINKS: M-KIT; M-PACING; M-ERRORS; M-CHROME-CDP; R-TRANSPORT; R-INPUT-TRUST.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# polite_wait: Enforce this source's pacing gap, reading the connector's live min_gap.
# blocked_error: Build the refusal error, with the operator's rotation hint and a bounded detail.
# host_checked_id: Extract an id from a URL only when its host is ours; bare ids pass.
# sync_curl_get: Tier-1 GET whose streamed body is capped and decoded.
# render_json: Open a page over CDP under the connector's lock, run an extractor, parse its JSON.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp_core.errors import ParserDriftError, TransportDownError, raise_tool_error
from mcp_core.pacing import Pacer

__all__ = [
    "blocked_error",
    "host_checked_id",
    "polite_wait",
    "render_json",
    "sync_curl_get",
]


async def polite_wait(pacer: Pacer, min_gap: float) -> None:
    """Space this source's requests out, and back off if it refused us.

    ``min_gap`` is passed by the caller rather than read from ``pacer`` so a
    connector keeps honouring its own module-level value: an operator or a test
    retunes the pace at runtime by patching that name, and the next wait picks
    it up.
    """
    await pacer.wait(min_gap=min_gap)


def blocked_error(message: str, *, pacer: Pacer, detail: str = "", status_code: int = 403) -> TransportDownError:
    """The connector's refusal error, with the pacer's standing-block hint.

    ``message`` is the source's own wording — what was refused and what the
    operator does about it. The two things every connector adds are here: the
    rotation hint once refusals stop looking like bad luck, and the upstream
    detail, bounded so a wall's HTML cannot become the error.
    """
    hint = pacer.rotation_hint()
    if hint:
        message += f" {hint}"
    if detail:
        message += f" Detail: {detail[:160]}"
    return TransportDownError(message, status_code=status_code)


def host_checked_id(
    raw: str,
    *,
    hosts: tuple[str, ...],
    path_re: re.Pattern[str],
    bare_re: re.Pattern[str],
) -> str | None:
    """Pull a product id out of a URL on one of ``hosts``, or from a bare id.

    Host-checked on purpose. The id is the only thing that survives this
    function and the caller rebuilds the navigation URL from its own base, so a
    caller cannot smuggle ``/product/<id>`` into a path on someone else's
    domain and steer the operator's logged-in Chrome there. Anything outside
    ``hosts``, or carrying a scheme we do not navigate, returns None. A
    scheme-relative value is off-host by definition.
    """
    raw = raw.strip()
    if not raw:
        return None
    if bare_re.fullmatch(raw):
        return raw.lower()
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme:
        if parts.scheme not in ("http", "https"):
            return None
        host = (parts.hostname or "").rstrip(".").lower()
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in hosts):
            return None
        raw = parts.path
    elif raw.startswith("//"):
        return None
    match = path_re.search(raw)
    return match.group(1).lower() if match else None


def sync_curl_get(
    url: str,
    *,
    get: Callable[..., Any],
    headers: Mapping[str, str],
    impersonate: Any,
    timeout: float,
    max_body_bytes: int,
    proxy: str | None = None,
) -> tuple[int, str]:
    """Tier-1 GET with an incremental body cap.

    Streams the response and aborts mid-download once ``max_body_bytes`` is
    exceeded, rather than buffering a body we would then have to reject: an
    inflated response must not be able to OOM the connector. The proxy arrives
    as an argument, not from the environment, because a caller may run this in a
    subprocess whose environment is deliberately reduced to an allowlist.

    ``get`` is the caller's own curl_cffi entry point, passed in rather than
    imported here: curl_cffi is an extra of the connectors that fetch this way,
    not of mcp-core, and a connector test that patches ``cffi.get`` on its own
    module keeps hitting the seam it always did.
    """
    kwargs: dict[str, Any] = {}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    r = get(url, headers=headers, impersonate=impersonate, timeout=timeout, stream=True, **kwargs)
    try:
        encoding = r.encoding or "utf-8"
        chunks: list[bytes] = []
        total = 0
        for chunk in r.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > max_body_bytes:
                raise ValueError(f"body exceeds {max_body_bytes} bytes (aborted at {total} during stream)")
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            text = body.decode(encoding, errors="replace")
        except (LookupError, TypeError):
            text = body.decode("utf-8", errors="replace")
        return r.status_code, text
    finally:
        try:
            r.close()
        except Exception:
            pass


async def render_json(
    url: str,
    extract_js: str,
    wait_ms: int,
    *,
    lock: asyncio.Lock,
    wait: Callable[[], Awaitable[None]],
    max_body_bytes: int,
    evaluate_timeout: float = 30.0,
) -> dict[str, Any]:
    """Open ``url`` in the operator's Chrome, let the page settle, extract.

    Serialized through ``lock`` so a burst of tool calls cannot spray tabs, and
    paced through ``wait`` before navigating. ``wait_ms`` is how long the page
    gets to render — the first navigation of a challenge-walled source pays a
    proof-of-work there, later reads on the same session are fast. The extractor
    returns JSON text; a missing, oversized or non-object payload is a loud
    error rather than an empty result.
    """
    from mcp_core.transport.chrome_cdp import open_page

    async with lock:
        await wait()
        async with open_page(url, wait_ms=wait_ms) as page:
            raw = await asyncio.wait_for(page.evaluate(extract_js), timeout=evaluate_timeout)
    if not isinstance(raw, str) or len(raw.encode()) > max_body_bytes:
        raise_tool_error(TransportDownError("extracted page data missing or over the body cap"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # The extractor returns JSON.stringify output, so text that is not JSON
        # means the extractor itself broke: parser drift, as AliExpress reports
        # the same case, not a bare JSONDecodeError (R-ERRORS).
        raise_tool_error(ParserDriftError("page extractor returned non-JSON data"))
    if not isinstance(data, dict):
        raise_tool_error(ParserDriftError("page extractor returned a non-object payload"))
    return data
