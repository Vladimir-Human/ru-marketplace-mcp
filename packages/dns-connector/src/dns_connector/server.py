"""DNS-Shop MCP connector.

DNS sits behind Qrator's JavaScript proof-of-work: every dynamic page answers
HTTP 401 with a ~349 KB challenge script, and the one route not behind it
(``/ajax-state/product-buy/``) wants a CSRF token you can only get from a page
that *is* behind it. No header trick clears a proof-of-work — but a real
Chrome executes it natively, so every read here runs in the operator's browser
over CDP and parses the rendered DOM.

Verified live July 2026 from a datacenter IP (docs/ANTI_BOT.md):
  - every dynamic page — 401 + Qrator challenge
  - ``restapi.dns-shop.ru`` — 403 even from residential (internal SSR address)
  - anonymously reachable: ``robots.txt``, ``sitemap.xml`` (~600k product URLs)

The CDP route needs verification from the operator's machine: the DOM tile
selectors below are written against the public catalog layout, and only a
challenge-passed session can confirm them. Run dns_selfcheck from your Chrome
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
from mcp_core.dom import JS_HELPERS
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

from dns_connector.models_output import (
    DnsCardResponse,
    DnsSearchItemOut,
    DnsSearchResponse,
    DnsSelfcheckResponse,
    MetaOut,
)
from dns_connector.settings import get_settings
from dns_connector.shape_reference import SEARCH_REQUIRED_KEYS, SEARCH_SHAPE_REFERENCE

_settings = get_settings()

SERVER_VERSION = "1.4.1"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://www.dns-shop.ru"
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
    name="dns-connector",
    version=SERVER_VERSION,
    instructions=(
        "DNS-Shop electronics catalog: search and product cards. Every read "
        "runs in the operator's own Chrome over CDP — Qrator's proof-of-work "
        "blocks all anonymous access. Start with dns_search; dns_card takes a "
        "product id or dns-shop.ru product URL."
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
    """Pull the product id out of a dns-shop.ru product URL or a bare id.

    Host-checked on purpose. The id is the only thing that survives this
    function, and the card URL is rebuilt from SITE_BASE, so a caller cannot
    smuggle ``/product/<id>`` into a path on someone else's domain and
    steer the operator's logged-in Chrome there. Anything that is not
    dns-shop.ru, or carries a scheme we do not navigate, returns None.
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
        if host != "dns-shop.ru" and not host.endswith(".dns-shop.ru"):
            return None
        raw = parts.path
    elif raw.startswith("//"):
        # Scheme-relative: the authority is someone else's, so treat it as off-host.
        return None
    match = _PRODUCT_ID_RE.search(raw)
    return match.group(1).lower() if match else None


