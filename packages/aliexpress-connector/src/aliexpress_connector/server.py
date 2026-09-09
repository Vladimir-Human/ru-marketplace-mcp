"""AliExpress MCP connector.

AliExpress gates its site behind x5sec, Alibaba's JS risk control. Measured
2026-08-20 from a Russian residential IP (docs/ANTI_BOT.md, section AliExpress):

  - anonymous HTTP, item page — 200 with a ``punish?x5secdata=`` challenge
    page («Пройдите проверку»), even with full browser headers;
  - anonymous HTTP, search/category — 200 but an empty SPA shell: the
    ``__AER_DATA__`` payload carries no catalog data, tiles are client-rendered;
  - anonymous HTTP, h5api item route — ``FAIL_SYS_API_NOT_FOUNDED``;
  - anonymous HTTP, feedback.aliexpress.ru reviews — 500.

Inside a real Chrome that has passed the challenge the picture flips:

  - the search page renders fully (77-96 tiles with prices) and never
    challenges;
  - a DIRECT ``goto`` to an item page is challenged even in a warm profile;
  - the page itself, however, can ``fetch`` an item URL in-page without a
    challenge (cookies ride along) — but the SSR shell carries no price;
  - ``window.open(itemUrl)`` from the loaded search page opens the real,
    JS-rendered card without a challenge: prices, rating and order stats are
    in the live DOM.

So every read takes the two-hop route: land on the search page, extract tiles
directly for search; for a card, ask the landing page to open the item in a
new tab and read that tab's rendered DOM. A direct navigation to item pages is
structurally absent from this connector.

The CDP route needs verification from the operator's machine: the selectors
below were written against a live grid captured 2026-08-20, and only a
challenge-passed session can confirm them. Run aliexpress_selfcheck from your
Chrome first — its verdict tells you which case you are in.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import urllib.parse
from typing import Annotated, Any, cast

from fastmcp import Context, FastMCP
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
from mcp_core.cache import TTLCache
from mcp_core.dom import JS_HELPERS, title_from_tile
from mcp_core.errors import (
    BadRequestError,
    ParserDriftError,
    ToolError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.pacing import Pacer
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport.chrome_cdp import NavBlocked, open_page
from pydantic import Field

from aliexpress_connector.models_output import (
    AliCardResponse,
    AliSearchItemOut,
    AliSearchResponse,
    AliSelfcheckResponse,
    MetaOut,
)
from aliexpress_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "2.0.2"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://aliexpress.ru"
SEARCH_URL = f"{SITE_BASE}/wholesale"
# The landing page any card read starts from. It is a live search the site keeps
# cache-friendly; measured clean across headless and two real-Chrome profiles.
_LANDING = f"{SEARCH_URL}?SearchText={urllib.parse.quote('часы')}"

TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes
_min_gap = _settings.min_gap

# Item ids on aliexpress.ru are 9-16 digit strings (1005...); accept a bare id
# or an aliexpress.ru item URL. The 9-digit floor keeps short garbage out of the
# URL we rebuild and hand to the operator's Chrome.
_ITEM_ID_RE = re.compile(r"/item/(\d{9,})(?:\.html)?")
_BARE_ID_RE = re.compile(r"\d{9,}")

mcp = FastMCP(
    name="aliexpress-connector",
    version=SERVER_VERSION,
    instructions=(
        "AliExpress catalog: search and product cards, read inside the operator's "
        "own Chrome over CDP — x5sec blocks anonymous access. Start with "
        "aliexpress_search; aliexpress_card takes an item id or aliexpress.ru item URL. "
        "Review TEXTS are not exposed: only the rating and order counts are read."
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
    """Pull the item id out of an aliexpress.ru URL or a bare numeric id.

    Host-checked on purpose. Only the id survives this function and the card
    URL is rebuilt from SITE_BASE, so a caller cannot steer the operator's
    logged-in Chrome somewhere else through the argument.
    """
    raw = raw.strip()
    if not raw:
        return None
    if _BARE_ID_RE.fullmatch(raw):
        return raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme:
        if parts.scheme not in ("http", "https"):
            return None
        host = (parts.hostname or "").rstrip(".").lower()
        if host != "aliexpress.ru" and not host.endswith(".aliexpress.ru"):
            return None
        raw = parts.path
    elif raw.startswith("//"):
        return None
    match = _ITEM_ID_RE.search(raw)
    return match.group(1) if match else None


def _is_punish(payload: dict[str, Any]) -> bool:
    """The x5sec challenge page is a transport verdict, not data."""
    return bool(payload.get("punish")) or bool(payload.get("url") and "punish" in str(payload["url"]))


def _challenge_error(detail: str = "") -> TransportDownError:
    message = (
        "AliExpress challenged the session (x5sec). Open aliexpress.ru in the "
        "scraping-profile Chrome, pass the check by hand, then retry; the "
        "connector never solves captchas itself."
    )
    hint = _pacer.rotation_hint()
    if hint:
        message += f" {hint}"
    if detail:
        message += f" Detail: {detail[:160]}"
    return TransportDownError(message, status_code=403)


# --------------------------------------------------------------- search JS ----
#
# The tile root comes from the shared tileRootFor (the DNS lesson: closest()
# tests the element itself first, so an image link masquerades as the tile and
# every tile reads as empty). Prices come from priceTextsIn: on the measured
# grid it returns {attached: ['6 967 ₽', '2 939 ₽'], other: ['2 334 купили']}
# — base price first, current second, the no-glyph orders line in `other`.
# Nothing here does arithmetic; coerce_price on the Python side decides.
_SEARCH_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    const anchors = Array.from(document.querySelectorAll('a[href*="/item/"]'))
        .filter(a => /\\/item\\/\\d{9,}\\.html/.test(a.getAttribute('href') || a.href || ''));
    const out = [];
    const seen = new Set();
    const ratingLeaf = (tile) => {
        for (const el of tile.querySelectorAll('*')) {
            if (el.children.length) continue;
            const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (/^\\d(?:[.,]\\d{1,2})?$/.test(t)) return t.replace(',', '.');
        }
        return null;
    };
    const ordersLeaf = (tile) => {
        for (const el of tile.querySelectorAll('*')) {
            if (el.children.length) continue;
            const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
            const m = t.match(/^([\\d\\s\\u00a0]+)\\s+купили$/i);
            if (m) return m[1].replace(/\\s+/g, '');
        }
        return null;
    };
    let punished = /punish|проверк/i.test((document.location && document.location.href || '') + document.title);
    for (const a of anchors) {
        if (out.length >= 48) break;
        const href = a.href || a.getAttribute('href') || '';
        const m = href.match(/\\/item\\/(\\d{9,})\\.html/);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);
        const tile = tileRootFor(a, /\\/item\\/(\\d{9,})\\.html/);
        if (!tile || !tile.textContent) continue;
        const priceInfo = priceTextsIn(tile);
        // The product name is the longest non-numeric leaf text. A leaf is
        // "numeric" when digits dominate it — >= 2 digits AND over 40% of the
        // chars — which drops "2 334 купили" and delivery lines while keeping
        // model-bearing titles like "iPhone 15 Pro" and "Galaxy S24".
        let title = null, best = 5;
        for (const el of tile.querySelectorAll('*')) {
            if (el.children.length) continue;
            const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (!t || t.length > 140) continue;
            if (DECOY_RE.test(t)) continue;
            const digits = ((t.match(/\\d/g) || []).length);
            if (digits >= 2 && !/купили$/i.test(t) && digits / t.length > 0.4) continue;
            if (t.length > best) { best = t.length; title = t; }
        }
        const sku = (href.match(/sku_id=(\\d+)/) || [])[1] || null;
        out.push({
            item_id: m[1],
            title: title,
            price_texts: priceInfo,
            rating: ratingLeaf(tile),
            orders: ordersLeaf(tile),
            sku_id: sku,
            url: href
        });
    }
    return JSON.stringify({items: out, punish: punished, page_title: document.title || ''});
}
"""

