"""MPStats MCP connector.

Sales/stock analytics for Ozon and Wildberries items via the MPStats browser-plugin
API. Unlike the anonymous catalog connectors in this workspace, this one needs a
paid MPStats account: authentication is a single JWT cookie, ``mp_auth``, read
from ``MPSTATS_MP_AUTH``. Without it the tools return ``auth_missing`` rather
than failing, so the server boots cleanly for any operator.

Endpoint (verified Jul 2026 against live data with a logged-in plugin session):
  - POST https://plugin.mpstats.io/pluginapi   (single JSON-RPC-style endpoint)

Request bodies are JSON objects; the method is the ``Request`` field (or inferred
from the presence of ``Sku``/``Place`` for the analytics call). Verified methods:

  - {"Request": "userInfo"}                       account/quota probe
  - {"Sku": <id>, "Place": "ozon"|"wildberries", "ozFBS": true}   per-SKU analytics
  - {"Request": "getWarehouses", "Place": ..., "Sku": [...]}     per-SKU stock split
  - {"Sku": [...], "Place": ..., "ozFBS": true}                   batch analytics (<=100)

Authentication model: the plugin endpoint authorises by the ``mp_auth`` cookie
alone — no Bearer, no other cookies, no origin-binding check observed in replay
(verified Jul 2026: a cold replay with only ``mp_auth`` answers 200 with data;
without it the body is ``{"code":403,"message":"Unauthorized"}`` behind HTTP 200).
So the connector sends exactly that one cookie. The token is a secret identifying
a paid, quota-billed account and must never be logged or committed.

Graphs are length ``days`` (default 30, oldest-first); a zero cell means "no data
for that day", never "the value was zero". A missing value stays ``None``, never
``0`` — a zero would rank a dead listing as the cheapest/top-selling item, which
is the exact bug class this connector's parsers exist to prevent.

NEVER use print() in stdio MCP — corrupts JSON-RPC. Use ctx.debug/info/error.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import time
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
from mcp_core.cache import TTLCache
from mcp_core.errors import (
    AuthMissingError,
    BadRequestError,
    NotFoundError,
    ParserDriftError,
    RateLimitedError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.pacing import Pacer
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport import proxy_from_env
from pydantic import Field

from mpstats_connector.models_output import (
    MetaOut,
    MpStatsItem,
    MpStatsItemResponse,
    MpStatsSelfCheckResponse,
    MpStatsStocks,
    MpStatsTotals,
    MpStatsWarehousesItem,
    MpStatsWarehousesResponse,
)
from mpstats_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "1.4.1"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

mcp = FastMCP(
    name="mpstats-connector",
    version=SERVER_VERSION,
)
mcp.add_middleware(RetryMiddleware(max_retries=3, base_delay=1.0))

# Verified live Jul 2026: the plugin endpoint answers from the chrome-extension
# origin, but a cold replay with only the mp_auth cookie works, so we do not
# forge a chrome-extension origin (that would be a lie about identity and adds
# nothing — the cookie is the real auth). A realistic browser UA is kept because
# shared unofficial endpoints sometimes gate on UA heuristics.
MP_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "pver": "4.254",
}
MP_TIMEOUT = httpx.Timeout(_settings.timeout, connect=5.0)
MP_WALL_TIMEOUT = _settings.wall_timeout
MAX_BODY_BYTES = _settings.max_body_bytes  # 2 MB hard cap — responses are small JSON
MPSTATS_HOST = "plugin.mpstats.io"
MPSTATS_URL = f"https://{MPSTATS_HOST}/pluginapi"

# Marketplaces MPStats analytics supports. Verified live Jul 2026: ozon returns
# per-day graphs; wildberries uses the same RPC shape (entry [6] in the source
# HAR is a wb utm metric, confirming wb is a first-class Place). ozFBS is an
# Ozon-specific flag (Fulfilled-by-Seller mode) — harmless to send for wb.
PLACES = frozenset({"ozon", "wildberries"})
# Batch analytics accepts up to 100 SKUs per call — the same ceiling WB v4 uses,
# and the source HAR's batch entry [7] sent 25 without complaint. Beyond 100 the
# endpoint has not been verified and the politeness argument against bulk also
# grows, so the cap is enforced client-side.
MAX_SKUS = 100
_SELFCHECK_SKU = 5107857210  # Ozon SKU verified live Jul 2026 to return analytics

_min_gap = _settings.min_gap
_pacer = Pacer(_min_gap)

# Transport-level retry for TRANSIENT network faults only (connect/read timeout,
# conn reset). Never retried on an HTTP status — a 429 or 5xx retry would worsen
# the rate limit. Mirrors the WB connector's bounded-retry contract.
_NET_RETRIES = _settings.net_retries
_NET_BACKOFF_S = _settings.net_backoff_s

# Cache the (status, text, err) triple keyed by the JSON request body. An agent
# conversation walks the same SKU repeatedly — item, then warehouses, then a
# comparison — and each is a fresh HTTP call without this. Only successful reads
# are cached; a failure is not (a transient timeout would keep failing the tool
# for the whole TTL even though the next attempt would succeed). MPSTATS_CACHE_TTL=0
# disables.
_cache: TTLCache[tuple[int, str | None, str | None]] = TTLCache(ttl_s=_settings.cache_ttl, max_entries=256)


def _proxy() -> str | None:
    """Resolve MPStats' proxy: explicit ``MPSTATS_PROXY`` first, then standard vars."""
    return (_settings.proxy.get_secret_value() or "").strip() or proxy_from_env("MPSTATS_PROXY")


