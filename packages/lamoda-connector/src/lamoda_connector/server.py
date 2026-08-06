"""Lamoda MCP connector.

Lamoda's anti-bot wall splits the catalog in two. One channel answers plain
anonymous HTTPS: the GraphQL product endpoint, which enriches a SKU you already
have with real prices, brands, sizes and availability. Everything that would
let you *find* a SKU — search, catalog, HTML pages — sits behind the same
self-referential 307 redirect loop as Ozon, so discovery runs in the operator's
Chrome over CDP.

Verified live July 2026 from a datacenter IP (docs/ANTI_BOT.md):
  - ``POST /goapi/v2/catalog/graphql/products/`` — 200, real JSON (tier 1)
  - catalog/search GET paths — 307 loop; HTML — 403; mobile API — 403
  - ``rating`` is not in the GraphQL schema (introspection disabled), so this
    connector reports no review data — Lamoda hides it entirely

Two tiers, tried in cost order: GraphQL first for cards, CDP for search. The
CDP route needs verification from the operator's machine for the same reason
as Megamarket — run lamoda_selfcheck from your Chrome first.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import urllib.parse
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
from mcp_core.cache import TTLCache
from mcp_core.dom import JS_HELPERS, prices_from_tile, title_from_tile
from mcp_core.errors import (
    BadRequestError,
    NotFoundError,
    ParserDriftError,
    ToolError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.pacing import Pacer
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport import build_client
from mcp_core.transport.chrome_cdp import NavBlocked, open_page
from pydantic import Field

from lamoda_connector.models_output import (
    LamodaCardResponse,
    LamodaSearchItemOut,
    LamodaSearchResponse,
    LamodaSelfcheckResponse,
    LamodaSizeOut,
    MetaOut,
)
from lamoda_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "1.3.1"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://www.lamoda.ru"
GRAPHQL_URL = f"{SITE_BASE}/goapi/v2/catalog/graphql/products/"

TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes
_min_gap = _settings.min_gap

# SKUs look like MP002XM1RMM3: two letters, alphanumerics, 8-20 chars. URLs
# carry them lowercased, so match case-insensitively and normalise to upper.
_SKU_RE = re.compile(r"\b([a-zA-Z]{2}[a-zA-Z0-9]{6,18})\b")

# Field names verified against the live endpoint, July 2026. `old_price_amount`
# is the real name: asking for `old_price` answers HTTP 200 with
# {"error": "Internal server error", "code": -32603} and no data at all.
_GRAPHQL_QUERY = (
    "query { products(skus: [%s]) { sku name brand_name price_amount old_price_amount "
    "is_available is_sellable stock_remains sizes { size is_available stock_remains } } }"
)

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

mcp = FastMCP(
    name="lamoda-connector",
    version=SERVER_VERSION,
    instructions=(
        "Lamoda fashion catalog: text search runs in the operator's Chrome over "
        "CDP (discovery is behind a redirect loop); product cards come from the "
        "anonymous GraphQL endpoint and work tier 1. Lamoda exposes no ratings. "
        "Start with lamoda_search; lamoda_card takes a SKU or product URL."
    ),
)
mcp.add_middleware(RetryMiddleware())

_cache: TTLCache = TTLCache(ttl_s=_settings.cache_ttl, max_entries=128)
_pacer = Pacer(_min_gap)
_cdp_lock = asyncio.Lock()


def _proxy() -> str | None:
    return _settings.proxy.get_secret_value() or None


async def _polite_wait() -> None:
    """Space this source's requests out, and back off if it refused us.

    Reads ``_min_gap`` at call time so an operator or a test can retune the
    pace without rebuilding the pacer.
    """
    await _pacer.wait(min_gap=_min_gap)


def _extract_sku(raw: str) -> str | None:
    """Pull a SKU out of a lamoda.ru URL or a bare SKU string.

    URLs carry the SKU lowercased (``/p/mp002xm1rmm3/``); the GraphQL endpoint
    expects the canonical uppercase form.
    """
    raw = raw.strip()
    match = _SKU_RE.search(raw)
    return match.group(1).upper() if match else None


_SEARCH_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    const ID_RE = /\\/p\\/([a-zA-Z]{2}[a-zA-Z0-9]{6,18})/i;
    const out = [];
    const anchors = document.querySelectorAll('a[href*="/p/"]');
    const seen = new Set();
    for (const a of anchors) {
        const href = a.href || '';
        const m = href.match(ID_RE);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);
        // tileRootFor, not closest(): closest() tests the anchor itself first, so
        // an image/overlay link whose own class contains "card" or "product"
        // becomes the tile and reads as empty. See mcp_core.dom.
        const card = tileRootFor(a, ID_RE);
        let titleEl = a;
        if (!cleanText(titleEl)) {
            for (const cand of card.querySelectorAll('a[href*="/p/"], [class*="name"], [class*="title"], [class*="brand"]')) {
                if (cleanText(cand)) { titleEl = cand; break; }
            }
        }
        const title = cleanText(titleEl) || (a.getAttribute('title') || null);
        out.push({
            sku: m[1],
            title: title,
            brand: null,
            price_texts: priceTextsIn(card),
            url: href
        });
        if (out.length >= 48) break;
    }
    return JSON.stringify({items: out, title: document.title || ''});
}
"""