_CARD_EXTRACT_TEMPLATE = """
() => {
    //__SHARED_HELPERS__
    const punished = /punish|проверк/i.test((document.location && document.location.href || '') + document.title);
    const title = cleanText(document.querySelector('h1')) || null;
    // The sticky offer bar carries both prices — measured wrappers in DOM order
    // ['2 939 ₽', '6 967 ₽'] (current first). Scope the price read to the sticky
    // module only: a body-wide scan would harvest "customers also bought" rails
    // and label a neighbour's price as this product's strikethrough.
    const sticky = document.querySelector('[class*="HazeStickyOfferPrice__offerPrice"]')
        || document.querySelector('[class*="SnowStickyOffer"]');
    const priceInfo = priceTextsIn(sticky);
    const ratingText = cleanText(document.querySelector('[class*="HazeProductDescription__ratingWrap"]'));
    const meta = (document.querySelector('meta[name="description"]') || {}).content || null;
    return JSON.stringify({
        punish: punished,
        title: title,
        price_texts: priceInfo,
        rating_text: ratingText,
        meta_description: meta,
        url: (document.location && document.location.href || ''),
    });
}
"""

_SEARCH_EXTRACT_JS = _SEARCH_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)
_CARD_EXTRACT_JS = _CARD_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)


# Price-shaped leaves that must never BE the product price. Courier delivery
# costs, credit lines and «N ₽ с купоном» are each glyph-attached and would win
# a min() pairing; the coupon value is kept aside for the warning instead.
_PRICE_JUNK = ("купон", "доставк", "кредит", "рассрочк", "кэшбэк", "кешбэк", "бонус")


