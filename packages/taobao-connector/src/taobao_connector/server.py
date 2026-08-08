"""Taobao MCP connector.

Taobao's search is a client-side React app whose data layer is the signed mtop
API: every XHR wants a ``sign`` parameter computed from the ``_m_h5_tk`` cookie
token, and anonymous probes answer ``FAIL_SYS_TOKEN_EMPTY``. There is no
anonymous JSON route worth maintaining — but there is also no captcha wall: the
pages themselves answer 200 from a datacenter IP. So every read here runs in
the operator's own Chrome over CDP, where the site's own JS signs requests
natively, and the connector reads the rendered DOM plus in-page state.

Verified live July 2026 from a datacenter IP (docs/ANTI_BOT.md):
  - ``s.taobao.com/search?q=…`` — 200, ~33 KB shell, results load over mtop XHR
  - ``h5api.m.taobao.com`` unsigned — ``FAIL_SYS_TOKEN_EMPTY``
  - main page, 1688, AliExpress — 200, no IP-level block

Prices stay in yuan (CNY). An agent comparing against ruble sources must
convert explicitly — a baked-in rate would go silently stale.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC. Use
``log_event`` (stderr) or the ``Context`` logging methods.
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
from mcp_core.dom import JS_HELPERS, prices_from_tile
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
from mcp_core.transport.chrome_cdp import NavBlocked, open_page
from pydantic import Field

from taobao_connector.models_output import (
    MetaOut,
    TaobaoCardResponse,
    TaobaoSearchItemOut,
    TaobaoSearchResponse,
    TaobaoSelfcheckResponse,
)
from taobao_connector.settings import get_settings
from taobao_connector.shape_reference import SEARCH_SHAPE_REFERENCE, missing_required_families

_settings = get_settings()

SERVER_VERSION = "1.4.1"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SEARCH_BASE = "https://s.taobao.com/search"
ITEM_BASE = "https://item.taobao.com/item.htm"

TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes
_min_gap = _settings.min_gap

# Item ids are 11-12 digit strings; keep them as strings (they exceed the
# JS-safe integer range, and the page itself treats them as strings).
_ITEM_ID_RE = re.compile(r"[?&]id=(\d{9,13})\b")

mcp = FastMCP(
    name="taobao-connector",
    version=SERVER_VERSION,
    instructions=(
        "Taobao listings: search and item cards, prices in yuan (CNY). Every "
        "read runs in the operator's own Chrome over CDP — Taobao's data API is "
        "signed per session, so there is no anonymous tier. Start with "
        "taobao_search; taobao_card takes an item id or URL."
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


def _extract_item_id(raw: str) -> str | None:
    """Pull the item id out of an item.taobao.com URL or a bare numeric id.

    Host-checked on purpose. The id is the only thing that survives this
    function, and the card URL is rebuilt from ITEM_BASE, so a caller cannot
    smuggle ``?id=`` into a path on someone else's domain and steer the
    operator's logged-in Chrome there. Anything that is not taobao.com, or
    carries a scheme we do not navigate, returns None.
    """
    raw = raw.strip()
    if raw.isdigit() and 9 <= len(raw) <= 13:
        return raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme:
        if parts.scheme not in ("http", "https"):
            return None
        host = (parts.hostname or "").rstrip(".").lower()
        if host != "taobao.com" and not host.endswith(".taobao.com"):
            return None
    elif raw.startswith("//"):
        # Scheme-relative: the authority belongs to someone else, so the host
        # check above never runs and an off-host URL would yield an id instead
        # of a refusal. The card only ever navigates ITEM_BASE, so nothing
        # leaks — but silently reading an id out of a stranger's URL is not a
        # contract worth keeping.
        return None
    match = _ITEM_ID_RE.search(raw)
    if match:
        return match.group(1)
    return None


# In-page extractor: walks the rendered search DOM and returns plain data.
# Kept defensive — every panel is optional, because Taobao A/B tests layout
# variants constantly and a missing shop block must not kill the item.
# The shared helpers replace the per-connector heuristics that produced the
# July-2026 class of bugs elsewhere (closest() resolving to an empty overlay,
# Math.min picking the instalment, innerText absent in jsdom). Taobao tiles get
# the same treatment: the extractor returns raw display text, and the price is
# decided in Python by mcp_core.dom.prices_from_tile from glyph-attached
# candidates — so a yuan-glued "999¥" is a price and a bare promo number is not.
_SEARCH_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    const ID_RE = /[?&]id=(\\d{9,13})/;
    const out = [];
    const anchors = document.querySelectorAll('a[href*="item.taobao.com"], a[href*="//item.taobao.com"]');
    const seen = new Set();
    for (const a of anchors) {
        const href = a.href || '';
        const m = href.match(ID_RE);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);
        // tileRootFor, not closest(): an anchor whose own class looks like a
        // card must not become the tile. See mcp_core.dom.
        const card = tileRootFor(a, ID_RE) || a;
        // A dedicated title node carries the clean name. The tile anchor wraps
        // the WHOLE tile, and reading its text first glues the price, sales
        // and shop into the title (live capture 2026-08-07,
        // tests/fixtures/search_grid_live.html); anchor text is the fallback.
        let title = null;
        const titleNode = card.querySelector('[class*="title"], [class*="Title"]');
        if (titleNode) {
            title = cleanText(titleNode) || (titleNode.getAttribute('title') || null);
        }
        if (!title) {
            let titleEl = null;
            for (const cand of card.querySelectorAll('a[href*="item.taobao.com"], [class*="title"], [class*="Title"]')) {
                if (cleanText(cand)) { titleEl = cand; break; }
            }
            title = (titleEl ? cleanText(titleEl) : null) || (a.getAttribute('title') || null);
        }

        // Price: the modern layout splits it into unit (¥) + priceInt +
        // priceFloat nodes. The whole price block as one text blob glues the
        // sales count ("200+人付款") onto the digits, which coerce_price
        // rejects as ambiguous — every priced tile would read as no price.
        // Reassemble the display parts; Python's coerce_price still does the
        // parsing. Without a priceInt node, fall back to the shared glyph hunt
        // (the older layout renders one glyph-attached price node).
        const priceRoot = card.querySelector('[class*="priceWrapper"], [class*="PriceWrapper"]') || card;
        let price_texts = priceTextsIn(priceRoot);
        const intEl = priceRoot.querySelector('[class*="priceInt"]');
        if (intEl) {
            const intPart = cleanText(intEl);
            if (intPart) {
                const unitEl = priceRoot.querySelector('[class*="unit"]');
                const floatEl = priceRoot.querySelector('[class*="priceFloat"]');
                const unit = unitEl && cleanText(unitEl) ? cleanText(unitEl) : '';
                const floatPart = floatEl && cleanText(floatEl) ? cleanText(floatEl) : '';
                price_texts = {
                    attached: [unit + intPart + floatPart].concat(price_texts.attached),
                    other: price_texts.other
                };
            }
        }

        // Shop / sales / location from their scoped nodes first. The legacy
        // line scan below stays as the fallback, but it splits on newlines and
        // the live rendered tile text carries none, so on the modern layout it
        // only produces glued blobs.
        let shop = null, location = null, sales = null;
        const shopEl = card.querySelector('[class*="shopNameText"], [class*="Shop--shop"], [class*="shop--"]');
        if (shopEl) shop = cleanText(shopEl);
        const salesEl = card.querySelector('[class*="realSales"], [class*="sales"]');
        if (salesEl) sales = cleanText(salesEl);
        const procityEls = card.querySelectorAll('[class*="procity"]');
        if (procityEls.length) {
            const parts = [];
            for (const p of procityEls) {
                const t = cleanText(p);
                if (t) parts.push(t);
            }
            if (parts.length) location = parts.join(' ');
        }
        if (!shop || !sales || !location) {
            const lines = (card.textContent || '').split('\\n').map(s => s.trim()).filter(Boolean);
            for (const line of lines) {
                if (!sales && /人付款|人收货|已售|约售|付款$/.test(line)) sales = line;
                else if (!shop && /店$/.test(line)) shop = line;
                else if (!location && /发货地|广东|浙江|江苏|上海|北京/.test(line)) location = line;
            }
        }
        out.push({
            item_id: m[1],
            title: title,
            price_texts: price_texts,
            shop_name: shop,
            location: location,
            sales: sales,
            url: href.split('?')[0] + '?id=' + m[1]
        });
        if (out.length >= 48) break;
    }
    return JSON.stringify({items: out, title: document.title || ''});
}
"""