def _client() -> httpx.AsyncClient:
    """Build the MPStats HTTP client.

    Redirects stay off (matching the runtime default): the plugin endpoint is a
    single fixed URL, and a redirect here would indicate a block or a move, not
    something to follow silently.
    """
    return httpx.AsyncClient(timeout=MP_TIMEOUT, headers=MP_HEADERS_BASE, proxy=_proxy())


async def _polite_wait() -> None:
    """Space this source's requests out, and back off if it refused us.

    The shared pacer rather than a hand-rolled sleep, for the half a hand-rolled
    one lacks: after a refusal it lengthens the gap instead of walking back into
    the same wall at the same speed. That matters more here than anywhere else in
    this repository — MPStats' offer makes a request rate above one per five
    seconds grounds for blocking the account, and a blocked account is not
    refunded, so the cost of hammering lands on the user, not on us.

    Reads ``_min_gap`` at call time so an operator or a test can retune the pace
    without rebuilding the pacer.
    """
    await _pacer.wait(min_gap=_min_gap)


def _cookie_header() -> str:
    """Build the Cookie header from the configured ``mp_auth`` JWT.

    Returns an empty string when no token is configured; callers gate on that and
    raise ``auth_missing`` before ever sending a request. The raw JWT is never
    returned in pieces or logged.
    """
    token = (_settings.mp_auth.get_secret_value() or "").strip()
    return f"mp_auth={token}" if token else ""


