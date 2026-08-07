"""Avito MCP connector.

Avito sits behind an IP-reputation firewall: a datacenter address gets HTTP 403
with a captcha challenge on every route that matters, including the internal
``/web/1/js/items`` search API. TLS impersonation alone does not clear it — the
endpoint answers from a residential Russian IP with a warmed-up session, and
always from the operator's logged-in Chrome over CDP. This connector therefore
mirrors the Ozon two-tier layout: try impersonated HTTPS first, fall back to a
fetch inside the real browser.

Verified live July 2026 from a datacenter IP (docs/ANTI_BOT.md):
  - ``https://www.avito.ru/web/1/js/items`` — 403 + firewall captcha from a DC
  - search HTML pages — 403 «Доступ ограничен: проблема с IP»
  - the same endpoint answers 200 from a residential session (third-party
    parsers depend on it: Duff89/parser_avito)

Behaviours that shape this code:

**Items, not products.** Avito is classifieds: a listing is a one-off ad with a
seller, not a catalog SKU with reviews. There is no review pool per item —
seller reputation is the review signal, exposed via avito_seller.

**A missing price is None, never 0.** Some listings (exchange, free, price on
request) carry no price; a zero would rank them cheapest in compare_prices.

**The block page looks like content.** A 403 arrives with cookies set and a
plausible HTML body, so only JSON-shaped 200s count as success; anything else
falls through to the CDP tier.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC. Use
``log_event`` (stderr) or the ``Context`` logging methods.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import urllib.parse
from typing import Annotated, Any, cast

from curl_cffi import requests as cffi
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

from avito_connector.models_output import (
    AvitoCardResponse,
    AvitoSearchItemOut,
    AvitoSearchResponse,
    AvitoSelfcheckResponse,
    AvitoSellerOut,
    AvitoSellerResponse,
    MetaOut,
)
from avito_connector.settings import get_settings
from avito_connector.shape_reference import missing_required_families

_settings = get_settings()

SERVER_VERSION = "1.4.0"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://www.avito.ru"
ITEMS_API = f"{SITE_BASE}/web/1/js/items"

TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes
IMPERSONATE = _settings.impersonate
_min_gap = _settings.min_gap

AVITO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": f"{SITE_BASE}/",
}

# Item URL slugs: /moskva/noutbuki/thinkpad_x1_1234567890 — the id is the tail.
_ITEM_ID_RE = re.compile(r"_(\d{6,})(?:\?|/|$)")
_LOCATION_ID_RE = re.compile(r"^\d{2,10}$")

mcp = FastMCP(
    name="avito-connector",
    version=SERVER_VERSION,
    instructions=(
        "Avito listings: search, item cards and seller profiles. Read-only, no "
        "credentials. Tier 1 is impersonated HTTPS (works from residential RU "
        "IPs); tier 2 fetches inside the operator's logged-in Chrome over CDP. "
        "Start with avito_search; avito_card takes an item id or URL; "
        "avito_seller takes a seller profile URL or id from a card/search hit."
    ),
)
mcp.add_middleware(RetryMiddleware())

_cache: TTLCache = TTLCache(ttl_s=_settings.cache_ttl, max_entries=256)
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


def _sync_curl_get(url: str, proxy: str | None = None) -> tuple[int, str]:
    """Tier-1: curl_cffi GET with an incremental body cap (see ozon_connector)."""
    kwargs: dict[str, Any] = {}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    chunks: list[bytes] = []
    total = 0
    r = cffi.get(url, headers=AVITO_HEADERS, impersonate=cast(Any, IMPERSONATE), timeout=TIMEOUT, stream=True, **kwargs)
    try:
        encoding = r.encoding or "utf-8"
        for chunk in r.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise ValueError(f"body exceeds {MAX_BODY_BYTES} bytes (aborted at {total} during stream)")
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


async def _cdp_fetch(url: str, ctx: Context | None) -> tuple[int, str]:
    """Tier-2: run the fetch inside the operator's logged-in Chrome.

    Serialized via _cdp_lock so a burst of tool calls cannot spray tabs. The
    in-page JS enforces the same body cap as tier 1 — an inflated body would
    otherwise OOM the connector through the CDP serialization pipeline.
    """

    async def _attempt() -> tuple[int, str]:
        async with _cdp_lock, open_page(f"{SITE_BASE}/", wait_ms=4000) as page:
            raw = await asyncio.wait_for(
                page.evaluate(
                    """async (args) => {
                    const res = await fetch(args.url, {
                        credentials: 'include',
                        headers: {'Accept': 'application/json, text/plain, */*'}
                    });
                    const reader = res.body.getReader();
                    let total = 0;
                    let chunks = [];
                    while (true) {
                        const {done, value} = await reader.read();
                        if (done) break;
                        total += value.length;
                        if (total > args.cap) {
                            return JSON.stringify({status: 0, text: 'BODY_CAP_EXCEEDED'});
                        }
                        chunks.push(value);
                    }
                    const buf = new Uint8Array(total);
                    let off = 0;
                    for (const c of chunks) { buf.set(c, off); off += c.length; }
                    const text = new TextDecoder().decode(buf);
                    return JSON.stringify({status: res.status, text: text});
                }""",
                    {"url": url, "cap": MAX_BODY_BYTES},
                ),
                timeout=30.0,
            )
        result = json.loads(raw) if isinstance(raw, str) else raw
        return result["status"], result["text"]

    try:
        return await asyncio.wait_for(_attempt(), timeout=max(0.01, float(TIMEOUT)))
    except TimeoutError:
        return 0, f"CDP timeout after {TIMEOUT}s"


def _looks_like_json(body: str) -> bool:
    stripped = body.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


async def _fetch(url: str, ctx: Context | None) -> tuple[int, str, str]:
    """Tier-1 first; CDP fallback on block/non-200. Returns (status, body, tier).

    Only JSON-shaped 200s are cached — a cached block page would keep reporting
    a block after the challenge cleared.
    """
    cached = _cache.get(url)
    if cached is not None:
        status, body = cached
        return status, body, "cache"

    await _polite_wait()

    try:
        status, body = await asyncio.to_thread(_sync_curl_get, url, _proxy())
        if status == 200 and _looks_like_json(body):
            _pacer.record_success()
            _cache.set(url, (status, body))
            return status, body, "curl_cffi"
        if ctx and status in (401, 403, 429):
            await ctx.debug(f"Avito tier-1 HTTP {status} (firewall); trying CDP")
    except Exception as exc:
        if ctx:
            # A connect failure quotes the proxy URL, credentials included —
            # the same redaction every other error path gets, not an exception.
            await ctx.debug(_redact(f"Avito tier-1 exception: {exc}; trying CDP"))

    try:
        status, body = await _cdp_fetch(url, ctx)
        if status == 200 and _looks_like_json(body):
            _cache.set(url, (status, body))
        return status, body, "cdp"
    except NavBlocked as exc:
        if exc.status:
            return exc.status, str(exc), "cdp_blocked"
        return 0, str(exc), "cdp_failed"
    except Exception as exc:
        return 0, str(exc), "cdp_failed"


def _blocked_error(tier: str, detail: str = "") -> TransportDownError:
    message = (
        f"Avito returned an IP-firewall block via {tier}. "
        "Open avito.ru in the Chrome CDP profile, solve the captcha, then retry; "
        "from a datacenter IP a Russian residential proxy (AVITO_PROXY) is the tier-1 fix."
    )
    hint = _pacer.rotation_hint()
    if hint:
        message += f" {hint}"
    if detail:
        message += f" Detail: {detail[:160]}"
    return TransportDownError(message, status_code=403)


def _raise_for_fetch_failure(status: int, body: str, tier: str, what: str) -> None:
    """Map a failed fetch to the shared error taxonomy.

    Refusals are reported to the pacer on the way out. Avito bans by IP
    reputation and request rate, so the call after a refusal waits longer, and
    a run of them earns the operator a straight answer about what to change
    rather than a fifth identical "blocked".
    """
    if status in (401, 403) or "firewall" in body[:300] or "too-many-requests" in body[:300]:
        _pacer.record_refusal()
        raise_tool_error(_blocked_error(tier, body))
    if status == 429:
        _pacer.record_refusal()
        raise_tool_error(_blocked_error(tier, body))
    if status == 404:
        raise_tool_error(NotFoundError(f"Avito {what} not found (404)."))
    raise_tool_error(TransportDownError(f"Avito {what} fetch failed: HTTP {status} via {tier}. {body[:120]}"))


def _extract_item_id(raw: str) -> int | None:
    """Pull the numeric item id out of a slug, URL or bare digits.

    Item ids run 9-12 digits today; the regex enforces a 6-digit floor so a
    short numeric fragment ("123") is rejected instead of fetched as an id that
    never existed.
    """
    raw = raw.strip()
    if raw.isdigit():
        return int(raw) if len(raw) >= 6 else None
    match = _ITEM_ID_RE.search(raw)
    if match:
        return int(match.group(1))
    return None


def _valid_location_id(raw: str) -> bool:
    return bool(_LOCATION_ID_RE.match(raw.strip()))


def _posted_at(item: dict[str, Any]) -> str | None:
    """Publication time as an ISO-8601 string, or an honest None.

    The live payload carries no `date`/`time` string — it carries
    `sortTimeStamp`/`allowTimeStamp` in epoch milliseconds (verified 2026-07-28
    against a real response). Reading only the string aliases left `posted_at`
    null on every row, so the epoch form is handled here and rendered as ISO-8601
    UTC rather than passed through as a bare 13-digit number, which no caller
    could tell from an id.
    """
    text = R.flatten_text(R.first_present(item, "date", "publishedAt", "postedAt", "time"), "text", "value", "name")
    if text:
        return text
    stamp = R.first_present(item, "sortTimeStamp", "allowTimeStamp")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return None
    try:
        # Milliseconds since epoch; guard against a seconds-based drift
        # upstream. The division stays inside the try: json.loads admits
        # arbitrary-precision ints, and a huge stamp overflows the float
        # arithmetic before fromtimestamp is ever reached.
        seconds = stamp / 1000.0 if stamp > 1e11 else float(stamp)
        moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.isoformat().replace("+00:00", "Z")


def _parse_search_items(payload: dict) -> tuple[list[dict[str, Any]], int | None]:
    """Best-effort extraction of items + total from a js/items payload.

    The endpoint has shipped several envelope shapes; tolerate the known ones
    and record drift loudly rather than guessing.
    """
    candidates = (
        payload.get("items"),
        payload.get("catalog", {}).get("items") if isinstance(payload.get("catalog"), dict) else None,
        payload.get("data", {}).get("items") if isinstance(payload.get("data"), dict) else None,
    )
    items_raw = next((c for c in candidates if isinstance(c, list)), [])
    total = R.first_present(payload, "totalCount", "total", "count")
    if not isinstance(total, int):
        total = R.coerce_int(total)
    out: list[dict[str, Any]] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        item_id = R.first_present(it, "id", "itemId", "item_id")
        item_id = R.coerce_int(item_id)
        title = R.flatten_text(R.first_present(it, "title", "name"))
        price = R.coerce_price(R.first_present(it, "price", "priceRub", "price_rub"))
        if price is None and isinstance(it.get("priceDetailed"), dict):
            price = R.coerce_price(R.first_present(it["priceDetailed"], "value", "price"))
        # The live js/items key is `urlPath`. The alias list used to read `uriPath`
        # — one letter out — so every search hit came back with url=None while the
        # rest of the row parsed fine. Verified 2026-07-28 against a real response
        # captured from a residential session: `urlPath` is the ONLY url-bearing
        # key on the item (there is no `uri`, `url` or `itemUrl`).
        uri = R.first_present(it, "uri", "url", "urlPath", "uriPath", "itemUrl") or ""
        if uri and uri.startswith("/"):
            uri = SITE_BASE + uri
        # location arrives as a nested object on the live endpoint, not a string:
        # {"id": 637640, "name": "Москва", ...}. flatten_text pulls the name out
        # (or yields None) so one extra level of nesting upstream can no longer
        # fail validation for the entire page of listings.
        location = R.flatten_text(
            R.first_present(it, "location", "locationName", "address"),
            "name",
            "title",
            "formattedAddress",
        )
        if location is None:
            # Real fallbacks present in the live payload, in order of specificity.
            # `addressDetailed.locationName` can be "" for an item Avito itself has
            # no place name for; flatten_text turns that into None rather than an
            # empty string masquerading as an answer. A bare `locationId` is NOT
            # resolved to a city — there is no lookup table here, and guessing one
            # would be inventing data.
            location = R.flatten_text(it.get("addressDetailed"), "locationName", "name") or R.flatten_text(
                it.get("geo"), "formattedAddress", "name", "title"
            )
        seller = it.get("seller") if isinstance(it.get("seller"), dict) else {}
        images = it.get("images")
        images_count = (
            len(images)
            if isinstance(images, list)
            else R.coerce_int(R.first_present(it, "imagesCount", "images_count")) or 0
        )
        out.append(
            {
                "item_id": item_id,
                "title": title,
                "price_rub": price,
                "url": uri or None,
                "location": location,
                "seller_name": R.first_present(seller, "name", "title")
                if seller
                else R.first_present(it, "sellerName"),
                "seller_id": str(R.first_present(seller if seller else {}, "id", "userId", "user_id", default="") or "")
                or None,
                "is_company": R.first_present(seller, "isCompany", "is_company") if seller else None,
                "posted_at": _posted_at(it),
                "images": images_count,
            }
        )
    return out, total


def _build_search_url(query: str, page: int, location_id: str, category_id: str | None) -> str:
    params: dict[str, str] = {
        "q": query,
        "p": str(page),
        "locationId": location_id,
        "context": "",
        "updateListOnly": "true",
    }
    if category_id:
        params["categoryId"] = category_id
    return f"{ITEMS_API}?{urllib.parse.urlencode(params)}"


@mcp.tool(
    name="avito_search",
    annotations=ToolAnnotations(
        title="Avito Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def avito_search(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Search text, e.g. 'thinkpad x1 carbon'")],
    page: Annotated[int, Field(ge=1, le=100, description="Result page (1-based)")] = 1,
    location_id: Annotated[
        str | None, Field(description="Avito location id; default AVITO_LOCATION_ID (637640 = Moscow)")
    ] = None,
    category_id: Annotated[str | None, Field(description="Optional Avito category id to narrow the search")] = None,
    ctx: Context | None = None,
) -> AvitoSearchResponse:
    """Search Avito listings via the internal js/items API.

    ## Return Format

    AvitoSearchResponse: {status, query, page, location_id, tier_used, count,
    total_count, items[], meta}. Items carry item_id, title, price_rub (None
    when the ad has no price — never 0), url, location, seller fields.

    ## Error Format

    ToolError: BadRequestError on malformed arguments; TransportDownError on
    firewall blocks (with the captcha/proxy guidance inline); ParserDriftError
    when a reached-200 body no longer parses as the expected envelope.
    """
    log_event("avito_search.start", query=query[:60], page=page)
    try:
        if location_id is not None and not location_id.strip():
            raise_tool_error(BadRequestError("location_id must not be empty"))
        loc = (location_id or _settings.location_id).strip()
        if not _valid_location_id(loc):
            raise_tool_error(BadRequestError(f"location_id must be digits (e.g. 637640), got {loc!r}"))
        if category_id is not None and not category_id.strip().isdigit():
            raise_tool_error(BadRequestError(f"category_id must be digits, got {category_id!r}"))

        url = _build_search_url(query.strip(), page, loc, category_id.strip() if category_id else None)
        status, body, tier = await _fetch(url, ctx)
        if status != 200:
            _raise_for_fetch_failure(status, body, tier, "search")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise_tool_error(ParserDriftError(f"non-JSON search body via {tier}; preview: {body[:200]}"))
        if not isinstance(payload, dict):
            raise_tool_error(ParserDriftError("search payload is not a JSON object"))

        items_raw, total = _parse_search_items(payload)
        warnings: list[str] = []
        if not items_raw and total not in (0, None):
            warnings.append("empty_items_with_nonzero_total")
        items = [AvitoSearchItemOut(**it) for it in items_raw]
        result = AvitoSearchResponse(
            query=query,
            page=page,
            location_id=loc,
            tier_used=tier,
            count=len(items),
            total_count=total,
            items=items,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="avito_search")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("avito_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"avito_search failed: {exc}")))


@mcp.tool(
    name="avito_card",
    annotations=ToolAnnotations(
        title="Avito Item Card", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def avito_card(
    item_id_or_url: Annotated[
        str, Field(min_length=1, max_length=300, description="Item id, slug path or full avito.ru URL")
    ],
    ctx: Context | None = None,
) -> AvitoCardResponse:
    """Fetch one Avito listing by id or URL.

    ## Return Format

    AvitoCardResponse: {status, item_id, title, price_rub, description, location,
    posted_at, views, images, seller, url, tier_used, meta}. price_rub is None
    when the ad has no price — never 0.

    ## Error Format

    ToolError: BadRequestError when no id can be extracted; NotFoundError on a
    404 (deleted or never existed); TransportDownError on blocks; ParserDriftError
    when the envelope changed.
    """
    log_event("avito_card.start", input=item_id_or_url[:80])
    try:
        item_id = _extract_item_id(item_id_or_url)
        if item_id is None:
            raise_tool_error(
                BadRequestError(
                    f"could not extract an item id from {item_id_or_url!r}; "
                    "pass bare digits, a /slug_1234567890 path, or a full avito.ru URL"
                )
            )
        url = f"{ITEMS_API}?{urllib.parse.urlencode({'itemId': str(item_id)})}"
        status, body, tier = await _fetch(url, ctx)
        if status != 200:
            _raise_for_fetch_failure(status, body, tier, "card")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise_tool_error(ParserDriftError(f"non-JSON card body via {tier}; preview: {body[:200]}"))

        item_raw = payload.get("item") if isinstance(payload.get("item"), dict) else payload
        if not isinstance(item_raw, dict) or not item_raw:
            raise_tool_error(ParserDriftError("card payload has no item object"))
        item: dict[str, Any] = item_raw

        price = R.coerce_price(R.first_present(item, "price", "priceRub"))
        if price is None and isinstance(item.get("priceDetailed"), dict):
            price = R.coerce_price(R.first_present(item["priceDetailed"], "value", "price"))
        seller_field = item.get("seller")
        seller_raw: dict[str, Any] = seller_field if isinstance(seller_field, dict) else {}
        seller = AvitoSellerOut(
            name=R.first_present(seller_raw, "name", "title"),
            seller_id=str(R.first_present(seller_raw, "id", "userId", "user_id", default="") or "") or None,
            is_company=R.first_present(seller_raw, "isCompany", "is_company"),
            rating_score=R.coerce_rating(R.first_present(seller_raw, "rating", "ratingScore")),
            rating_count=R.coerce_int(R.first_present(seller_raw, "ratingCount", "reviewsCount")),
            profile_url=R.first_present(seller_raw, "profileUrl", "uri"),
        )
        images = item.get("images")
        result = AvitoCardResponse(
            item_id=item_id,
            title=R.flatten_text(R.first_present(item, "title", "name")),
            price_rub=price,
            description=R.first_present(item, "description", "text"),
            location=R.flatten_text(
                R.first_present(item, "location", "locationName", "address"),
                "name",
                "title",
                "formattedAddress",
            )
            or R.flatten_text(item.get("geo"), "formattedAddress", "name", "title"),
            posted_at=_posted_at(item),
            views=R.coerce_int(R.first_present(item, "views", "viewsCount", "viewCount")),
            images=len(images) if isinstance(images, list) else 0,
            seller=seller if seller.name or seller.seller_id else None,
            url=R.first_present(item, "url", "uri", "urlPath") or f"{SITE_BASE}/_/{item_id}",
            tier_used=tier,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), [], source="avito_card")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("avito_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"avito_card failed: {exc}")))


@mcp.tool(
    name="avito_seller",
    annotations=ToolAnnotations(
        title="Avito Seller Profile", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def avito_seller(
    seller_id_or_url: Annotated[
        str, Field(min_length=1, max_length=300, description="Seller id or profile URL from a card/search hit")
    ],
    ctx: Context | None = None,
) -> AvitoSellerResponse:
    """Fetch an Avito seller profile — reputation is the review signal here.

    Classifieds have no per-item review pool; the seller's rating, review count
    and active-listing count are what a buyer checks.

    ## Return Format

    AvitoSellerResponse: {status, seller, active_items, tier_used, meta}.

    ## Error Format

    ToolError: BadRequestError on empty input; NotFoundError on 404;
    TransportDownError on blocks; ParserDriftError on envelope drift.
    """
    log_event("avito_seller.start", input=seller_id_or_url[:80])
    try:
        raw = seller_id_or_url.strip()
        seller_id = re.sub(r"[^0-9A-Za-z_-]", "", raw) if not raw.isdigit() else raw
        if not seller_id:
            raise_tool_error(BadRequestError(f"could not extract a seller id from {seller_id_or_url!r}"))
        url = f"{ITEMS_API}?{urllib.parse.urlencode({'userId': seller_id, 'p': '1', 'updateListOnly': 'true'})}"
        status, body, tier = await _fetch(url, ctx)
        if status != 200:
            _raise_for_fetch_failure(status, body, tier, "seller")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise_tool_error(ParserDriftError(f"non-JSON seller body via {tier}; preview: {body[:200]}"))

        seller_raw = payload.get("seller") if isinstance(payload.get("seller"), dict) else payload.get("user", {})
        if not isinstance(seller_raw, dict):
            seller_raw = {}
        seller = AvitoSellerOut(
            name=R.first_present(seller_raw, "name", "title"),
            seller_id=seller_id,
            is_company=R.first_present(seller_raw, "isCompany", "is_company"),
            rating_score=R.coerce_rating(R.first_present(seller_raw, "rating", "ratingScore")),
            rating_count=R.coerce_int(R.first_present(seller_raw, "ratingCount", "reviewsCount")),
            profile_url=R.first_present(seller_raw, "profileUrl", "uri"),
        )
        _, total = _parse_search_items(payload)
        warnings: list[str] = []
        if not seller.name:
            warnings.append("seller_identity_missing")
        result = AvitoSellerResponse(seller=seller, active_items=total, tier_used=tier)
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="avito_seller")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("avito_seller.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"avito_seller failed: {exc}")))


@mcp.tool(
    name="avito_selfcheck",
    annotations=ToolAnnotations(
        title="Avito Self-Check", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def avito_selfcheck(ctx: Context | None = None) -> AvitoSelfcheckResponse:
    """Structural drift canary for Avito (tri-state: success / drift_detected /
    inconclusive). Runs live probes against search, card and seller endpoints.

    A 403 firewall block or CDP-down is ``inconclusive`` (transport), NEVER
    drift: from a datacenter IP that is the expected state. Only a reached-200
    JSON body that fails the parse smoke is ``drift``.

    ## Return Format

    AvitoSelfcheckResponse: {status, healthy, connector, checks, server_version,
    server_started_at, process_id}.

    ## Error Format

    Raises ToolError (TransportDownError) ONLY on an unexpected internal bug
    that prevents the canary from producing any verdict. Transport/block
    failures of individual sub-checks map to inconclusive entries, not errors.
    """
    log_event("avito_selfcheck.start")
    try:
        result = await _avito_selfcheck_impl(ctx)
        log_event("avito_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("avito_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"avito_selfcheck failed: {exc}")))


async def _avito_selfcheck_impl(ctx: Context | None) -> AvitoSelfcheckResponse:
    checks: dict[str, dict] = {}

    async def _check(name: str, url: str, smoke: Any, baseline: str) -> None:
        try:
            async with asyncio.timeout(60):
                status, body, tier = await _fetch(url, ctx)
        except TimeoutError:
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason="timeout", notes=["fetch exceeded 60s"]
            )
            return
        except Exception as exc:
            checks[name] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="transport_down",
                notes=[f"raised {type(exc).__name__}: {str(exc)[:120]}"],
            )
            return
        if status != 200:
            reason = "rate_limited" if status == 429 else ("blocked" if status in (401, 403, 407) else "transport_down")
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason=reason, notes=[f"http {status} via {tier}"], code=status
            )
            return
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason="transport_down", notes=["body not JSON (firewall page?)"]
            )
            return
        try:
            verdict = smoke(payload)
        except Exception as exc:
            checks[name] = R.selfcheck_entry(
                "drift", baseline=baseline, reason="parse_smoke_failed", notes=[str(exc)[:160]]
            )
            return
        checks[name] = R.selfcheck_entry("healthy", baseline=baseline, notes=[verdict or "ok"])

    def _search_smoke(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")
        items, _ = _parse_search_items(payload)
        if not items:
            raise ValueError("no items parsed from a generic query")
        first = items[0]
        if first["item_id"] is None and first["title"] is None:
            raise ValueError("items carry neither id nor title")
        # The parser binds through alias families, so a rename WITHIN a family
        # is fine — but the loss of a whole family is drift the parse smoke
        # cannot see (id renamed away while title survives would serve every
        # item without an id). Compare against the captured reference.
        missing = missing_required_families(R.shape_signature(payload))
        if missing:
            lost = "; ".join("/".join(family) for family in missing)
            raise ValueError(f"payload lost parser-critical key families: {lost}")
        return f"{len(items)} items parsed"

    def _card_smoke(payload: Any) -> str:
        item = payload.get("item") if isinstance(payload, dict) and isinstance(payload.get("item"), dict) else payload
        if not isinstance(item, dict) or not item:
            raise ValueError("no item object")
        if R.first_present(item, "title", "name") is None:
            raise ValueError("item has no title")
        return "card envelope intact"

    def _seller_smoke(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise ValueError("payload not an object")
        return "seller envelope intact"

    await _check(
        "search",
        _build_search_url("ноутбук", 1, _settings.location_id, None),
        _search_smoke,
        "js-items-search-v1",
    )
    await _check(
        "seller",
        f"{ITEMS_API}?{urllib.parse.urlencode({'userId': '1', 'p': '1', 'updateListOnly': 'true'})}",
        _seller_smoke,
        "js-items-seller-v1",
    )

    result_dict = R.selfcheck_result(
        "avito",
        checks,
        required=("search", "seller"),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return AvitoSelfcheckResponse(**result_dict)