# Spliced like every other CDP source: one fix to tile resolution or price
# selection lands on all four connectors at once.
_SEARCH_EXTRACT_JS = _SEARCH_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)


_CARD_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    // The modern card renders the product name in a mainTitle span (with the
    // same text in its title attribute); there is no h1. The generic
    // [class*="title"] fallback is last on purpose: first in document order it
    // matches the header's image-search widget and reads its placeholder —
    // that happened on the live capture of 2026-08-07
    // (tests/fixtures/item_card_live.html).
    let title = null;
    const mainTitleEl = document.querySelector('[class*="mainTitle"]');
    if (mainTitleEl) {
        title = cleanText(mainTitleEl) || (mainTitleEl.getAttribute('title') || null);
    }
    if (!title) {
        const titleEl = document.querySelector('h1')
            || document.querySelector('[class*="title"], [class*="Title"]');
        title = (titleEl ? cleanText(titleEl) : null) || document.title || null;
    }

    // Price: the modern card renders symbol + text spans inside
    // highlightPrice (the amount the buyer pays) and subPrice (the
    // before-discount figure). A body-wide glyph hunt misses them: the
    // candidate spans carry digits only, and their parent's text glues Chinese
    // labels around the glyph, so the glyph-attachment check fails and THE
    // price lands among the weak candidates. Reassemble the display parts;
    // Python's coerce_price still does the parsing. Without those blocks, fall
    // back to the shared glyph hunt (older layouts).
    const priceRoot = document.querySelector('[class*="priceWrap"], [class*="PriceWrap"]') || document.body;
    let priceTexts = priceTextsIn(priceRoot);
    const assembled = [];
    for (const blockSel of ['[class*="highlightPrice"]', '[class*="subPrice"]']) {
        for (const block of priceRoot.querySelectorAll(blockSel)) {
            // The blocks render their parts as sibling spans, and the yuan
            // sign rides in a symbol-- span OR a bare text-- span depending
            // on the block (live capture: highlightPrice uses symbol--,
            // subPrice uses a second text-- span). Concatenate every part in
            // DOM order and keep results that carry digits.
            const parts = [];
            for (const el of block.querySelectorAll('[class*="symbol"], [class*="text"]')) {
                const t = cleanText(el);
                if (t) parts.push(t);
            }
            const joined = parts.join('');
            if (joined && /\\d/.test(joined)) assembled.push(joined);
        }
    }
    if (assembled.length) {
        priceTexts = {attached: assembled.concat(priceTexts.attached), other: priceTexts.other};
    }

    let shop = null, sales = null;
    // The name rides in a span[class*="shopName"] (with the same text in its
    // title attribute); the wrapping div matches the same substring and its
    // text glues the rating and service stats onto the name.
    const shopEl = document.querySelector('span[class*="shopName"]') || document.querySelector('[class*="shopName"]');
    if (shopEl) shop = cleanText(shopEl) || (shopEl.getAttribute('title') || null);
    const lines = (document.body.textContent || '').split('\\n').map(s => s.trim()).filter(Boolean);
    for (const line of lines) {
        // A glued body blob (no newlines in the live render) must never become
        // a sales/shop value — cap the candidate length like the price hunt.
        if (line.length > 40) continue;
        if (/人付款|人收货|已售/.test(line)) sales = sales || line;
        if (/店$/.test(line) && !shop) shop = line;
    }
    if (!sales) {
        // The modern card renders «已售 N+» in an unnamed colored span, and
        // the body text carries no newlines for the line scan to split. Find
        // the short text node itself; anything long is glued body text.
        // (4 === NodeFilter.SHOW_TEXT; the name itself is absent from some
        // jsdom harnesses, so the constant is spelled out.)
        const walker = document.createTreeWalker(document.body, 4);
        let node;
        while ((node = walker.nextNode())) {
            const t = (node.textContent || '').trim();
            if (t.length <= 20 && /^已售/.test(t) && /\\d/.test(t)) { sales = t; break; }
        }
    }
    const imgs = document.querySelectorAll('[class*="desc"] img, [class*="Desc"] img, #description img');
    return JSON.stringify({
        title: title,
        price_texts: priceTexts,
        shop_name: shop,
        sales: sales,
        description_images: imgs.length,
        page_title: document.title || ''
    });
}
"""

_CARD_EXTRACT_JS = _CARD_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)


def _search_item_from_tile(tile: dict[str, Any]) -> TaobaoSearchItemOut:
    """Map one extracted tile onto the wire shape, parsing prices in Python.

    Accepts BOTH shapes on purpose: the extractor now returns ``price_texts``,
    but a payload cached by an older build still carries a numeric
    ``price_cny``, and a cache entry outliving a deploy must not start
    answering with nulls. The wire shape is unchanged: ``price_cny`` is a float
    or None, never 0.
    """
    price, _old = prices_from_tile(tile)
    if price is None:
        price = R.coerce_price(tile.get("price_cny"))
    return TaobaoSearchItemOut(
        item_id=tile.get("item_id"),
        title=R.flatten_text(tile.get("title")),
        price_cny=price,
        shop_name=R.flatten_text(tile.get("shop_name")),
        location=R.flatten_text(tile.get("location")),
        sales=R.flatten_text(tile.get("sales")),
        url=tile.get("url"),
    )


async def _cdp_render(url: str, extract_js: str, wait_ms: int, ctx: Context | None) -> dict[str, Any]:
    """Open ``url`` in the operator's Chrome, wait for client-side render, extract.

    Serialized via _cdp_lock: a burst of searches must not spray tabs across the
    operator's browser. The extracted JSON is capped like any HTTP body — an
    inflated page would otherwise cross the CDP serialization pipeline raw.
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