async def _post_json_budgeted(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    *,
    max_bytes: int,
    wall_timeout_s: float,
    retries: int,
    backoff_s: float,
) -> tuple[int, str | None, str | None]:
    """POST JSON ``body`` under a wall-clock budget, returning ``(status, text, err)``.

    The MPStats plugin API is POST-only, so the shared runtime's GET-only
    ``get_text_budgeted`` cannot serve it. This helper reproduces the same
    invariants locally rather than promoting a POST path into ``mcp-core`` (one
    connector needs POST; adding it to core would force every connector's test
    surface to consider it):

    * **Bounded bodies** — responses stream against ``max_bytes`` so a compromised
      or runaway upstream cannot exhaust memory.
    * **Wall-clock budget** — ``wall_timeout_s`` caps the whole operation across
      retries, so a tool call has a predictable worst case.
    * **Errors returned, not raised** — a classified ``err`` string (``timeout:``,
      ``network:``, ``http_status:``) keeps failure a value the caller inspects.
    * **Retry only what a retry can fix** — transport faults get bounded backoff;
      HTTP statuses (429, 4xx, 5xx) are returned immediately, never retried.
    * **Politeness preserved across retries** — the gate is re-entered before each
      retry, so a moment of trouble never becomes a burst.
    """
    deadline = time.monotonic() + wall_timeout_s
    budget_exhausted = f"timeout: global {wall_timeout_s}s budget exhausted"
    last_exc: Exception | None = None
    cookie = _cookie_header()
    # Per-request headers carry the cookie (a secret), so they are built fresh
    # per call and never captured in the module-level base headers.
    headers = {"Cookie": cookie} if cookie else {}

    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0, None, budget_exhausted

        async def _drain(response: httpx.Response) -> tuple[int, str | None, str | None]:
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
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
            request = client.build_request("POST", url, json=body, headers=headers)
            response = await client.send(request, stream=True)
            try:
                return await _drain(response)
            finally:
                await response.aclose()

        try:
            return await asyncio.wait_for(_once(), timeout=remaining)
        except httpx.HTTPStatusError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", 0) or 0
            return status_code, None, f"http_status: {exc.__class__.__name__}: {exc}"
        except (TimeoutError, httpx.TransportError) as exc:
            last_exc = exc
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
                # Re-enter the polite gate before retrying — bursting a shared,
                # quota-billed endpoint when it has already signalled trouble is
                # the fastest way to burn quota or earn a block.
                try:
                    await asyncio.wait_for(_polite_wait(), timeout=remaining)
                except TimeoutError:
                    return 0, None, budget_exhausted
                continue
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
            return 0, None, f"{kind}: {exc.__class__.__name__}: {exc} (after {attempt + 1} attempts)"

    return 0, None, f"network: {last_exc}"


async def _call(body: dict[str, Any], *, label: str) -> dict[str, Any]:
    """Send one plugin RPC, enforce auth, classify errors, return parsed JSON.

    Centralises the contract every tool shares: auth gate, polite wait, body cap,
    status handling, and the MPStats-specific ``code`` envelope. The upstream wraps
    its real verdict in ``{"code": 200, ...}`` (or ``{"code": 403, "message": ...}``
    for an auth failure) behind HTTP 200, so HTTP status alone is not the verdict —
    the inner ``code`` is.

    Raises ``ToolError`` (via ``raise_tool_error``) on any failure, so a tool body
    never has to repeat this ladder.
    """
    if not _cookie_header():
        log_event(f"{label}.auth_missing")
        raise_tool_error(AuthMissingError(provider="mpstats"))

    cache_key = json.dumps(body, sort_keys=True, ensure_ascii=False)
    cached = _cache.get(cache_key)
    was_cached = cached is not None
    if cached is not None:
        status_code, text, err = cached
    else:
        await _polite_wait()
        try:
            async with _client() as client:
                status_code, text, err = await _post_json_budgeted(
                    client,
                    MPSTATS_URL,
                    body,
                    max_bytes=MAX_BODY_BYTES,
                    wall_timeout_s=MP_WALL_TIMEOUT,
                    retries=_NET_RETRIES,
                    backoff_s=_NET_BACKOFF_S,
                )
        except httpx.HTTPError as exc:
            # Redact both channels, not just the log. A malformed token — a
            # trailing newline off a copy-paste is enough — makes httpx put the
            # whole Cookie header into the exception text, and this text travels
            # to the model and into the client's transcript. Every other
            # connector redacts here; this is the only one holding a secret.
            log_event(f"{label}.network_error", error=_redact(str(exc)), exc_type=type(exc).__name__)
            raise_tool_error(TransportDownError(_redact(str(exc)), provider="mpstats"))

        if err:
            log_event(f"{label}.network_error", error=_redact(err))
            raise_tool_error(TransportDownError(_redact(err), provider="mpstats"))
        if status_code == 429:
            _pacer.record_refusal()
            log_event(f"{label}.rate_limited")
            raise_tool_error(RateLimitedError("mpstats", retry_after_s=30.0))
        if text and "<html" in text[:200].lower():
            _pacer.record_refusal()
            log_event(f"{label}.blocked", reason="html_response")
            raise_tool_error(TransportDownError("HTML page instead of JSON (likely a block)", provider="mpstats"))
        if status_code != 200:
            log_event(f"{label}.http_error", status=status_code)
            raise_tool_error(
                TransportDownError(f"{label} HTTP {status_code}", provider="mpstats", status_code=status_code)
            )

    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as exc:
        log_event(f"{label}.parse_error", error=_redact(str(exc)))
        raise_tool_error(ParserDriftError(str(exc), provider="mpstats"))

    if not isinstance(data, dict):
        log_event(f"{label}.shape_error", reason="not_object")
        raise_tool_error(ParserDriftError(f"{label} expected object, got {type(data).__name__}", provider="mpstats"))

    # Inner verdict: MPStats returns {"code": 403, "message": "Unauthorized"} for
    # an invalid/expired token, BEHIND HTTP 200. Treating HTTP 200 as success here
    # would silently surface auth failure as "no data". Verified live Jul 2026.
    inner_code = data.get("code")
    if inner_code == 403:
        _pacer.record_refusal()
        log_event(f"{label}.auth_denied")
        raise_tool_error(AuthMissingError("mp_auth token rejected (code 403)", provider="mpstats"))
    if inner_code is not None and inner_code != 200:
        log_event(f"{label}.inner_error", code=inner_code, message=_redact(str(data.get("message", ""))))
        raise_tool_error(
            TransportDownError(f"mpstats code {inner_code}: {data.get('message', '')}", provider="mpstats")
        )

    # Cache only now, with the inner verdict known good. Caching on HTTP 200
    # alone stored the failures too: MPStats answers `{"code": 403}` and its
    # 5xx equivalents behind HTTP 200, so a rejected token or a momentary
    # upstream fault was replayed from cache for the whole TTL — the exact
    # behaviour the comment on `_cache` promises this does not have.
    if not was_cached:
        # Only a call that reached the wire and came back good clears the
        # backoff; a cache hit proves nothing about the upstream's current mood.
        _pacer.record_success()
    if not was_cached and text is not None:
        _cache.set(cache_key, (status_code, text, None))
    return data