# Spliced rather than duplicated: one fix to tile resolution or price selection
# lands on every CDP source at once.
_SEARCH_EXTRACT_JS = _SEARCH_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)


def _search_item_from_tile(tile: dict[str, Any]) -> LamodaSearchItemOut:
    """Map one extracted tile onto the wire shape, parsing prices in Python.

    Accepts the numeric ``price_rub`` an older build cached alongside the current
    ``price_texts`` candidates; the wire shape does not change either way.
    """
    price, old_price = prices_from_tile(tile)
    return LamodaSearchItemOut(
        sku=tile.get("sku"),
        title=title_from_tile(tile),
        brand=title_from_tile({"title": tile.get("brand")}),
        price_rub=price,
        old_price_rub=old_price,
        url=tile.get("url"),
    )


async def _graphql_card(sku: str, ctx: Context | None) -> dict[str, Any]:
    """Tier-1: POST the SKU-enrichment query over plain HTTPS."""
    cache_key = f"graphql:{sku}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    await _polite_wait()
    query = _GRAPHQL_QUERY % json.dumps(sku)
    try:
        # Requests that work in a browser carry the product page as Referer.
        # Tested against the live endpoint it made no difference either way, so
        # this is alignment with a known-good request rather than a fix — kept
        # because it costs nothing and removes one variable.
        headers = dict(_HEADERS)
        headers["Referer"] = f"{SITE_BASE}/p/{sku.lower()}/"
        async with build_client(timeout_s=TIMEOUT, headers=headers, proxy=_proxy()) as client:
            response = await client.post(GRAPHQL_URL, json={"query": query})
    except httpx.TransportError as exc:
        raise_tool_error(TransportDownError(f"Lamoda GraphQL unreachable: {exc}"))
    if response.status_code != 200:
        raise_tool_error(
            TransportDownError(
                f"Lamoda GraphQL answered HTTP {response.status_code}. "
                f"Body preview: {_redact(response.text[:200]) or '<empty>'}"
            )
        )
    if len(response.content) > MAX_BODY_BYTES:
        raise_tool_error(TransportDownError("GraphQL body over the cap"))
    try:
        payload = response.json()
    except json.JSONDecodeError:
        raise_tool_error(ParserDriftError(f"non-JSON GraphQL body; preview: {response.text[:200]}"))
    # Lamoda does not use the standard GraphQL envelope. Captured live in July
    # 2026 against the real endpoint:
    #   found      {"error": null, "result": [{…}]}
    #   unknown    {"error": null, "result": null}
    #   bad field  {"error": "Internal server error", "code": -32603}
    # The products live under `result`, not `data.products`, and a failure comes
    # back as a single `error` string rather than an `errors` array. Reading the
    # standard shape meant every response looked like drift.
    upstream_error = payload.get("error")
    if isinstance(upstream_error, str) and upstream_error:
        code = payload.get("code")
        suffix = f" (code {code})" if code is not None else ""
        raise_tool_error(
            ParserDriftError(
                f"Lamoda GraphQL rejected the query: {upstream_error[:160]}{suffix}. "
                "A wrong field name in the query is the usual cause."
            )
        )
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        # Kept as a fallback in case Lamoda ever moves to the standard shape.
        first = errors[0] if isinstance(errors[0], dict) else {}
        detail = str(first.get("message") or errors[0])[:200]
        raise_tool_error(ParserDriftError(f"Lamoda GraphQL rejected the query: {detail}"))

    products = payload.get("result")
    if products is None:
        data = payload.get("data")
        products = data.get("products") if isinstance(data, dict) else None
    if products is None:
        # `result: null` is how an unknown SKU comes back — a real not-found,
        # not a broken parser.
        raise_tool_error(NotFoundError(f"Lamoda SKU {sku} returned no product."))
    if not isinstance(products, list):
        raise_tool_error(ParserDriftError(f"GraphQL result is {type(products).__name__}, expected a list"))
    if not products:
        raise_tool_error(NotFoundError(f"Lamoda SKU {sku} returned no product."))
    product = products[0]
    if not isinstance(product, dict):
        raise_tool_error(ParserDriftError("GraphQL product entry is not an object"))
    _cache.set(cache_key, product)
    return product