def _login_wall(data: dict[str, Any]) -> bool:
    """A redirected login wall reads as an empty extraction with a telling title."""
    title = str(data.get("title") or data.get("page_title") or "")
    return "登录" in title or "login" in title.lower()


@mcp.tool(
    name="taobao_search",
    annotations=ToolAnnotations(
        title="Taobao Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def taobao_search(
    query: Annotated[
        str,
        Field(min_length=1, max_length=200, description="Search text, Chinese or English, e.g. '手机' or 'headphones'"),
    ],
    page: Annotated[int, Field(ge=1, le=100, description="Result page (1-based)")] = 1,
    ctx: Context | None = None,
) -> TaobaoSearchResponse:
    """Search Taobao listings, rendered in the operator's Chrome.

    ## Return Format

    TaobaoSearchResponse: {status, query, page, tier_used, count, items[], meta}.
    Items carry item_id (string), title, price_cny (None when hidden — never 0),
    shop_name, sales label, url.

    ## Error Format

    ToolError: TransportDownError when Chrome/CDP is unreachable or the page
    lands on a login wall (log into taobao.com in the scraping profile, then
    retry); ParserDriftError when a rendered page yields zero items, which means
    the DOM shape moved.
    """
    log_event("taobao_search.start", query=query[:60], page=page)
    try:
        params = urllib.parse.urlencode({"q": query.strip(), "page": str(page)})
        url = f"{SEARCH_BASE}?{params}"
        cached = _cache.get(url)
        if cached is not None:
            payload, tier = cached, "cache"
        else:
            try:
                payload = await _cdp_render(url, _SEARCH_EXTRACT_JS, wait_ms=6000, ctx=ctx)
            except NavBlocked as exc:
                raise_tool_error(
                    TransportDownError(
                        f"Taobao navigation blocked (HTTP {exc.status}). Check the scraping-profile Chrome and retry."
                    )
                )
            tier = "cdp"
            _cache.set(url, payload)

        if _login_wall(payload):
            raise_tool_error(
                TransportDownError(
                    "Taobao served a login wall. Log into taobao.com in the Chrome scraping profile (scripts/start_chrome_cdp.sh), then retry."
                )
            )
        items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        if not items_raw:
            raise_tool_error(
                ParserDriftError(
                    "rendered search page yielded zero items — either the query genuinely matched nothing or the DOM shape moved; verify manually before quoting"
                )
            )
        items = [_search_item_from_tile(it) for it in items_raw if isinstance(it, dict)]
        warnings: list[str] = []
        priceless = sum(1 for it in items if it.price_cny is None)
        if priceless == len(items):
            warnings.append("no_prices_on_page")
        result = TaobaoSearchResponse(query=query, page=page, tier_used=tier, count=len(items), items=items)
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="taobao_search")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("taobao_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"taobao_search failed: {exc}")))


@mcp.tool(
    name="taobao_card",
    annotations=ToolAnnotations(
        title="Taobao Item Card", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def taobao_card(
    item_id_or_url: Annotated[str, Field(min_length=1, max_length=300, description="Item id or item.taobao.com URL")],
    ctx: Context | None = None,
) -> TaobaoCardResponse:
    """Fetch one Taobao item card.

    ## Return Format

    TaobaoCardResponse: {status, item_id, title, price_cny, shop_name, sales,
    description_images, url, tier_used, meta}. price_cny is None when the page
    hides it or prices by variant — never 0. When the description-image count
    drifts to a non-number, description_images degrades to 0 and meta.warnings
    names the drift — the card itself still answers.

    ## Error Format

    ToolError: BadRequestError when no id can be extracted; NotFoundError when
    the item page reports itself gone; TransportDownError on login walls and CDP
    failures; ParserDriftError when a rendered card has neither title nor price.
    """
    log_event("taobao_card.start", input=item_id_or_url[:80])
    try:
        item_id = _extract_item_id(item_id_or_url)
        if item_id is None:
            raise_tool_error(
                BadRequestError(
                    f"could not extract an item id from {item_id_or_url!r}; pass a 9-13 digit id or an item.taobao.com URL with ?id="
                )
            )
        url = f"{ITEM_BASE}?id={item_id}"
        cached = _cache.get(url)
        if cached is not None:
            payload, tier = cached, "cache"
        else:
            try:
                payload = await _cdp_render(url, _CARD_EXTRACT_JS, wait_ms=6000, ctx=ctx)
            except NavBlocked as exc:
                raise_tool_error(
                    TransportDownError(
                        f"Taobao navigation blocked (HTTP {exc.status}). Check the scraping-profile Chrome and retry."
                    )
                )
            tier = "cdp"
            _cache.set(url, payload)

        if _login_wall(payload):
            raise_tool_error(
                TransportDownError(
                    "Taobao served a login wall. Log into taobao.com in the Chrome scraping profile, then retry."
                )
            )
        title = payload.get("title")
        page_title = str(payload.get("page_title") or "")
        if "不存在" in page_title or "很抱歉" in page_title:
            raise_tool_error(NotFoundError(f"Taobao item {item_id} reports itself gone ({page_title[:60]})."))
        price, _old = prices_from_tile(payload)
        if price is None:
            price = R.coerce_price(payload.get("price_cny"))
        if title is None and price is None:
            raise_tool_error(
                ParserDriftError(
                    "rendered card has neither title nor price — the item page shape moved; verify manually"
                )
            )
        # A decorative count drifting to a non-number must not kill an otherwise
        # readable card, and it is not a transport event — degrade to 0 and name
        # the drift in meta.warnings instead of leaking it to the catch-all,
        # which would misreport it as transport_down.
        card_warnings: list[str] = []
        raw_desc_images = payload.get("description_images")
        desc_images = R.coerce_int(raw_desc_images)
        if desc_images is None:
            desc_images = 0
            if raw_desc_images not in (None, 0, 0.0, ""):
                card_warnings.append(
                    f"description_images: expected a count, got {str(raw_desc_images)[:40]!r}; reported 0"
                )
        result = TaobaoCardResponse(
            item_id=item_id,
            title=title,
            price_cny=price,
            shop_name=payload.get("shop_name"),
            sales=payload.get("sales"),
            description_images=desc_images,
            url=url,
            tier_used=tier,
        )
        attached = R.attach_meta(
            result.model_dump(by_alias=True, exclude={"meta"}), card_warnings, source="taobao_card"
        )
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("taobao_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"taobao_card failed: {exc}")))


@mcp.tool(
    name="taobao_selfcheck",
    annotations=ToolAnnotations(
        title="Taobao Self-Check", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def taobao_selfcheck(ctx: Context | None = None) -> TaobaoSelfcheckResponse:
    """Structural drift canary for Taobao (tri-state). Renders one live search
    page in the operator's Chrome and checks the extractor still finds items.

    CDP down or a login wall is ``inconclusive`` (transport/session), NEVER
    drift. Only a rendered page that yields zero items is ``drift``.

    ## Return Format

    TaobaoSelfcheckResponse: {status, healthy, connector, checks, server_version,
    server_started_at, process_id}.

    ## Error Format

    Raises ToolError (TransportDownError) ONLY on an unexpected internal bug
    that prevents the canary from producing any verdict. Transport/block
    failures of individual sub-checks map to inconclusive entries, not errors.
    """
    log_event("taobao_selfcheck.start")
    try:
        result = await _taobao_selfcheck_impl(ctx)
        log_event("taobao_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("taobao_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"taobao_selfcheck failed: {exc}")))


async def _taobao_selfcheck_impl(ctx: Context | None) -> TaobaoSelfcheckResponse:
    checks: dict[str, dict] = {}
    baseline = "cdp-search-v1"
    url = f"{SEARCH_BASE}?{urllib.parse.urlencode({'q': '手机', 'page': '1'})}"
    try:
        async with asyncio.timeout(90):
            payload = await _cdp_render(url, _SEARCH_EXTRACT_JS, wait_ms=6000, ctx=ctx)
    except TimeoutError:
        checks["search"] = R.selfcheck_entry(
            "inconclusive", baseline=baseline, reason="timeout", notes=["render exceeded 90s"]
        )
    except NavBlocked as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive", baseline=baseline, reason="blocked", notes=[f"navigation http {exc.status}"]
        )
    except Exception as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive",
            baseline=baseline,
            reason="transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )
    else:
        if _login_wall(payload):
            checks["search"] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="auth_missing",
                notes=["login wall — log into taobao.com in the scraping profile"],
            )
        else:
            items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
            if items_raw:
                # Items extract — now ask the second question: did the SHAPE
                # move? The registry was measured on the captured page
                # (2026-08-07); a live payload that loses a parser-critical
                # key family is structural drift even while items come back.
                live_signature = R.shape_signature(payload)
                drift = R.diff_keys(SEARCH_SHAPE_REFERENCE, live_signature)
                missing = missing_required_families(live_signature)
                if missing:
                    checks["search"] = R.selfcheck_entry(
                        "drift",
                        baseline=baseline,
                        reason="shape_drift",
                        notes=[
                            f"{len(items_raw)} items extracted",
                            "required key families missing: " + "; ".join(", ".join(family) for family in missing),
                        ],
                        shape_missing=drift["missing"],
                        shape_added=drift["added"],
                    )
                else:
                    notes = [f"{len(items_raw)} items extracted", "shape matches the captured reference"]
                    if drift["added"]:
                        notes.append(f"{len(drift['added'])} new paths vs baseline (informational)")
                    checks["search"] = R.selfcheck_entry(
                        "healthy", baseline=baseline, notes=notes, shape_added=drift["added"]
                    )
            else:
                checks["search"] = R.selfcheck_entry(
                    "drift", baseline=baseline, reason="parse_smoke_failed", notes=["rendered page yielded zero items"]
                )

    result_dict = R.selfcheck_result(
        "taobao",
        checks,
        required=("search",),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return TaobaoSelfcheckResponse(**result_dict)