def _last_nonzero(graph: list[Any]) -> float | None:
    """Return the last non-zero cell of a numeric graph, else None.

    The graphs are oldest-first and the final cell is "today", so the last
    non-zero value is the current reading. An all-zero/empty graph returns
    ``None`` — never ``0.0`` — so a delisted item reports no price/stock instead
    of a false zero that would rank it as the cheapest.
    """
    if not isinstance(graph, list) or not graph:
        return None
    for cell in reversed(graph):
        n = R.coerce_price(cell)
        if n is not None and n > 0:
            return n
    return None


def _int_graph(graph: Any) -> list[int]:
    """Coerce a graph list to ints, dropping cells that are not clean integers.

    A non-int cell (a drifted string, a float, null) becomes 0 — the parser
    keeps the window length aligned with ``days`` rather than truncating, but a
    failed coercion is visible as a warning from the caller. ``coerce_int``
    already rejects ambiguous forms (signed, decimal, magnitude suffix).
    """
    if not isinstance(graph, list):
        return []
    out: list[int] = []
    for cell in graph:
        n = R.coerce_int(cell)
        out.append(n if n is not None else 0)
    return out


def _parse_item_entry(raw: Any, place: str) -> MpStatsItem | None:
    """Flatten one MPStats analytics entry into the typed item model.

    Accepts the dict that sits under ``items[<sku>]`` in the analytics response.
    Returns ``None`` for a non-dict entry so a single drifted row never crashes
    the whole tool — the caller counts and warns instead.
    """
    if not isinstance(raw, dict):
        return None
    totals_raw = raw.get("Totals")
    totals = MpStatsTotals(
        orders=R.coerce_int(totals_raw.get("orders")) if isinstance(totals_raw, dict) else None,
        sum=R.coerce_price(totals_raw.get("sum")) if isinstance(totals_raw, dict) else None,
        sum_prev=R.coerce_price(totals_raw.get("sumPrev")) if isinstance(totals_raw, dict) else None,
    )
    count_graph = _int_graph(raw.get("countGraph"))
    prices_graph = _int_graph(raw.get("pricesGraph"))
    stock_now_raw = _last_nonzero(count_graph)
    stock_now = R.coerce_int(stock_now_raw) if stock_now_raw is not None else None
    # Stock is an integer count, not a price; _last_nonzero returns a float, so
    # coerce back through coerce_int for a clean int (or None if the cell was 0).
    # Deliberate asymmetry with price: an all-zero count graph means the item is
    # genuinely out of stock right now (a real 0 reading), while an all-zero
    # price graph stays None — a zero price is never a real reading. An empty
    # graph stays None for both ("no data").
    if stock_now is None and count_graph:
        stock_now = 0
    return MpStatsItem(
        sku=R.coerce_int(raw.get("Sku")),
        place=place,
        seller=str(raw.get("Seller") or ""),
        seller_id=R.coerce_int(raw.get("SellerId")),
        brand=str(raw.get("Brand") or ""),
        stock_now=stock_now,
        price_avg_rub=_last_nonzero(prices_graph),
        orders_per_day=R.coerce_price(raw.get("OrdersPerDay")),
        days_on_stocks=R.coerce_int(raw.get("DaysOnStocks")),
        totals=totals,
        orders_graph=_int_graph(raw.get("ordersGraph")),
        prices_graph=prices_graph,
        count_graph=count_graph,
        rubrics_graph=_int_graph(raw.get("rubricsGraph")),
    )


