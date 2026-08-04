"""Citilink MCP connector.

Citilink sits behind Qrator's JavaScript proof-of-work: every dynamic page answers
HTTP 401 with a ~349 KB challenge script, and the one route not behind it
(``/ajax-state/product-buy/``) wants a CSRF token you can only get from a page
that *is* behind it. No header trick clears a proof-of-work — but a real
Chrome executes it natively, so every read here runs in the operator's browser
over CDP and parses the rendered DOM.

Verified live July 2026 from a datacenter IP (docs/ANTI_BOT.md):
  - every dynamic page — 401 + Qrator challenge
  - ``restapi.citilink.ru`` — 403 even from residential (internal SSR address)
  - anonymously reachable: ``robots.txt``, ``sitemap.xml`` (~600k product URLs)

The CDP route needs verification from the operator's machine: the DOM tile
selectors below are written against the public catalog layout, and only a
challenge-passed session can confirm them. Run citilink_selfcheck from your Chrome
first — its verdict tells you which case you are in.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import urllib.parse
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
from mcp_core.cache import TTLCache
from mcp_core.dom import JS_HELPERS, prices_from_tile, title_from_tile
from mcp_core.errors import (
    BadRequestError,
    ParserDriftError,
    ToolError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.pacing import Pacer
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport.chrome_cdp import NavBlocked, open_page
from pydantic import Field

from citilink_connector.models_output import (
    CitilinkCardResponse,
    CitilinkSearchItemOut,
    CitilinkSearchResponse,
    CitilinkSelfcheckResponse,
    MetaOut,
)
from citilink_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "1.3.1"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://www.citilink.ru"
SEARCH_URL = f"{SITE_BASE}/search/"

TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes
_min_gap = _settings.min_gap

# Real product routes, confirmed live July 2026 from a challenge-passed session:
#   citilink  /product/noutbuk-lenovo-2169270/   slug ending in a numeric id
#   dns       /product/b7a1667f9b19ed20/         16-hex id
# The original pattern demanded 24 hex — a MongoDB ObjectId shape neither site
# uses — so every search returned zero tiles and reported drift.
#
# The charset is deliberately narrower than "anything but a slash". This id is
# interpolated straight back into a URL that opens in the operator's logged-in
# Chrome, so `?`, `#`, `%` and `.` stay out: they would let a caller append a
# query string or walk the path on a page we then render with their cookies.
_ID_CHARS = r"[A-Za-z0-9_-]"
_PRODUCT_ID_RE = re.compile(rf"/product/({_ID_CHARS}+)")
_BARE_ID_RE = re.compile(rf"{_ID_CHARS}{{4,}}")

mcp = FastMCP(
    name="citilink-connector",
    version=SERVER_VERSION,
    instructions=(
        "Citilink electronics catalog: search and product cards. Every read "
        "runs in the operator's own Chrome over CDP — Qrator's proof-of-work "
        "blocks all anonymous access. Start with citilink_search; citilink_card takes a "
        "product id or citilink.ru product URL."
    ),
)
mcp.add_middleware(RetryMiddleware())

_cache: TTLCache = TTLCache(ttl_s=_settings.cache_ttl, max_entries=128)
_pacer = Pacer(_min_gap)
_cdp_lock = asyncio.Lock()


async def _polite_wait() -> None:
    """Space this source's requests out, and back off if it refused us.

    Reads ``_min_gap`` at call time so an operator or a test can retune the
    pace without rebuilding the pacer.
    """
    await _pacer.wait(min_gap=_min_gap)


def _extract_product_id(raw: str) -> str | None:
    """Pull the product id out of a citilink.ru product URL or a bare id.

    Host-checked on purpose. The id is the only thing that survives this
    function, and the card URL is rebuilt from SITE_BASE, so a caller cannot
    smuggle ``/product/<id>`` into a path on someone else's domain and
    steer the operator's logged-in Chrome there. Anything that is not
    citilink.ru, or carries a scheme we do not navigate, returns None.
    """
    raw = raw.strip()
    if not raw:
        return None
    if _BARE_ID_RE.fullmatch(raw):
        return raw.lower()
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme:
        if parts.scheme not in ("http", "https"):
            return None
        host = (parts.hostname or "").rstrip(".").lower()
        if host != "citilink.ru" and not host.endswith(".citilink.ru"):
            return None
        raw = parts.path
    elif raw.startswith("//"):
        # Scheme-relative: the authority is someone else's, so treat it as off-host.
        return None
    match = _PRODUCT_ID_RE.search(raw)
    return match.group(1).lower() if match else None


_SEARCH_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    // Search results render inside an iframe in at least one of this site's
    // layouts — confirmed live in July 2026, where the top-level document held
    // zero product anchors while the grid sat in a same-origin frame. Reading
    // only `document` makes that layout look like a dead parser. Collect the
    // frames we are allowed to read and treat them as one page.
    const docs = [document];
    for (const frame of document.querySelectorAll('iframe')) {
        try {
            if (frame.contentDocument) docs.push(frame.contentDocument);
        } catch (e) {
            // Cross-origin frame: not ours to read, and not where our tiles are.
        }
    }
    const ID_RE = /\\/product\\/([A-Za-z0-9_-]+)/;
    const out = [];
    const anchors = [];
    for (const doc of docs) {
        for (const a of doc.querySelectorAll('a[href*="/product/"]')) anchors.push(a);
    }
    const seen = new Set();
    for (const a of anchors) {
        const href = a.href || '';
        const m = href.match(ID_RE);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);
        // Outermost ancestor still covering one product, NOT closest(): the
        // first product anchor in a tile is often an empty image/overlay link
        // whose own class matches a [class*="product"] pattern, and closest()
        // tests the element itself first.
        const card = tileRootFor(a, ID_RE);

        // Citilink's class names are build-hashed (app-catalog-51bw0j-...), so they
        // are not selectable across deploys. What IS stable is the data-meta-*
        // contract the site uses for its own analytics — verified on captured
        // markup 2026-07-28:
        //   data-meta-name="Snippet__title"      the product name anchor
        //   data-meta-name="Snippet__price"      current price (₽ in a CHILD span)
        //   data-meta-name="Snippet__old-price"  strikethrough price, no glyph
        //   data-meta-price="59990"              machine-readable numeric price
        // The first anchor in a tile is an EMPTY overlay link, so anchor text can
        // never be the title source here.
        const titleEl = card.querySelector('[data-meta-name="Snippet__title"]');
        let title = cleanText(titleEl) || (titleEl && titleEl.getAttribute('title'));
        if (!title) {
            let fallback = null;
            for (const cand of card.querySelectorAll('a[href*="/product/"], [class*="name"], [class*="title"]')) {
                if (cleanText(cand)) { fallback = cand; break; }
            }
            title = cleanText(fallback) || a.getAttribute('title') || null;
        }

        // data-meta-price is exact and needs no parsing; keep the display strings
        // as a fallback for the day the attribute goes away.
        const metaPriceEl = card.querySelector('[data-meta-price]');
        const metaPrice = metaPriceEl ? metaPriceEl.getAttribute('data-meta-price') : null;
        const priceEl = card.querySelector('[data-meta-name="Snippet__price"]');
        const oldPriceEl = card.querySelector('[data-meta-name="Snippet__old-price"]');

        const candidates = priceTextsIn(card);
        out.push({
            product_id: m[1],
            title: title,
            price_meta: metaPrice,
            price_text: cleanText(priceEl),
            old_price_text: cleanText(oldPriceEl),
            price_texts: candidates,
            url: href
        });
        if (out.length >= 48) break;
    }
    return JSON.stringify({items: out, title: document.title || ''});
}
"""