def _scored_prices(
    attached_raw: list[Any] | None,
) -> tuple[list[float], float | None]:
    """Split glyph-attached candidates into (regular prices, coupon value).

    Returns copy-parsed floats; a leaf containing «купон» is a coupon price,
    not the price a customer pays without opting in.
    """
    regular: list[float] = []
    coupon: float | None = None
    if not attached_raw:
        return regular, coupon
    for candidate in attached_raw:
        text = str(candidate).lower()
        parsed = R.coerce_price(candidate)
        if parsed is None:
            continue
        if "купон" in text:
            if coupon is None or parsed < coupon:
                coupon = parsed
        elif any(junk in text for junk in _PRICE_JUNK):
            continue
        else:
            regular.append(parsed)
    return regular, coupon


def _tile_prices(tile: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """(price, old_price, coupon_price) for a SEARCH tile.

    Measured against the live grid (2026-08-20): priceTextsIn returns the BASE
    first and the CURRENT price second — "6 967 ₽-58%2 939 ₽" renders as
    {attached: ['6 967 ₽', '2 939 ₽']}. The shared prices_from_tile would take
    attached[0] as THE price and ship the base as the current — the exact
    "plausible and wrong" failure the doctrine forbids. The smallest non-junk
    glyph-attached number is what the customer pays, the largest remaining is
    the strikethrough. A «N ₽ с купоном» leaf is neither: it goes to the third
    return value so the caller can warn, never into price_rub.
    """
    texts = tile.get("price_texts")
    attached_raw = (
        texts.get("attached") if isinstance(texts, dict) and isinstance(texts.get("attached"), list) else None
    )
    regular, coupon = _scored_prices(attached_raw)
    if not regular:
        price = R.price_from_texts(tile.get("price_text"), tile.get("price_rub"))
        return price, None, coupon
    current = min(regular)
    above = [candidate for candidate in regular if candidate > current]
    old_price = max(above) if above else R.price_from_texts(tile.get("old_price_text"))
    if old_price is not None and old_price <= current:
        old_price = None
    return current, old_price, coupon


_PRICE_UNSET: object = object()


def _item_from_payload(
    tile: dict[str, Any],
    price: float | object | None = _PRICE_UNSET,
    old_price: float | object | None = _PRICE_UNSET,
) -> AliSearchItemOut:
    """Map one extracted tile onto the wire shape, prices parsed in Python."""
    if price is _PRICE_UNSET or old_price is _PRICE_UNSET:
        computed, computed_old, _coupon = _tile_prices(tile)
        if price is _PRICE_UNSET:
            price = computed
        if old_price is _PRICE_UNSET:
            old_price = computed_old
    orders = R.coerce_int(tile.get("orders"))
    rating = R.coerce_rating(tile.get("rating"))
    return AliSearchItemOut(
        item_id=tile.get("item_id"),
        title=title_from_tile(tile),
        price_rub=cast("float | None", price),
        old_price_rub=cast("float | None", old_price),
        rating=rating,
        orders_count=orders,
        sku_id=tile.get("sku_id"),
        url=tile.get("url"),
    )


async def _cdp_render_search(url: str, ctx: Context | None) -> dict[str, Any]:
    """Open the search URL in the operator's Chrome and extract tiles."""
    async with _cdp_lock:
        await _polite_wait()
        async with open_page(url, wait_ms=10000) as page:
            raw = await asyncio.wait_for(page.evaluate(_SEARCH_EXTRACT_JS), timeout=30.0)
    return _unwrap_extract(raw, "search")


def _unwrap_extract(raw: Any, what: str) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BODY_BYTES:
        raise ToolError(TransportDownError("extracted page data missing or over the body cap"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError(ParserDriftError(f"non-JSON {what} extract; preview: {str(raw)[:200]}")) from exc
    if not isinstance(data, dict):
        raise ToolError(ParserDriftError(f"{what} extractor returned a non-object payload"))
    return data


async def _cdp_card(url: str, ctx: Context | None) -> dict[str, Any]:
    """Open the item in a NEW TAB from the loaded landing page and read its DOM.

    Direct navigation to item pages is challenged even in a warm profile; the
    in-page open is not (measured 2026-08-20). The landing page also inherits
    nothing from the caller: the URL is rebuilt from SITE_BASE here.
    """
    item_id = _extract_item_id(url) or ""
    async with _cdp_lock:
        await _polite_wait()
        async with open_page(_LANDING, wait_ms=9000) as page:
            opener = (
                "() => { try { window.open("
                + json.dumps(url)
                + ", '_blank', 'noopener,noreferrer'); return 'opened'; } "
                "catch (e) { return 'error:' + String(e); } }"
            )
            opened = await asyncio.wait_for(page.evaluate(opener), timeout=10.0)
            if not isinstance(opened, str) or "opened" not in opened:
                raise ToolError(TransportDownError(f"could not open the item tab ({opened})"))
            # The sibling-tab walk needs the Playwright-managed page handle; the
            # raw-CDP fallback in mcp_core deliberately exposes only url/evaluate.
            # On that fallback the two-hop card read cannot work, and the honest
            # answer is a transport error, never a broken half-read.
            context = getattr(page, "context", None)
            if context is None:
                raise ToolError(
                    TransportDownError(
                        "aliexpress_card needs the Playwright-managed CDP page (tab-hopping); "
                        "the raw-CDP fallback carries no page context. Run with the default transport."
                    )
                )
            before = set(context.pages)
            wants = f"/item/{item_id}"
            target = None
            # The tab must (a) be one THIS call opened and (b) carry the requested
            # item id. Grabbing the nearest /item/ tab would silently answer with
            # another product's card — a wrong price under a right id — and then
            # close an operator's own tab.
            for _ in range(20):
                await asyncio.sleep(1)
                for candidate in list(context.pages):
                    if candidate in before:
                        continue
                    page_url = candidate.url or ""
                    if wants in page_url and "punish" not in page_url:
                        target = candidate
                        break
                if target is not None:
                    break
            if target is None:
                # The tab was never delivered, or was closed under us. This is a
                # transport outcome, not a proven challenge: record_refusal would
                # poison the pacer's backoff for a refusal that never happened.
                raise ToolError(
                    TransportDownError(
                        "the item tab never materialised (popup blocked, closed, or challenged); "
                        "open aliexpress.ru in the scraping profile and retry — this is not a parser failure"
                    )
                )
            try:
                raw = None
                # The sticky price module is client-rendered and lags the title:
                # keep polling for gyph-attached prices; a title-only state is
                # only accepted after half the budget, so a lazy module does not
                # masquerade as a permanent price_missing (measured 2026-08-20).
                for attempt in range(12):
                    try:
                        raw = await asyncio.wait_for(target.evaluate(_CARD_EXTRACT_JS), timeout=30.0)
                        probe = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {}
                        if probe.get("punish"):
                            break
                        if (probe.get("price_texts") or {}).get("attached"):
                            break
                        if attempt >= 6 and probe.get("title"):
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                if raw is None:
                    raise ToolError(TransportDownError("item tab produced no extractable content"))
                data = _unwrap_extract(raw, "card")
                if _is_punish(data):
                    _pacer.record_refusal()
                    raise ToolError(_challenge_error("the item tab opened onto an x5sec challenge"))
                return data
            finally:
                # Close the tab this call opened, matched or not, so a failed
                # call cannot farm orphaned item tabs (the leak itself triggers
                # x5sec). The scraping profile is dedicated to these fetches, so
                # sweeping the freshly-created set is the safe bound.
                try:
                    await target.close()
                except Exception:
                    pass
                for candidate in list(context.pages):
                    if candidate in before:
                        continue
                    if "/item/" in (candidate.url or ""):
                        try:
                            await candidate.close()
                        except Exception:
                            pass


def _card_prices(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """(price, old_price) from the sticky-offer module, symmetric with tiles.

    The sticky module was measured current-first, but the pairing does not
    trust order: the smallest non-junk glyph-attached number is the price, the
    largest above it is the strikethrough, and a jelly pair is dropped — the
    same rule as _tile_prices, so a reordered sticky cannot ship the base as
    the current (the exact "plausible and wrong" failure the search side
    guards against).
    """
    texts = payload.get("price_texts") if isinstance(payload.get("price_texts"), dict) else {}
    attached_raw = (
        texts.get("attached") if isinstance(texts, dict) and isinstance(texts.get("attached"), list) else None
    )
    regular, _coupon = _scored_prices(attached_raw)
    if not regular:
        return None, None
    price = min(regular)
    above = [candidate for candidate in regular if candidate > price]
    old_price = max(above) if above else None
    if old_price is not None and old_price <= price:
        old_price = None
    return price, old_price


# -------------------------------------------------------------------- tools ----


@mcp.tool(
    name="aliexpress_search",
    annotations=ToolAnnotations(
        title="AliExpress Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def aliexpress_search(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Search text, e.g. 'умные часы'")],
    ctx: Context | None = None,
) -> AliSearchResponse:
    """Search AliExpress, rendered in the operator's Chrome.

    ## Return Format

    AliSearchResponse: {status, query, tier_used, count, items[], meta}.
    price_rub is None when absent — never 0.

    ## Error Format

    ToolError: TransportDownError on x5sec challenges or CDP failures (with the
    manual-check hint inline); ParserDriftError when a rendered page yields zero
    product tiles.
    """
    log_event("aliexpress_search.start", query=query[:60])
    try:
        url = f"{SEARCH_URL}?SearchText={urllib.parse.quote(query.strip())}"
        cached = _cache.get(url)
        if cached is not None:
            payload, tier = cached, "cache"
        else:
            try:
                payload = await _cdp_render_search(url, ctx)
            except NavBlocked as exc:
                raise_tool_error(TransportDownError(f"AliExpress navigation blocked (HTTP {exc.status})."))
            if _is_punish(payload):
                _pacer.record_refusal()
                raise_tool_error(_challenge_error())
            tier = "cdp"
        items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
        if not items_raw:
            raise_tool_error(
                ParserDriftError(
                    "rendered search page yielded zero product tiles — either the query matched nothing "
                    "or the DOM shape moved; verify manually"
                )
            )
        # Persist only validated payloads: caching a transitively empty render
        # would block self-healing retries for the whole cache TTL.
        _cache.set(url, payload)
        items = []
        coupon_seen = 0
        for t in items_raw:
            if not isinstance(t, dict):
                continue
            price, old_price, coupon = _tile_prices(t)
            if coupon is not None:
                coupon_seen += 1
            items.append(_item_from_payload(t, price=price, old_price=old_price))
        warnings: list[str] = []
        if items and all(it.price_rub is None for it in items):
            warnings.append("no_prices_on_page")
        if len(items) >= 48:
            warnings.append("truncated_at_48: the grid exceeds the 48-tile cap — refine the query to see the rest")
        if coupon_seen:
            warnings.append(
                f"{coupon_seen} tile(s) advertise a coupon price; price_rub carries the regular "
                f"price and the coupon value is not part of the response — treat 'cheapest' ranking with care"
            )
        result = AliSearchResponse(query=query, tier_used=tier, count=len(items), items=items)
        attached = R.attach_meta(
            result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="aliexpress_search"
        )
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("aliexpress_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"aliexpress_search failed: {exc}")))


@mcp.tool(
    name="aliexpress_card",
    annotations=ToolAnnotations(
        title="AliExpress Product Card",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def aliexpress_card(
    item_id_or_url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=400,
            description="AliExpress item id (9-16 digits) or aliexpress.ru item URL, e.g. /item/1005010003103368.html",
        ),
    ],
    ctx: Context | None = None,
) -> AliCardResponse:
    """Fetch one AliExpress product card, rendered in the operator's Chrome.

    Review TEXTS are deliberately not exposed: they require navigating the
    review tab, which x5sec challenges; this tool returns the rating and the
    order count instead.

    ## Return Format

    AliCardResponse: {status, item_id, title, price_rub, old_price_rub, rating,
    orders_count, url, tier_used, meta}.

    ## Error Format

    ToolError: BadRequestError when no item id can be extracted;
    TransportDownError on x5sec challenges or CDP failures; ParserDriftError
    when a rendered card has neither title nor price.
    """
    log_event("aliexpress_card.start", input=item_id_or_url[:80])
    try:
        item_id = _extract_item_id(item_id_or_url)
        if item_id is None:
            raise_tool_error(
                BadRequestError(
                    f"could not extract an item id from {item_id_or_url!r}; pass a 9-16 digit id or an "
                    f"aliexpress.ru URL like /item/1005010003103368.html"
                )
            )
        # Rebuilt from SITE_BASE: the page opens in the operator's own Chrome,
        # so the target host must come from us, never from the argument.
        url = f"{SITE_BASE}/item/{item_id}.html"
        cached = _cache.get(url)
        if cached is not None:
            payload, tier = cached, "cache"
        else:
            try:
                payload = await _cdp_card(url, ctx)
            except NavBlocked as exc:
                raise_tool_error(TransportDownError(f"AliExpress navigation blocked (HTTP {exc.status})."))
            if _is_punish(payload):
                _pacer.record_refusal()
                raise_tool_error(_challenge_error())
            tier = "cdp"
            _cache.set(url, payload)
        title = R.flatten_text(payload.get("title"))
        price, old_price = _card_prices(payload)
        rating = R.coerce_rating(payload.get("rating_text"))
        # Orders arrive from the SEO description block only: the extractor
        # emits meta_description, and there is no dedicated orders field in the
        # sticky/card modules measured so far.
        orders: int | None = None
        meta_desc = payload.get("meta_description")
        if orders is None and isinstance(meta_desc, str):
            match = re.search(r"➜\s*([\d\s\u00a0]+)\s+заказов", meta_desc)
            if match:
                orders = R.coerce_int(match.group(1).replace("\u00a0", " ").replace(" ", ""))
        if rating is None and isinstance(meta_desc, str):
            match = re.search(r"Рейтинг\s*-\s*(\d[.,]\d)", meta_desc)
            if match:
                rating = R.coerce_rating(match.group(1))
        if title is None and price is None:
            raise_tool_error(
                ParserDriftError("rendered card has neither title nor price — the card shape moved; verify manually")
            )
        warnings: list[str] = []
        if price is None:
            warnings.append(
                "price_missing: the price module did not render — treat availability of this field as unknown"
            )
        if orders is None:
            warnings.append(
                "orders_count_missing: the SEO block it is read from was absent — drift-prone, not necessarily zero"
            )
        result = AliCardResponse(
            item_id=item_id,
            title=title,
            price_rub=price,
            old_price_rub=old_price,
            rating=rating,
            orders_count=orders,
            url=url,
            tier_used=tier,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="aliexpress_card")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("aliexpress_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"aliexpress_card failed: {exc}")))


# CLI-only drift canary: ``marketplace-mcp doctor`` imports and calls
# aliexpress_selfcheck() directly. It is deliberately NOT registered as an MCP
# tool — selfchecks are operator diagnostics, and their input/output schemas
# would be billed in every client request.
async def aliexpress_selfcheck(ctx: Context | None = None) -> AliSelfcheckResponse:
    """Structural drift canary for AliExpress (tri-state). Renders one live
    search and one card in the operator's Chrome and checks they parse.

    An x5sec challenge or CDP-down is ``inconclusive`` (transport), NEVER drift.
    Only a rendered page that yields zero tiles / an unparseable card is
    ``drift``. From a machine whose Chrome has not passed the challenge,
    inconclusive is the expected verdict.

    ## Return Format

    AliSelfcheckResponse: {status, healthy, connector, checks, ...}.

    ## Error Format

    Raises ToolError (TransportDownError) ONLY on an unexpected internal bug
    that prevents the canary from producing any verdict. Transport/block
    failures of individual sub-checks map to inconclusive entries, not errors.
    """
    log_event("aliexpress_selfcheck.start")
    try:
        result = await _aliexpress_selfcheck_impl(ctx)
        log_event("aliexpress_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("aliexpress_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"aliexpress_selfcheck failed: {exc}")))


async def _aliexpress_selfcheck_impl(ctx: Context | None) -> AliSelfcheckResponse:
    checks: dict[str, dict] = {}
    baseline = "cdp-search-shape-v1"
    url = f"{SEARCH_URL}?SearchText={urllib.parse.quote('умные часы')}"
    items_raw: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(90):
            payload = await _cdp_render_search(url, ctx)
    except TimeoutError:
        checks["search"] = R.selfcheck_entry(
            "inconclusive", baseline=baseline, reason="timeout", notes=["render exceeded 90s"]
        )
    except ToolError as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive",
            baseline=baseline,
            reason="blocked" if "challenge" in str(exc) else "transport_down",
            notes=[str(exc)[:160]],
        )
    except Exception as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive",
            baseline=baseline,
            reason="transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )
    else:
        if _is_punish(payload):
            checks["search"] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="blocked",
                notes=["x5sec challenge page rendered instead of the grid"],
            )
        else:
            items_raw = [t for t in (payload.get("items") or []) if isinstance(t, dict)]
            priced = [t for t in items_raw if (t.get("price_texts") or {}).get("attached")]
            totals = items_raw and [t for t in items_raw if t.get("orders") is not None]
            if not items_raw:
                checks["search"] = R.selfcheck_entry(
                    "drift", baseline=baseline, reason="parse_smoke_failed", notes=["zero tiles"]
                )
            elif len(items_raw) < 3 or not priced:
                checks["search"] = R.selfcheck_entry(
                    "drift",
                    baseline=baseline,
                    reason="parse_smoke_failed",
                    notes=[
                        f"{len(items_raw)} tiles but {'no prices attach' if not priced else 'fewer than 3'} — the shape likely moved"
                    ],
                )
            else:
                notes = [
                    f"{len(items_raw)} tiles extracted",
                    f"{len(priced)} tiles with an attached price"
                    + (f", {len(totals)} with an orders line" if totals else ""),
                ]
                checks["search"] = R.selfcheck_entry("healthy", baseline=baseline, notes=notes)

    # Chain the card probe off the search result so the canary never depends on
    # a hardcoded SKU that may have been delisted.
    card_checked = False
    search_state = checks.get("search") or {}
    first_id = next((t.get("item_id") for t in items_raw if t.get("item_id")), None)
    if first_id and search_state.get("state") == "healthy":
        card_checked = True
        try:
            async with asyncio.timeout(90):
                card = await _cdp_card(f"{SITE_BASE}/item/{first_id}.html", ctx)
        except ToolError as exc:
            checks["card"] = R.selfcheck_entry(
                "inconclusive",
                baseline="cdp-card-shape-v1",
                reason="blocked" if "challenge" in str(exc) else "transport_down",
                notes=[str(exc)[:160]],
            )
        except Exception as exc:
            checks["card"] = R.selfcheck_entry(
                "inconclusive",
                baseline="cdp-card-shape-v1",
                reason="transport_down",
                notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
            )
        else:
            price, _old = _card_prices(card)
            if _is_punish(card):
                checks["card"] = R.selfcheck_entry(
                    "inconclusive",
                    baseline="cdp-card-shape-v1",
                    reason="blocked",
                    notes=["the item tab opened onto an x5sec challenge"],
                )
            elif card.get("title") and price is not None:
                checks["card"] = R.selfcheck_entry(
                    "healthy", baseline="cdp-card-shape-v1", notes=[f"card {first_id} parsed with a price"]
                )
            elif card.get("title"):
                checks["card"] = R.selfcheck_entry(
                    "inconclusive",
                    baseline="cdp-card-shape-v1",
                    reason="price_missing",
                    notes=[
                        "card rendered but the price module stayed empty even after the extended poll — "
                        "measured on live sessions this is x5sec load-shedding, not a selector break "
                        "(megamarket precedent: session-shaped empties are inconclusive, never drift)",
                    ],
                )
            else:
                checks["card"] = R.selfcheck_entry(
                    "drift",
                    baseline="cdp-card-shape-v1",
                    reason="parse_smoke_failed",
                    notes=["card rendered but neither title nor price parsed"],
                )
    elif not card_checked:
        checks["card"] = R.selfcheck_entry(
            "inconclusive",
            baseline="cdp-card-shape-v1",
            reason="skipped_no_search_health",
            notes=["card probe skipped because the search gate is not healthy"],
        )

    result_dict = R.selfcheck_result(
        "aliexpress",
        checks,
        required=("search",),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return AliSelfcheckResponse(**result_dict)


# Advertised output schemas are the dominant constant cost of an MCP mount:
# replace the full Pydantic tree with top-level field names (~64 % fewer
# wire tokens on the unified server).
apply_compact_output_schemas(mcp)