def _parse_warehouses_entry(raw: Any) -> MpStatsWarehousesItem | None:
    """Flatten one MPStats warehouse entry into the typed stock model.

    Accepts the dict under ``data[<sku>]``. ``fbo`` is a list of per-warehouse
    entries in the upstream; we total it for the agent view and keep the raw list.
    Returns ``None`` for a non-dict entry so a single drifted row never crashes
    the whole tool.
    """
    if not isinstance(raw, dict):
        return None
    stocks_raw = raw.get("stocks")
    fbs: int | None = None
    fbo: int | None = None
    fbo_warehouses: list[Any] = []
    if isinstance(stocks_raw, dict):
        fbs = R.coerce_int(stocks_raw.get("fbs"))
        fbo_raw = stocks_raw.get("fbo")
        if isinstance(fbo_raw, list):
            fbo_warehouses = fbo_raw
            total = 0
            for entry in fbo_raw:
                # Each FBO entry is typically {"<warehouse>": <qty>} or a bare int;
                # tolerate both and sum the integer values found.
                if isinstance(entry, dict):
                    for v in entry.values():
                        n = R.coerce_int(v)
                        if n is not None:
                            total += n
                else:
                    n = R.coerce_int(entry)
                    if n is not None:
                        total += n
            fbo = total if total > 0 else None
        elif isinstance(fbo_raw, (int, float)):
            fbo = R.coerce_int(fbo_raw)
    return MpStatsWarehousesItem(
        sku=R.coerce_int(raw.get("Sku")) if "Sku" in raw else None,
        stocks=MpStatsStocks(
            fbs=fbs,
            fbo=fbo,
            fbo_warehouses=fbo_warehouses,
            last_update=str(raw.get("last_update") or "") or None,
        ),
    )


def _validate_skus(skus: list[int], label: str) -> None:
    """Shape-validate a SKU list: non-empty, <=MAX_SKUS, all positive ints.

    Validation by shape, not escape — the SKUs land in a JSON body, but rejecting
    malformed values up front is easier to verify than reasoning about them
    downstream. ``bool`` is rejected explicitly because ``isinstance(True, int)``
    is True in Python.
    """
    if not skus:
        raise_tool_error(BadRequestError(f"{label}: skus empty"))
    if len(skus) > MAX_SKUS:
        raise_tool_error(BadRequestError(f"{label}: max {MAX_SKUS} skus, got {len(skus)}"))
    if any(isinstance(s, bool) or not isinstance(s, int) or s <= 0 for s in skus):
        raise_tool_error(BadRequestError(f"{label}: skus must be positive integers"))