async def _cdp_render_search(query: str, ctx: Context | None) -> dict[str, Any]:
    """Tier-2: render the search page in the operator's Chrome, extract tiles."""
    cache_key = f"search:{query}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    url = f"{SITE_BASE}/catalogsearch/result/?q={urllib.parse.quote(query)}"

    async def _attempt() -> dict[str, Any]:
        async with _cdp_lock:
            await _polite_wait()
            async with open_page(url, wait_ms=8000) as page:
                raw = await asyncio.wait_for(page.evaluate(_SEARCH_EXTRACT_JS), timeout=30.0)
        if not isinstance(raw, str) or len(raw.encode()) > MAX_BODY_BYTES:
            raise ToolError(TransportDownError("extracted page data missing or over the body cap"))
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ToolError(ParserDriftError("search extractor returned a non-object payload"))
        return data

    try:
        payload = await asyncio.wait_for(_attempt(), timeout=max(0.01, float(TIMEOUT)))
    except TimeoutError:
        raise_tool_error(TransportDownError(f"CDP timeout after {TIMEOUT}s"))
    _cache.set(cache_key, payload)
    return payload


@mcp.tool(
    name="lamoda_search",
    annotations=ToolAnnotations(
        title="Lamoda Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def lamoda_search(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Search text, e.g. 'кроссовки nike'")],
    ctx: Context | None = None,
) -> LamodaSearchResponse:
    """Search Lamoda, rendered in the operator's Chrome (discovery is blocked tier 1).

    ## Return Format

    LamodaSearchResponse: {status, query, tier_used, count, items[], meta}.
    Items carry sku, title, brand, price_rub (None when absent — never 0), url.

    ## Error Format

    ToolError: TransportDownError on CDP/nav failures; ParserDriftError when a
    rendered page yields zero SKUs, which means the tile shape moved.
    """
    log_event("lamoda_search.start", query=query[:60])
    try:
        try:
            payload = await _cdp_render_search(query.strip(), ctx)
        except NavBlocked as exc:
            raise_tool_error(TransportDownError(f"Lamoda navigation blocked (HTTP {exc.status})."))
        items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        if not items_raw:
            raise_tool_error(
                ParserDriftError(
                    "rendered search page yielded zero SKUs — either the query matched nothing or the tile shape moved; verify manually"
                )
            )
        items = [_search_item_from_tile(it) for it in items_raw if isinstance(it, dict)]
        warnings: list[str] = []
        if all(it.price_rub is None for it in items):
            warnings.append("no_prices_on_page")
        result = LamodaSearchResponse(query=query, tier_used="cdp", count=len(items), items=items)
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="lamoda_search")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("lamoda_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"lamoda_search failed: {exc}")))


@mcp.tool(
    name="lamoda_card",
    annotations=ToolAnnotations(
        title="Lamoda Product Card", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def lamoda_card(
    sku_or_url: Annotated[
        str, Field(min_length=1, max_length=300, description="SKU (MP002XM1RMM3) or lamoda.ru product URL")
    ],
    ctx: Context | None = None,
) -> LamodaCardResponse:
    """Fetch one Lamoda product card via the anonymous GraphQL endpoint (tier 1).

    ## Return Format

    LamodaCardResponse: {status, sku, title, brand, price_rub, old_price_rub,
    is_available, sizes[], url, tier_used, meta}. Lamoda exposes no ratings.

    ## Error Format

    ToolError: BadRequestError when no SKU can be extracted; NotFoundError when
    the SKU has no product; TransportDownError on HTTP failures; ParserDriftError
    when the GraphQL envelope changed.
    """
    log_event("lamoda_card.start", input=sku_or_url[:80])
    try:
        sku = _extract_sku(sku_or_url)
        if sku is None:
            raise_tool_error(
                BadRequestError(f"could not extract a SKU from {sku_or_url!r}; pass MP… or a lamoda.ru product URL")
            )
        product = await _graphql_card(sku, ctx)
        sizes_field = product.get("sizes")
        sizes_raw: list[Any] = sizes_field if isinstance(sizes_field, list) else []
        sizes = [
            LamodaSizeOut(size=s.get("size"), is_available=s.get("is_available"))
            for s in sizes_raw
            if isinstance(s, dict)
        ]
        result = LamodaCardResponse(
            sku=sku,
            title=product.get("name"),
            brand=product.get("brand_name"),
            price_rub=R.coerce_price(product.get("price_amount")),
            old_price_rub=R.coerce_price(R.first_present(product, "old_price_amount", "old_price")),
            is_available=product.get("is_available"),
            sizes=sizes,
            url=f"{SITE_BASE}/p/{sku.lower()}/",
            tier_used="graphql",
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), [], source="lamoda_card")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("lamoda_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"lamoda_card failed: {exc}")))


@mcp.tool(
    name="lamoda_selfcheck",
    annotations=ToolAnnotations(
        title="Lamoda Self-Check", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def lamoda_selfcheck(ctx: Context | None = None) -> LamodaSelfcheckResponse:
    """Structural drift canary for Lamoda (tri-state). Probes the GraphQL card
    path (tier 1) and the CDP search path (tier 2).

    GraphQL down is inconclusive for the card check; CDP down / a redirect loop
    is inconclusive for the search check. Only a reached payload that fails its
    parse smoke is drift.

    ## Return Format

    LamodaSelfcheckResponse: {status, healthy, connector, checks, ...}.
    """
    log_event("lamoda_selfcheck.start")
    try:
        result = await _lamoda_selfcheck_impl(ctx)
        log_event("lamoda_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("lamoda_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"lamoda_selfcheck failed: {exc}")))


async def _lamoda_selfcheck_impl(ctx: Context | None) -> LamodaSelfcheckResponse:
    checks: dict[str, dict] = {}

    # GraphQL tier: a 502/403 here means the endpoint moved or the wall grew.
    try:
        async with asyncio.timeout(60):
            await _graphql_card("MP002XM1RMM3", ctx)
        checks["card_graphql"] = R.selfcheck_entry(
            "healthy", baseline="graphql-products-v2", notes=["SKU enrichment answered"]
        )
    except ToolError as exc:
        state = "drift" if "ParserDrift" in type(exc).__name__ else "inconclusive"
        reason = "parse_smoke_failed" if state == "drift" else "transport_down"
        checks["card_graphql"] = R.selfcheck_entry(
            state, baseline="graphql-products-v2", reason=reason, notes=[str(exc)[:160]]
        )
    except Exception as exc:
        checks["card_graphql"] = R.selfcheck_entry(
            "inconclusive", baseline="graphql-products-v2", reason="transport_down", notes=[f"{type(exc).__name__}"]
        )

    # CDP tier: search tile extraction.
    try:
        async with asyncio.timeout(90):
            payload = await _cdp_render_search("кроссовки", ctx)
        items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        if items_raw:
            checks["search"] = R.selfcheck_entry(
                "healthy", baseline="cdp-search-tiles-v1", notes=[f"{len(items_raw)} tiles extracted"]
            )
        else:
            checks["search"] = R.selfcheck_entry(
                "drift", baseline="cdp-search-tiles-v1", reason="parse_smoke_failed", notes=["zero SKUs"]
            )
    except ToolError as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive", baseline="cdp-search-tiles-v1", reason="transport_down", notes=[str(exc)[:160]]
        )
    except Exception as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive", baseline="cdp-search-tiles-v1", reason="transport_down", notes=[f"{type(exc).__name__}"]
        )

    result_dict = R.selfcheck_result(
        "lamoda",
        checks,
        required=("card_graphql", "search"),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return LamodaSelfcheckResponse(**result_dict)