_CARD_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    // h1 first: the generic [class*="title"] fallback can pick a section heading.
    const titleEl = document.querySelector('h1')
        || document.querySelector('[class*="product-card-top__title"]');
    const title = cleanText(titleEl) || document.title || null;

    // Scope the price hunt to the buy block when one is identifiable. Scanning
    // the whole body sweeps in recommended products, instalment offers and bonus
    // amounts — every one of them a number smaller than the real price.
    const scope = document.querySelector('[data-meta-name="ProductHeader"], [class*="product-price"], [class*="price-block"], [itemprop="offers"]')
        || document.body;
    const metaPriceEl = document.querySelector('[data-meta-price]');
    const metaPrice = metaPriceEl ? metaPriceEl.getAttribute('data-meta-price') : null;
    const priceEl = document.querySelector('[data-meta-name="Snippet__price"], [data-meta-name="ProductPrice__price"]');
    const oldPriceEl = document.querySelector('[data-meta-name="Snippet__old-price"], [data-meta-name="ProductPrice__old-price"]');
    const priceTexts = priceTextsIn(scope);

    const bodyText = (document.body ? (document.body.textContent || '') : '');
    let available = null;
    if (/Нет в наличии|Под заказ|Закончился/i.test(bodyText)) available = false;
    else if (/В наличии|В корзину|Купить/i.test(bodyText)) available = true;

    return JSON.stringify({
        title: title,
        price_meta: metaPrice,
        price_text: cleanText(priceEl),
        old_price_text: cleanText(oldPriceEl),
        price_texts: priceTexts,
        is_available: available,
        page_title: document.title || ''
    });
}
"""

# The shared helpers are spliced in rather than duplicated per connector, so a
# fix to tile resolution or price selection lands everywhere at once.
_SEARCH_EXTRACT_JS = _SEARCH_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)
_CARD_EXTRACT_JS = _CARD_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)


def _search_item_from_tile(tile: dict[str, Any]) -> CitilinkSearchItemOut:
    """Map one extracted tile onto the wire shape, parsing prices in Python.

    Tolerates the numeric ``price_rub`` an older build cached as well as the
    ``price_texts`` candidate list the current extractor returns; the wire shape
    is identical either way, and ``price_rub`` stays float-or-None, never 0.
    """
    price, old_price = prices_from_tile(tile)
    return CitilinkSearchItemOut(
        product_id=tile.get("product_id"),
        title=title_from_tile(tile),
        price_rub=price,
        old_price_rub=old_price,
        url=tile.get("url"),
    )


async def _cdp_render(url: str, extract_js: str, wait_ms: int, ctx: Context | None) -> dict[str, Any]:
    """Open ``url`` in the operator's Chrome, let Qrator pass, extract.

    The first navigation pays the proof-of-work (wait_ms is generous for that
    reason); later reads on the same session are fast. Serialized via _cdp_lock.
    """
    async with _cdp_lock:
        await _polite_wait()
        async with open_page(url, wait_ms=wait_ms) as page:
            raw = await asyncio.wait_for(page.evaluate(extract_js), timeout=30.0)
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BODY_BYTES:
        raise ToolError(TransportDownError("extracted page data missing or over the body cap"))
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ToolError(ParserDriftError("page extractor returned a non-object payload"))
    return data


def _is_qrator_wall(data: dict[str, Any]) -> bool:
    title = str(data.get("title") or data.get("page_title") or "")
    return "доступ ограничен" in title.lower() or "qrator" in title.lower() or not title.strip()


@mcp.tool(
    name="citilink_search",
    annotations=ToolAnnotations(
        title="Citilink Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def citilink_search(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Search text, e.g. 'ноутбук lenovo'")],
    ctx: Context | None = None,
) -> CitilinkSearchResponse:
    """Search Citilink, rendered in the operator's Chrome.

    ## Return Format

    CitilinkSearchResponse: {status, query, tier_used, count, items[], meta}.
    price_rub is None when absent — never 0.

    ## Error Format

    ToolError: TransportDownError on CDP/Qrator failures; ParserDriftError when
    a rendered page yields zero product tiles.
    """
    log_event("citilink_search.start", query=query[:60])
    try:
        url = f"{SEARCH_URL}?q={urllib.parse.quote(query.strip())}"
        cached = _cache.get(url)
        if cached is not None:
            payload, tier = cached, "cache"
        else:
            try:
                payload = await _cdp_render(url, _SEARCH_EXTRACT_JS, wait_ms=8000, ctx=ctx)
            except NavBlocked as exc:
                raise_tool_error(
                    TransportDownError(
                        f"Citilink navigation blocked (HTTP {exc.status}). The Qrator challenge must pass in the scraping-profile Chrome — open citilink.ru there once, then retry."
                    )
                )
            tier = "cdp"
            _cache.set(url, payload)
        items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        if not items_raw:
            raise_tool_error(
                ParserDriftError(
                    "rendered search page yielded zero product tiles — either the query matched nothing or the DOM shape moved; verify manually"
                )
            )
        items = [_search_item_from_tile(it) for it in items_raw if isinstance(it, dict)]
        warnings: list[str] = []
        if all(it.price_rub is None for it in items):
            warnings.append("no_prices_on_page")
        result = CitilinkSearchResponse(query=query, tier_used=tier, count=len(items), items=items)
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="citilink_search")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("citilink_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"citilink_search failed: {exc}")))


@mcp.tool(
    name="citilink_card",
    annotations=ToolAnnotations(
        title="Citilink Product Card", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def citilink_card(
    product_url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=400,
            description="citilink.ru product URL containing /product/<slug>/, e.g. /product/noutbuk-lenovo-2169270/",
        ),
    ],
    ctx: Context | None = None,
) -> CitilinkCardResponse:
    """Fetch one Citilink product card.

    ## Return Format

    CitilinkCardResponse: {status, product_id, title, price_rub, old_price_rub,
    is_available, url, tier_used, meta}.

    ## Error Format

    ToolError: BadRequestError when the URL carries no product id;
    TransportDownError on CDP/Qrator failures; ParserDriftError when a rendered
    card has neither title nor price.
    """
    log_event("citilink_card.start", input=product_url[:80])
    try:
        product_id = _extract_product_id(product_url)
        if product_id is None:
            raise_tool_error(
                BadRequestError(
                    f"could not extract a product id from {product_url!r}; pass a citilink.ru URL with /product/<slug>/ "
                    f"(the slug ends in a numeric id, e.g. /product/noutbuk-lenovo-2169270/)"
                )
            )
        # Rebuild from SITE_BASE rather than navigating what the caller passed:
        # this page opens in the operator's own logged-in Chrome, so the target
        # host must come from us, never from the argument.
        url = f"{SITE_BASE}/product/{product_id}/"
        cached = _cache.get(url)
        if cached is not None:
            payload, tier = cached, "cache"
        else:
            try:
                payload = await _cdp_render(url, _CARD_EXTRACT_JS, wait_ms=8000, ctx=ctx)
            except NavBlocked as exc:
                raise_tool_error(TransportDownError(f"Citilink navigation blocked (HTTP {exc.status})."))
            tier = "cdp"
            _cache.set(url, payload)
        title = title_from_tile(payload)
        price, old_price = prices_from_tile(payload)
        if title is None and price is None:
            raise_tool_error(
                ParserDriftError("rendered card has neither title nor price — the card shape moved; verify manually")
            )
        result = CitilinkCardResponse(
            product_id=product_id,
            title=title,
            price_rub=price,
            old_price_rub=old_price,
            is_available=payload.get("is_available"),
            url=url,
            tier_used=tier,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), [], source="citilink_card")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("citilink_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"citilink_card failed: {exc}")))


@mcp.tool(
    name="citilink_selfcheck",
    annotations=ToolAnnotations(
        title="Citilink Self-Check", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def citilink_selfcheck(ctx: Context | None = None) -> CitilinkSelfcheckResponse:
    """Structural drift canary for Citilink (tri-state). Renders one live search
    page in the operator's Chrome and checks tiles extract.

    Qrator-blocked or CDP-down is ``inconclusive`` (transport), NEVER drift.
    Only a rendered page that yields zero tiles is ``drift``.

    ## Return Format

    CitilinkSelfcheckResponse: {status, healthy, connector, checks, ...}.
    """
    log_event("citilink_selfcheck.start")
    try:
        result = await _citilink_selfcheck_impl(ctx)
        log_event("citilink_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("citilink_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"citilink_selfcheck failed: {exc}")))


async def _citilink_selfcheck_impl(ctx: Context | None) -> CitilinkSelfcheckResponse:
    checks: dict[str, dict] = {}
    baseline = "cdp-search-tiles-v1"
    url = f"{SEARCH_URL}?q={urllib.parse.quote('ноутбук')}"
    try:
        async with asyncio.timeout(90):
            payload = await _cdp_render(url, _SEARCH_EXTRACT_JS, wait_ms=8000, ctx=ctx)
    except TimeoutError:
        checks["search"] = R.selfcheck_entry(
            "inconclusive", baseline=baseline, reason="timeout", notes=["render exceeded 90s"]
        )
    except Exception as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive",
            baseline=baseline,
            reason="transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )
    else:
        items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        if items_raw:
            checks["search"] = R.selfcheck_entry(
                "healthy", baseline=baseline, notes=[f"{len(items_raw)} tiles extracted"]
            )
        else:
            checks["search"] = R.selfcheck_entry(
                "drift", baseline=baseline, reason="parse_smoke_failed", notes=["zero tiles"]
            )

    result_dict = R.selfcheck_result(
        "citilink",
        checks,
        required=("search",),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return CitilinkSelfcheckResponse(**result_dict)