@mcp.tool(
    name="mpstats_item",
    annotations=ToolAnnotations(
        title="MPStats Item Analytics",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def mpstats_item(
    skus: Annotated[
        list[int],
        Field(description="1..100 SKU integers (positive). Per-SKU 30-day sales/price/stock analytics from MPStats."),
    ],
    place: Annotated[
        str,
        Field(
            description="Marketplace: 'ozon' or 'wildberries'. Determines which MPStats dataset the SKUs resolve against.",
        ),
    ],
    oz_fbs: Annotated[
        bool,
        Field(
            default=True,
            description="Ozon FBS (Fulfilled-by-Seller) mode. Ozon-specific; harmless for wildberries. Default true.",
        ),
    ] = True,
    ctx: Context | None = None,
) -> MpStatsItemResponse:
    """Fetch per-SKU 30-day sales analytics from MPStats (Ozon or Wildberries).

    Returns, per SKU: seller/brand identity, current stock and price, a rolling
    orders-per-day average, aggregated totals over the window, and four
    per-day graphs (orders, prices, stock count, rubric positions). Graphs are
    length ``days`` (default 30), oldest-first; a zero cell means "no data for
    that day", not "the value was zero".

    Requires the ``MPSTATS_MP_AUTH`` env var (a paid MPStats account JWT cookie).
    Without it the tool returns an ``auth_missing`` error.

    ## Return Format

    MpStatsItemResponse: {place, days, count, items, meta}. Each item carries
    sku, place, seller, seller_id, brand, stock_now, price_avg_rub,
    orders_per_day, days_on_stocks, totals {orders, sum, sum_prev} and four
    per-day graphs (orders, prices, count, rubrics), oldest-first. Missing
    values are None, never 0; a zero graph cell means "no data for that day".

    ## Error Format

    ToolError: BadRequestError on malformed skus or place; AuthMissingError
    when MPSTATS_MP_AUTH is missing or rejected; RateLimitedError on HTTP
    429; TransportDownError on network failures, non-200 responses and HTML
    blocks; ParserDriftError on a non-JSON or mis-shaped body; NotFoundError
    when no requested SKU has analytics.

    Args:
        skus: 1..100 positive SKU integers.
        place: 'ozon' or 'wildberries'.
        oz_fbs: Ozon FBS mode flag (default true).
    """
    log_event("mpstats_item.start", sku_count=len(skus), place=place)
    if ctx is not None:
        await ctx.info(f"mpstats_item: {len(skus)} skus place={place}")

    _validate_skus(skus, "mpstats_item")
    if place not in PLACES:
        raise_tool_error(BadRequestError(f"mpstats_item: place must be one of {sorted(PLACES)}"))

    body: dict[str, Any] = {"Place": place, "ozFBS": oz_fbs}
    if len(skus) == 1:
        body["Sku"] = skus[0]
    else:
        body["Sku"] = skus

    data = await _call(body, label="mpstats_item")
    days = R.coerce_int(data.get("days")) or 30
    items_raw = data.get("items")
    if not isinstance(items_raw, dict):
        log_event("mpstats_item.shape_error", reason="items_not_object")
        raise_tool_error(
            ParserDriftError(
                f"mpstats_item: 'items' expected object, got {type(items_raw).__name__}", provider="mpstats"
            )
        )

    items: list[MpStatsItem] = []
    warnings: list[str] = []
    # Preserve the requested SKU order: the upstream keys items by SKU id, so we
    # walk the request list, not the dict, to keep a deterministic agent-facing
    # ordering regardless of upstream key order.
    items_by_key: dict[str, Any] = {str(k): v for k, v in items_raw.items()}
    for sku in skus:
        raw = items_by_key.get(str(sku))
        if raw is None:
            warnings.append(f"sku {sku}: no analytics returned")
            continue
        parsed = _parse_item_entry(raw, place)
        if parsed is None:
            warnings.append(f"sku {sku}: entry not an object")
            continue
        items.append(parsed)

    if not items:
        log_event("mpstats_item.no_results", place=place)
        raise_tool_error(NotFoundError(f"mpstats_item: no analytics for any of {len(skus)} sku(s)", provider="mpstats"))

    log_event("mpstats_item.done", items=len(items), warnings=len(warnings))
    return MpStatsItemResponse(
        place=place,
        days=days,
        count=len(items),
        items=items,
        meta=MetaOut(source="mpstats_item", healthy=not warnings, warnings=warnings),
    )


@mcp.tool(
    name="mpstats_warehouses",
    annotations=ToolAnnotations(
        title="MPStats Warehouse Stock",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def mpstats_warehouses(
    skus: Annotated[
        list[int],
        Field(description="1..100 SKU integers (positive). Per-SKU warehouse stock split from MPStats."),
    ],
    place: Annotated[
        str,
        Field(
            description="Marketplace: 'ozon' or 'wildberries'. Determines which MPStats dataset the SKUs resolve against.",
        ),
    ],
    ctx: Context | None = None,
) -> MpStatsWarehousesResponse:
    """Fetch per-SKU warehouse stock split from MPStats (Ozon or Wildberries).

    Returns, per SKU: FBS (seller warehouse) stock count, total FBO (marketplace
    warehouse) stock count, the raw per-warehouse FBO entries when MPStats
    populates them, and the upstream ``last_update`` timestamp.

    Requires the ``MPSTATS_MP_AUTH`` env var (a paid MPStats account JWT cookie).
    Without it the tool returns an ``auth_missing`` error.

    ## Return Format

    MpStatsWarehousesResponse: {place, days, count, items, meta}. Each item
    carries sku and stocks {fbs, fbo, fbo_warehouses, last_update}. Missing
    stock counts are None, never 0.

    ## Error Format

    ToolError: BadRequestError on malformed skus or place; AuthMissingError
    when MPSTATS_MP_AUTH is missing or rejected; RateLimitedError on HTTP
    429; TransportDownError on network failures, non-200 responses and HTML
    blocks; ParserDriftError on a non-JSON or mis-shaped body; NotFoundError
    when no requested SKU has stock data.

    Args:
        skus: 1..100 positive SKU integers.
        place: 'ozon' or 'wildberries'.
    """
    log_event("mpstats_warehouses.start", sku_count=len(skus), place=place)
    if ctx is not None:
        await ctx.info(f"mpstats_warehouses: {len(skus)} skus place={place}")

    _validate_skus(skus, "mpstats_warehouses")
    if place not in PLACES:
        raise_tool_error(BadRequestError(f"mpstats_warehouses: place must be one of {sorted(PLACES)}"))

    body = {"Request": "getWarehouses", "Place": place, "Sku": skus}
    data = await _call(body, label="mpstats_warehouses")
    days = R.coerce_int(data.get("days")) or 30
    data_raw = data.get("data")
    if not isinstance(data_raw, dict):
        log_event("mpstats_warehouses.shape_error", reason="data_not_object")
        raise_tool_error(
            ParserDriftError(
                f"mpstats_warehouses: 'data' expected object, got {type(data_raw).__name__}", provider="mpstats"
            )
        )

    items: list[MpStatsWarehousesItem] = []
    warnings: list[str] = []
    data_by_key: dict[str, Any] = {str(k): v for k, v in data_raw.items()}
    for sku in skus:
        raw = data_by_key.get(str(sku))
        if raw is None:
            warnings.append(f"sku {sku}: no stock returned")
            continue
        parsed = _parse_warehouses_entry(raw)
        if parsed is None:
            warnings.append(f"sku {sku}: entry not an object")
            continue
        # The warehouse endpoint does not echo the SKU in each entry, so stamp
        # it from the request to keep the agent-facing row self-describing.
        if parsed.sku is None:
            parsed.sku = sku
        items.append(parsed)

    if not items:
        log_event("mpstats_warehouses.no_results", place=place)
        raise_tool_error(
            NotFoundError(f"mpstats_warehouses: no stock for any of {len(skus)} sku(s)", provider="mpstats")
        )

    log_event("mpstats_warehouses.done", items=len(items), warnings=len(warnings))
    return MpStatsWarehousesResponse(
        place=place,
        days=days,
        count=len(items),
        items=items,
        meta=MetaOut(source="mpstats_warehouses", healthy=not warnings, warnings=warnings),
    )


async def _finalize_selfcheck(checks: dict[str, dict[str, Any]]) -> MpStatsSelfCheckResponse:
    """Aggregate the per-check dict entries into the typed selfcheck response.

    Mirrors the WB connector: sub-checks are kept as plain dicts (built by
    ``R.selfcheck_entry``) so ``selfcheck_result`` can aggregate them, then the
    result dict is validated into the typed response. ``config_loaded`` and
    ``tool_count`` are surfaced the same way WB does, because the first question
    about a drift report is always which build and how many tools it registered.
    """
    try:
        config_loaded = bool(get_settings())
    except Exception:
        config_loaded = False
    try:
        tool_count = len(await mcp.list_tools())
    except Exception:
        tool_count = 0
    result = R.selfcheck_result(
        "mpstats",
        checks,
        required=["item"],
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=os.getpid(),
    )
    result["config_loaded"] = config_loaded
    result["tool_count"] = tool_count
    return MpStatsSelfCheckResponse.model_validate(result)


@mcp.tool(
    name="mpstats_selfcheck",
    annotations=ToolAnnotations(
        title="MPStats Self-Check",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def mpstats_selfcheck(ctx: Context | None = None) -> MpStatsSelfCheckResponse:
    """Health canary for the MPStats connector.

    Tri-state, matching the shared contract:

    - ``success`` — the item analytics family answered in the expected shape.
    - ``drift_detected`` — reachable and authorised, but unparseable: a code
      change is needed.
    - ``inconclusive`` — transport failure, missing auth, or an auth denial:
      says nothing about the parsers, so it never reads as drift.

    The probe uses a live Ozon SKU (verified Jul 2026) rather than a hardcoded
    short-lived fixture, so a delisted baseline never reads as parser drift.

    ## Return Format

    MpStatsSelfCheckResponse: {status, healthy, connector, checks,
    server_version, server_started_at, process_id, config_loaded,
    tool_count} — checks maps auth / item to a per-subcheck verdict
    (healthy/drift/inconclusive). drift_detected and inconclusive are NOT
    errors; they are valid canary verdicts returned as a normal response.

    ## Error Format

    Never raises ToolError: missing or rejected auth and transport failures
    map to inconclusive entries, a reachable-but-unparseable analytics shape
    to a drift entry.
    """
    log_event("mpstats_selfcheck.start")
    checks: dict[str, dict[str, Any]] = {}

    if not _cookie_header():
        checks["auth"] = R.selfcheck_entry(
            "inconclusive",
            baseline="mp_auth",
            reason="auth_missing",
            notes=["MPSTATS_MP_AUTH not set — cannot reach the data plane"],
        )
        return await _finalize_selfcheck(checks)

    try:
        data = await _call(
            {"Sku": _SELFCHECK_SKU, "Place": "ozon", "ozFBS": True},
            label="mpstats_selfcheck",
        )
    except ToolError as exc:
        # An auth denial or transport failure is inconclusive, not drift — the
        # canary could not reach a verdict about the parsers.
        checks["item"] = R.selfcheck_entry(
            "inconclusive",
            baseline=str(_SELFCHECK_SKU),
            reason="transport_or_auth",
            notes=[_redact(str(exc))],
        )
        return await _finalize_selfcheck(checks)

    items_raw = data.get("items")
    days = R.coerce_int(data.get("days"))
    if not isinstance(items_raw, dict) or days is None:
        checks["item"] = R.selfcheck_entry(
            "drift",
            baseline=str(_SELFCHECK_SKU),
            reason="shape_changed",
            notes=[f"items={type(items_raw).__name__}, days={days!r}"],
        )
    else:
        entry = items_raw.get(str(_SELFCHECK_SKU))
        if not isinstance(entry, dict):
            checks["item"] = R.selfcheck_entry(
                "drift",
                baseline=str(_SELFCHECK_SKU),
                reason="sku_entry_missing",
                notes=[f"items keys={list(items_raw.keys())[:5]}"],
            )
        else:
            # Anchor on the fields the parser reads: a renamed key here is the
            # loud alarm, not a silent zero. Verified Jul 2026 against live data.
            expected = {"Sku", "Totals", "ordersGraph", "pricesGraph", "countGraph"}
            present = {k for k in expected if k in entry}
            missing = expected - present
            if missing:
                checks["item"] = R.selfcheck_entry(
                    "drift",
                    baseline=str(_SELFCHECK_SKU),
                    reason="anchors_missing",
                    notes=[f"missing={sorted(missing)}", f"days={days}"],
                )
            else:
                checks["item"] = R.selfcheck_entry(
                    "healthy",
                    baseline=str(_SELFCHECK_SKU),
                    notes=[f"days={days}", f"orders_graph_len={len(entry.get('ordersGraph') or [])}"],
                )

    return await _finalize_selfcheck(checks)
