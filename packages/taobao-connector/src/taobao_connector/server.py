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

_settings = get_settings()

SERVER_VERSION = "1.2.0"
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
_SEARCH_EXTRACT_JS = """
() => {
    const out = [];
    const anchors = document.querySelectorAll('a[href*="item.taobao.com"], a[href*="//item.taobao.com"]');
    const seen = new Set();
    for (const a of anchors) {
        const href = a.href || '';
        const m = href.match(/[?&]id=(\\d{9,13})/);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);
        const card = a.closest('[class*="Card"], [class*="card"], [class*="item"], li, div') || a;
        const text = (card.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
        const title = (a.innerText || '').trim() || text[0] || null;
        let price = null;
        const priceEl = card.querySelector('[class*="price"], [class*="Price"]');
        if (priceEl) {
            const pm = (priceEl.innerText || '').replace(/[^0-9.]/g, '');
            if (pm) price = parseFloat(pm);
        }
        if (price === null) {
            for (const line of text) {
                const pm = line.match(/^[¥￥]?\\s*([0-9]+(?:\\.[0-9]+)?)$/);
                if (pm) { price = parseFloat(pm[1]); break; }
            }
        }
        let shop = null, location = null, sales = null;
        for (const line of text) {
            if (/人付款|人收货|付款$/.test(line)) sales = sales || line;
            else if (/店$/.test(line)) shop = shop || line;
        }
        out.push({item_id: m[1], title, price_cny: price, shop_name: shop, location, sales, url: href.split('?')[0] + '?id=' + m[1]});
        if (out.length >= 48) break;
    }
    return JSON.stringify({items: out, title: document.title || ''});
}
"""

_CARD_EXTRACT_JS = """
() => {
    const text = (document.body.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
    const titleEl = document.querySelector('h1, [class*="title"], [class*="Title"]');
    const title = titleEl ? (titleEl.innerText || '').trim() : (document.title || null);
    let price = null;
    const priceEl = document.querySelector('[class*="price"], [class*="Price"]');
    if (priceEl) {
        const pm = (priceEl.innerText || '').replace(/[^0-9.]/g, '');
        if (pm) price = parseFloat(pm);
    }
    let shop = null, sales = null;
    for (const line of text) {
        if (/人付款|人收货|已售/.test(line)) sales = sales || line;
        if (/店$/.test(line) && !shop) shop = line;
    }
    const imgs = document.querySelectorAll('[class*="desc"] img, [class*="Desc"] img, #description img');
    return JSON.stringify({title, price_cny: price, shop_name: shop, sales, description_images: imgs.length, page_title: document.title || ''});
}
"""


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
        items = [TaobaoSearchItemOut(**it) for it in items_raw if isinstance(it, dict)]
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
    hides it or prices by variant — never 0.

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
        price = payload.get("price_cny")
        if price is not None:
            price = R.coerce_price(price)
        if title is None and price is None:
            raise_tool_error(
                ParserDriftError(
                    "rendered card has neither title nor price — the item page shape moved; verify manually"
                )
            )
        result = TaobaoCardResponse(
            item_id=item_id,
            title=title,
            price_cny=price,
            shop_name=payload.get("shop_name"),
            sales=payload.get("sales"),
            description_images=int(payload.get("description_images") or 0),
            url=url,
            tier_used=tier,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), [], source="taobao_card")
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
                checks["search"] = R.selfcheck_entry(
                    "healthy", baseline=baseline, notes=[f"{len(items_raw)} items extracted"]
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