# Why this extractor returns TEXT and not numbers
# ------------------------------------------------
# Audit 2026-07-28, against the live grid (query «ноутбук», Moscow):
#
#   1. It keyed on `a[href*="/product/"]` and walked up with
#      `closest('[class*="catalog-product"], ..., li, div')`. `closest()` tests
#      THE ELEMENT ITSELF before any ancestor, and the first product anchor in a
#      DNS tile is the image link, whose own class is
#      `catalog-product__image-link` — which matches `[class*="catalog-product"]`.
#      So the "card" resolved to that anchor, not to the tile. Measured on the
#      captured grid: the resolved node has textContent length 0, the real tile
#      root has 402. Every tile therefore produced title=null and price=null
#      while the page showed 48 perfectly good products.
#      The tile ROOT is the single BEM block `.catalog-product`; the inner nodes
#      are `catalog-product__*`, which is why an exact class match finds the root
#      and a substring match cannot distinguish them. Tiles are now selected
#      directly and never inferred by walking up from an anchor.
#
#   2. Price was `Math.min` over lines matching /руб|₽/. DNS tiles carry
#      "от 5 751 ₽/ мес." next to a 58 999 ₽ price, so the minimum is the
#      monthly instalment. That is worse than null: it validates, it looks
#      plausible, and it is wrong. Confirmed on the live tile for HUAWEI
#      MateBook D 16 (58 999 ₽ shown, 5 751 ₽/мес instalment).
#
#   3. The strikethrough price lives in `.product-buy__prev` and carries NO
#      currency glyph ("54 999"), so the /руб|₽/ filter could never see it.
#
# So the JS no longer does arithmetic. It returns the raw display strings from
# named elements and lets mcp_core.resilience.coerce_price parse them, because
# that helper already handles non-breaking spaces, a missing glyph and comma
# decimals — and refuses an ambiguous multi-number blob instead of concatenating
# it into nonsense.
#
# It also reads textContent rather than innerText. innerText depends on layout
# and CSS visibility, which is exactly the kind of thing that differs between a
# warm tab and a freshly navigated one; textContent is stable and is what the
# fixture test can assert against.
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

    // The tile root, resolved without closest(). Exact-match the BEM block so
    // `catalog-product__image` cannot masquerade as the tile.
    const tiles = [];
    for (const doc of docs) {
        for (const t of doc.querySelectorAll('.catalog-product')) tiles.push(t);
    }
    // Fallback for a renamed grid: take each product anchor's OUTERMOST ancestor
    // that still holds exactly one product link, which is the tile by definition.
    if (!tiles.length) {
        const seenTile = new Set();
        for (const doc of docs) {
            for (const a of doc.querySelectorAll('a[href*="/product/"]')) {
                let node = a, best = a;
                for (let hop = 0; hop < 8 && node.parentElement; hop++) {
                    node = node.parentElement;
                    const links = node.querySelectorAll('a[href*="/product/"]');
                    const ids = new Set();
                    for (const l of links) {
                        const mm = (l.href || '').match(/\\/product\\/([A-Za-z0-9_-]+)/);
                        if (mm) ids.add(mm[1]);
                    }
                    if (ids.size > 1) break;
                    best = node;
                }
                if (!seenTile.has(best)) { seenTile.add(best); tiles.push(best); }
            }
        }
    }

    const out = [];
    const seen = new Set();
    for (const tile of tiles) {
        // Prefer the NAME link: the image link carries no text.
        const nameEl = tile.querySelector('a.catalog-product__name')
            || tile.querySelector('a[href*="/product/"][title]')
            || tile.querySelector('a[href*="/product/"]');
        if (!nameEl) continue;
        const href = nameEl.href || '';
        const m = href.match(/\\/product\\/([A-Za-z0-9_-]+)/);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);

        // Drop the bracketed short-spec blob from the title; keep the product name.
        const title = cleanTextWithout(nameEl, ['.catalog-product__short-specs'])
            || nameEl.getAttribute('title')
            || null;

        // Current price: the price node minus the strikethrough child and minus
        // the instalment sub-line. `.product-buy__sub` is the "от N ₽/ мес." decoy.
        const priceEl = tile.querySelector('.product-buy__price')
            || tile.querySelector('[class*="product-buy__price"]');
        const priceText = cleanTextWithout(priceEl, ['.product-buy__prev', '.product-buy__sub', '.product-buy__hint']);
        const oldPriceText = cleanText(tile.querySelector('.product-buy__prev'));
        const availText = cleanText(tile.querySelector('.available')) || cleanText(tile.querySelector('[class*="available"]'));

        out.push({
            product_id: m[1],
            title: title,
            price_text: priceText,
            old_price_text: oldPriceText,
            availability_text: availText,
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
    // h1 is the product name on a card page; the generic [class*="title"] fallback
    // could otherwise pick a section heading.
    const titleEl = document.querySelector('h1')
        || document.querySelector('[class*="product-card-top__title"]');
    const title = cleanText(titleEl) || document.title || null;

    // Named price nodes, not a scan of body text: the body carries recommended
    // products, instalment offers and bonus amounts, any of which is smaller than
    // the real price and would win a Math.min.
    const priceEl = document.querySelector('.product-buy__price')
        || document.querySelector('[class*="product-buy__price"]');
    const priceText = cleanTextWithout(priceEl, ['.product-buy__prev', '.product-buy__sub', '.product-buy__hint']);
    const oldPriceText = cleanText(document.querySelector('.product-buy__prev'));

    const availEl = document.querySelector('.order-avail-wrap .available')
        || document.querySelector('.available');
    const availText = cleanText(availEl);
    // Only decide availability from an explicit signal. A page whose stock block
    // has not rendered must read "unknown" (null), not "in stock".
    let available = null;
    if (availText) {
        available = !/Нет в наличии|Под заказ|Закончился/i.test(availText);
    } else if (document.body) {
        // Fallback scan of body text. textContent, not innerText: innerText
        // depends on layout visibility, is unavailable in jsdom, and this
        // fallback exists so the DOM fixture can assert on it.
        const body = (document.body.textContent || '').replace(/\\s+/g, ' ');


        if (/Нет в наличии|Под заказ|Закончился/i.test(body)) available = false;
        else if (/В наличии/i.test(body)) available = true;
    }




    return JSON.stringify({
        title: title,
        price_text: priceText,
        old_price_text: oldPriceText,
        availability_text: availText,
        is_available: available,
        page_title: document.title || ''
    });
}
"""

# Shared with every other CDP source through mcp_core.dom, so a fix to tile
# resolution or price selection lands on all of them at once instead of being
# copied four times and drifting three ways.
_SEARCH_EXTRACT_JS = _SEARCH_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)
_CARD_EXTRACT_JS = _CARD_EXTRACT_TEMPLATE.replace("//__SHARED_HELPERS__", JS_HELPERS)


def _search_item_from_tile(tile: dict[str, Any]) -> DnsSearchItemOut:
    """Map one extracted tile onto the wire shape, parsing prices in Python.

    Accepts BOTH shapes on purpose. The extractor now returns ``price_text`` /
    ``old_price_text``, but a payload cached by an older build (or a fixture
    written against it) still carries numeric ``price_rub``, and a cache entry
    outliving a deploy must not start answering with nulls. The wire shape is
    unchanged either way: ``price_rub`` is a float or None, never 0.
    """
    price = R.price_from_texts(tile.get("price_text"), tile.get("price_rub"))
    old_price = R.price_from_texts(tile.get("old_price_text"), tile.get("old_price_rub"))
    # A strikethrough below the current price is a drifted read, not a discount:
    # report the pair only when it is internally consistent.
    if price is not None and old_price is not None and old_price <= price:
        old_price = None
    return DnsSearchItemOut(
        product_id=tile.get("product_id"),
        title=R.flatten_text(tile.get("title")),
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
    name="dns_search",
    annotations=ToolAnnotations(
        title="DNS-Shop Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def dns_search(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Search text, e.g. 'ноутбук lenovo'")],
    ctx: Context | None = None,
) -> DnsSearchResponse:
    """Search DNS-Shop, rendered in the operator's Chrome.

    ## Return Format

    DnsSearchResponse: {status, query, tier_used, count, items[], meta}.
    price_rub is None when absent — never 0.

    ## Error Format

    ToolError: TransportDownError on CDP/Qrator failures; ParserDriftError when
    a rendered page yields zero product tiles.
    """
    log_event("dns_search.start", query=query[:60])
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
                        f"DNS navigation blocked (HTTP {exc.status}). The Qrator challenge must pass in the scraping-profile Chrome — open dns-shop.ru there once, then retry."
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
        if items and all(it.price_rub is None for it in items):
            warnings.append("no_prices_on_page")
        result = DnsSearchResponse(query=query, tier_used=tier, count=len(items), items=items)
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="dns_search")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("dns_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"dns_search failed: {exc}")))


@mcp.tool(
    name="dns_card",
    annotations=ToolAnnotations(
        title="DNS-Shop Product Card", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def dns_card(
    product_url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=400,
            description="dns-shop.ru product URL containing /product/<id>/, e.g. /product/b7a1667f9b19ed20/",
        ),
    ],
    ctx: Context | None = None,
) -> DnsCardResponse:
    """Fetch one DNS-Shop product card.

    ## Return Format

    DnsCardResponse: {status, product_id, title, price_rub, old_price_rub,
    is_available, url, tier_used, meta}.

    ## Error Format

    ToolError: BadRequestError when the URL carries no product id;
    TransportDownError on CDP/Qrator failures; ParserDriftError when a rendered
    card has neither title nor price.
    """
    log_event("dns_card.start", input=product_url[:80])
    try:
        product_id = _extract_product_id(product_url)
        if product_id is None:
            raise_tool_error(
                BadRequestError(
                    f"could not extract a product id from {product_url!r}; pass a dns-shop.ru URL with /product/<id>/ "
                    f"(the id is a 16-hex string, e.g. /product/b7a1667f9b19ed20/)"
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
                raise_tool_error(TransportDownError(f"DNS navigation blocked (HTTP {exc.status})."))
            tier = "cdp"
            _cache.set(url, payload)
        title = R.flatten_text(payload.get("title"))
        # price_text is what the current extractor returns; price_rub keeps a
        # cache entry written by an older build readable.
        price = R.price_from_texts(payload.get("price_text"), payload.get("price_rub"))
        old_price = R.price_from_texts(payload.get("old_price_text"), payload.get("old_price_rub"))
        if price is not None and old_price is not None and old_price <= price:
            old_price = None
        if title is None and price is None:
            raise_tool_error(
                ParserDriftError("rendered card has neither title nor price — the card shape moved; verify manually")
            )
        result = DnsCardResponse(
            product_id=product_id,
            title=title,
            price_rub=price,
            old_price_rub=old_price,
            is_available=payload.get("is_available"),
            url=url,
            tier_used=tier,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), [], source="dns_card")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("dns_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"dns_card failed: {exc}")))


@mcp.tool(
    name="dns_selfcheck",
    annotations=ToolAnnotations(
        title="DNS-Shop Self-Check", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def dns_selfcheck(ctx: Context | None = None) -> DnsSelfcheckResponse:
    """Structural drift canary for DNS-Shop (tri-state). Renders one live search
    page in the operator's Chrome and checks tiles extract.

    Qrator-blocked or CDP-down is ``inconclusive`` (transport), NEVER drift.
    Only a rendered page that yields zero tiles is ``drift``.

    ## Return Format

    DnsSelfcheckResponse: {status, healthy, connector, checks, ...}.

    ## Error Format

    Raises ToolError (TransportDownError) ONLY on an unexpected internal bug
    that prevents the canary from producing any verdict. Transport/block
    failures of individual sub-checks map to inconclusive entries, not errors.
    """
    log_event("dns_selfcheck.start")
    try:
        result = await _dns_selfcheck_impl(ctx)
        log_event("dns_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("dns_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"dns_selfcheck failed: {exc}")))


async def _dns_selfcheck_impl(ctx: Context | None) -> DnsSelfcheckResponse:
    checks: dict[str, dict] = {}
    baseline = "cdp-search-shape-v1"
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
            # Tiles extract — now ask the second question: did the SHAPE move?
            # The registry was measured on a captured page; a live payload that
            # loses a parser-critical path is structural drift even while tiles
            # still come back. Types may vary with the data (str/null), so the
            # required check is on key presence, not on the full signature.
            live_signature = R.shape_signature(payload)
            drift = R.diff_keys(SEARCH_SHAPE_REFERENCE, live_signature)
            missing_required = [
                key for key in SEARCH_REQUIRED_KEYS if not any(entry.startswith(f"{key}:") for entry in live_signature)
            ]
            if missing_required:
                checks["search"] = R.selfcheck_entry(
                    "drift",
                    baseline=baseline,
                    reason="shape_drift",
                    notes=[
                        f"{len(items_raw)} tiles extracted",
                        f"required paths missing: {', '.join(missing_required)}",
                    ],
                    shape_missing=drift["missing"],
                    shape_added=drift["added"],
                )
            else:
                notes = [f"{len(items_raw)} tiles extracted", "shape matches the captured reference"]
                if drift["added"]:
                    notes.append(f"{len(drift['added'])} new paths vs baseline (informational)")
                checks["search"] = R.selfcheck_entry(
                    "healthy", baseline=baseline, notes=notes, shape_added=drift["added"]
                )
        else:
            checks["search"] = R.selfcheck_entry(
                "drift", baseline=baseline, reason="parse_smoke_failed", notes=["zero tiles"]
            )

    result_dict = R.selfcheck_result(
        "dns",
        checks,
        required=("search",),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return DnsSelfcheckResponse(**result_dict)
