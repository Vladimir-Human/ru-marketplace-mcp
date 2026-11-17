"""Avito MCP connector.

Avito sits behind an IP-reputation firewall: a datacenter address gets HTTP 403
with a captcha challenge on every route that matters, including the internal
``/web/1/js/items`` search API. TLS impersonation alone does not clear it — the
endpoint answers from a residential Russian IP with a warmed-up session, and
always from the operator's logged-in Chrome over CDP. This connector therefore
uses the Ozon two-tier layout for search: try impersonated HTTPS first,
fall back to a fetch inside the real browser. Cards and sellers read rendered
pages over paced, host-allowlisted CDP.

Verified live July 2026 from a datacenter IP (docs/ANTI_BOT.md):
  - ``https://www.avito.ru/web/1/js/items`` — 403 + firewall captcha from a DC
  - search HTML pages — 403 «Доступ ограничен: проблема с IP»
  - the same endpoint answers 200 from a residential session (third-party
    parsers depend on it: Duff89/parser_avito)

Verified live 2026-10-12 (captured item-page fixture + provenance):
  - ``/web/1/js/items`` still serves search, but ignores ``itemId`` and answers
    a generic catalog envelope, not an item object.
  - it also ignores ``userId``: id=1 and a nonsense id return the same catalog
    totalCount=1500, not a seller's active-listing count.
  - cards read the item page's schema.org Product JSON-LD and seller DOM block
    over CDP (``open_page`` + ``page.content()``), never the catalog as an item.
  - seller profiles expose title, score and review count in HTML, no JSON-LD
    or active-listing total; active_items is None. The canary chains search,
    card and its seller profile rather than accepting any JSON object.

Behaviours that shape this code:

**Items, not products.** Avito is classifieds: a listing is a one-off ad with a
seller, not a catalog SKU with reviews. There is no review pool per item —
seller reputation is the review signal, exposed via avito_seller.

**A missing price is None, never 0.** Some listings (exchange, free, price on
request) carry no price; a zero would rank them cheapest in compare_prices.

**Some shop pages omit JSON-LD.** The captured 2026-10-13 B2C page carries
item-view DOM markers instead. Cards use them only if no Product exists, with
a matching page item id and an explicit rouble price (or None).

**The block page looks like content.** A 403 arrives with cookies set and a
plausible HTML body, so API reads accept only JSON-shaped 200s before CDP
fallback. Card navigation refusals are classified before parsing; a page with
neither Product nor usable item DOM is drift, never an empty successful card.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC. Use
``log_event`` (stderr).
"""
# START_MODULE_CONTRACT
# PURPOSE: Expose Avito listing search, item cards and seller reputation.
# SCOPE: Read-only HTTP/CDP search with API-error/schema guards; paced allowlisted CDP pages; Product-first cards with identity-checked DOM fallback; nullable data; chained canary.
# DEPENDS: FastMCP; curl_cffi; stdlib HTMLParser; mcp_core CDP/cache/pacing/errors/kit; models_output; settings; shape_reference.
# LINKS: R-NO-PERSONAL-DATA; R-NO-MCP-LOGGING; M-AVITO; M-KIT; M-COMPARE; M-MARKETPLACE; R-CACHE-VALIDATED; R-SELFCHECK-GATES-PRICE; packages/avito-connector/tests; M-SOURCES; R-NAV-HOSTS.invariant.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# parse.py: item pages, seller blocks/profiles and search envelopes (pure parsing, moved out of this file).
# _fetch: Try JSON-shaped HTTP results before operator-browser fallback for search.
# _fetch_item_page: Read paced, serialized, host-allowlisted rendered HTML over CDP.
# _check_rendered_page: Reject transport/challenge pages before HTML parsing.
# _raise_for_fetch_failure: Classify firewall, missing-page and transport failures.
# _extract_item_id: Resolve numeric handles or Avito listing URLs.
# _build_search_url: Encode query, page and validated location/category identifiers.
# avito_search: Read paginated classifieds search; per-hit availability from isReserved; reserved hits kept unless
#               include_inactive=false (then counted and announced in _meta); warn when a required key family vanishes.
# avito_card: Read item-page Product (or identity-checked item DOM) and seller block over CDP; full URLs only with an item-page path; never use a catalog envelope;
#             availability (active/reserved/unknown) from hydration flags and the safe-deal banner only (item_state);
#             a sale discount from the card state, checked against the page price; one that does not add up is None.
# _attach_photos: Opt-in photos=N: https *.img.avito.st fetches through the source's pacer, bounded, stop at the first
#                 refusal, nothing stored.
# avito_seller: Read CDP profile reputation; active_items stays None (not shown).
# avito_selfcheck: Chain search/card/seller to diagnose transport/parser health; not an MCP tool.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
import re
import urllib.parse
from typing import Annotated, Any

from curl_cffi import requests as cffi
from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent, ToolAnnotations
from mcp_core import resilience as R
from mcp_core.cache import TTLCache
from mcp_core.errors import (
    BadRequestError,
    ChallengeRequiredError,
    ConnectorError,
    NotFoundError,
    ParserDriftError,
    RateLimitedError,
    ToolError,
    TransportDownError,
    raise_tool_error,
    raise_tool_error_from,
)
from mcp_core.kit import blocked_error, polite_wait, sync_curl_get, url_on_hosts
from mcp_core.logging import log_event
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.pacing import Pacer
from mcp_core.redact import redact_error_text as _redact
from mcp_core.resources import register_reference_resources
from mcp_core.sources import BY_NAME
from mcp_core.transport.chrome_cdp import NavBlocked, open_page
from pydantic import Field

from avito_connector import item_state as _state
from avito_connector.models_output import (
    AvitoCardResponse,
    AvitoMetroOut,
    AvitoParamOut,
    AvitoPhotoOut,
    AvitoSearchItemOut,
    AvitoSearchResponse,
    AvitoSelfcheckResponse,
    AvitoSellerOut,
    AvitoSellerResponse,
    MetaOut,
)
from avito_connector.parse import (
    ALLOWED_HOSTS,
    SITE_BASE,
    _ItemPageHTML,
    _parse_search_items,
    _posted_at,  # noqa: F401 - re-exported, imported from server by tests
    _seller_profile_url,
    parse_item_page,
    parse_item_seller,
    parse_seller_profile,
    seller_handle,
)
from avito_connector.settings import get_settings
from avito_connector.shape_reference import SEARCH_REQUIRED_FAMILIES, missing_required_families

_settings = get_settings()

SERVER_VERSION = "2.9.4"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

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
# Paths a full URL may keep: an item-page shape only — /<id>, /<slug>_<id> or
# /<city>/<category>/<slug>_<id>, the shapes of every captured item URL and
# fixture. Every segment is a plain ASCII slug ([A-Za-z0-9_-]): that is what
# every captured Avito item path uses, and anything else — a one-segment
# personal prefix with an item-like tail (/profile/x_1234567890), a path
# continuing past the id, a non-ASCII segment, a backslash or a percent escape
# (a separator to the browser, plain text to a wider regex) — never reaches
# the operator's logged-in browser.
_SLUG_CHARS = r"[A-Za-z0-9_-]"
_ITEM_URL_PATH_RE = re.compile(rf"/(?:(?:{_SLUG_CHARS}+/){{2}})?(?:{_SLUG_CHARS}+_)?(\d{{6,}})/?")
# The /<city>/<category>/ shape cannot tell a city slug from a route prefix by
# form alone, so first segments that are Avito's own personal or service routes
# are refused outright: an item-shaped tail under them is not an item page.
_NON_ITEM_ROOTS = frozenset(
    {
        "account",
        "api",
        "basket",
        "cart",
        "checkout",
        "dashboard",
        "favorites",
        "messenger",
        "my",
        "notifications",
        "orders",
        "payments",
        "personal",
        "profile",
        "settings",
        "user",
        "users",
        "web",
    }
)
_LOCATION_ID_RE = re.compile(r"^\d{2,10}$")

mcp = FastMCP(
    name="avito-connector",
    version=SERVER_VERSION,
    instructions=(
        "Avito listings: search, item cards and seller profiles. Read-only, no "
        "credentials. Search tries impersonated HTTPS first (works from "
        "residential RU IPs), then the operator's logged-in Chrome over CDP. "
        "Cards and seller profiles always read rendered pages in that Chrome. "
        "Start with avito_search; avito_card takes an item id or URL and returns "
        "the seller block when present. avito_seller takes an Avito /brands/<id> "
        "or /user/<id>/profile URL, a bare id or avito_card's seller_id. "
        "active_items is unavailable (None): /web/1/js/items ignores userId. "
        "Every tool's full field list and error codes are MCP resources (`guide://<source>/<tool>`); all error codes together are `marketplace://errors`."
    ),
)

_cache: TTLCache = TTLCache(ttl_s=_settings.cache_ttl, max_entries=256)
_pacer = Pacer(_min_gap)
_cdp_lock = asyncio.Lock()


def _proxy() -> str | None:
    return _settings.proxy.get_secret_value() or None


async def _polite_wait() -> None:
    """Space this source's requests out, and back off if it refused us.

    Reads ``_min_gap`` at call time so an operator or a test can retune the
    pace without rebuilding the pacer. The pacing itself is shared
    (mcp_core.kit.polite_wait); this module-level name stays because the tools
    call it and the tests patch it.
    """
    await polite_wait(_pacer, _min_gap)


def _sync_curl_get(url: str, proxy: str | None = None) -> tuple[int, str]:
    """Tier-1: curl_cffi GET with an incremental body cap (see ozon_connector)."""
    return sync_curl_get(
        url,
        get=cffi.get,
        headers=AVITO_HEADERS,
        impersonate=IMPERSONATE,
        timeout=TIMEOUT,
        max_body_bytes=MAX_BODY_BYTES,
        proxy=proxy,
    )


async def _cdp_fetch(url: str, ctx: Context | None) -> tuple[int, str]:
    """Tier-2: run the fetch inside the operator's logged-in Chrome.

    Serialized via _cdp_lock so a burst of tool calls cannot spray tabs. The
    in-page JS enforces the same body cap as tier 1 — an inflated body would
    otherwise OOM the connector through the CDP serialization pipeline.
    """

    async def _attempt() -> tuple[int, str]:
        async with _cdp_lock, open_page(f"{SITE_BASE}/", wait_ms=4000, allowed_hosts=ALLOWED_HOSTS) as page:
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

    Cache entries come only from avito_search after parsing and validation;
    JSON-shaped HTTP 200 alone is not proof of a successful response.
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
            return status, body, "curl_cffi"
        if status in (401, 403, 429):
            log_event(f"Avito tier-1 HTTP {status} (firewall); trying CDP", level=logging.DEBUG, logger_name=__name__)
    except Exception as exc:
        # A connect failure quotes the proxy URL, credentials included —
        # the same redaction every other error path gets, not an exception.
        log_event(_redact(f"Avito tier-1 exception: {exc}; trying CDP"), level=logging.DEBUG, logger_name=__name__)

    try:
        status, body = await _cdp_fetch(url, ctx)
        return status, body, "cdp"
    except NavBlocked as exc:
        if exc.status:
            return exc.status, str(exc), "cdp_blocked"
        return 0, str(exc), "cdp_failed"
    except ToolError:
        # R-CDP-HOST-PAUSE: open_page already mapped the pause to a rate_limited
        # ToolError; swallowing it here would report a generic CDP failure.
        raise
    except Exception as exc:
        return 0, str(exc), "cdp_failed"


async def _fetch_item_page(url: str, ctx: Context | None) -> tuple[int, str, str]:
    """Read rendered HTML in a paced, serialized operator-Chrome tab.

    Shared by cards and seller profiles; no JSON cache or API fallback. Navigation refusals
    retain their status for _raise_for_fetch_failure and its refusal backoff.
    """
    await _polite_wait()

    async def _attempt() -> str:
        async with _cdp_lock, open_page(url, wait_ms=4000, allowed_hosts=ALLOWED_HOSTS) as page:
            content = getattr(page, "content", None)
            # Playwright exposes content(); the shared raw-CDP PageLike currently
            # only exposes evaluate(). Snapshot the same rendered DOM there,
            # without an API/network fetch or changes to the shared transport.
            body = (
                await content()
                if callable(content)
                else await page.evaluate("() => document.documentElement.outerHTML")
            )
            if not isinstance(body, str):
                raise ValueError("CDP item-page content is not text")
            if len(body.encode("utf-8")) > MAX_BODY_BYTES:
                raise ValueError(f"body exceeds {MAX_BODY_BYTES} bytes")
            return body

    try:
        body = await asyncio.wait_for(_attempt(), timeout=max(0.01, float(TIMEOUT)))
        return 200, body, "cdp"
    except NavBlocked as exc:
        return exc.status or 0, str(exc), "cdp"
    except TimeoutError:
        return 0, f"CDP timeout after {TIMEOUT}s", "cdp"
    except ToolError:
        # R-CDP-HOST-PAUSE: the host-pause error from open_page must reach the
        # tool boundary with its retry_after_s intact, not become a generic one.
        raise
    except Exception as exc:
        return 0, _redact(str(exc)), "cdp"


def _blocked_error(tier: str, detail: str = "") -> TransportDownError:
    """The refusal error, with the pacer's standing-block hint (mcp_core.kit)."""
    message = (
        f"Avito returned an IP-firewall block via {tier}. "
        "Open avito.ru in the Chrome CDP profile, solve the captcha, then retry; "
        "from a datacenter IP a Russian residential proxy (AVITO_PROXY) is the tier-1 fix."
    )
    return blocked_error(message, pacer=_pacer, detail=detail)


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


def _check_rendered_page(status: int, body: str, tier: str, what: str) -> None:
    """Classify rendered-page refusals before attempting either HTML parser."""
    if status != 200:
        _raise_for_fetch_failure(status, body, tier, what)
    document = _ItemPageHTML()
    document.feed(body)
    document.close()
    # Do not mistake firewall-related JS bundled into a valid item page for
    # a challenge; inspect visible text plus the existing short body prefix.
    preview = (body[:300] + " " + " ".join(document.text)).lower()
    if any(
        marker in preview
        for marker in (
            "firewall",
            "too-many-requests",
            "доступ ограничен",
            "доступ с вашего ip",
            "подтвердите, что вы не робот",
            "проверка, что вы не робот",
        )
    ):
        _raise_for_fetch_failure(403, body, tier, what)


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
        title="Avito Search", read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
    ),
)
async def avito_search(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Search text, e.g. 'thinkpad x1 carbon'")],
    page: Annotated[int, Field(ge=1, le=100, description="Result page (1-based)")] = 1,
    location_id: Annotated[
        str | None, Field(description="Avito location id; default AVITO_LOCATION_ID (637640 = Moscow)")
    ] = None,
    category_id: Annotated[str | None, Field(description="Optional Avito category id to narrow the search")] = None,
    include_inactive: Annotated[
        bool,
        Field(
            description=(
                "Keep reserved hits (availability=reserved), the default. false drops them from items and counts "
                "them in excluded_inactive; total_count still includes them"
            )
        ),
    ] = True,
    ctx: Context | None = None,
) -> AvitoSearchResponse:
    """Search Avito listings via the internal js/items API.

    price_rub is None for an ad without a price — never 0.
    Each hit carries availability from Avito's own isReserved flag; reserved
    hits are kept unless include_inactive=false.

    Full field list and error codes: `guide://avito/avito_search`.
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
        conflicts = sum(1 for it in items_raw if it.pop("price_conflict", False))
        if conflicts:
            warnings.append(
                f"price_conflict: {conflicts} hit(s) show an Avito discount that does not add up; their price_rub is None"
            )
        if not items_raw and total not in (0, None):
            warnings.append("empty_items_with_nonzero_total")
        if items_raw:
            # Per-offer invariants (a fabricated or unparseable price, a rating
            # off its scale, in_stock contradicting a missing price) are the
            # shared drift alarm; without this call they only fire in WB and
            # Ozon, while every other connector's fabricated price reaches the
            # agent as a plain field.
            warnings.extend(R.validate_items(items_raw, require_title=True, absent_price="no_prices_on_page"))
            # The selfcheck compares this payload against the captured family
            # registry; the tool's own answer now carries the same drift
            # signal, so an agent does not need a human running doctor to
            # learn the shape moved. An empty envelope stays out of the
            # check: with zero items every per-item family is vacuously
            # absent, and the empty-items warning above already covers it.
            drift = R.shape_drift_warning(payload, SEARCH_REQUIRED_FAMILIES, source="avito_search")
            if drift:
                warnings.append(drift)
        excluded = 0
        if not include_inactive:
            kept = [it for it in items_raw if it["availability"] not in _state.INACTIVE]
            excluded = len(items_raw) - len(kept)
            items_raw = kept
            if excluded:
                # Reservations on Avito often fall through: say what was hidden.
                warnings.append(
                    f"info: {excluded} reserved hit(s) dropped (include_inactive=false); total_count still counts them"
                )
        items = [AvitoSearchItemOut(**it) for it in items_raw]
        result = AvitoSearchResponse(
            excluded_inactive=excluded,
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
        if tier != "cache":
            _cache.set(url, (status, body))
        return result
    except ConnectorError as exc:
        if isinstance(exc, (RateLimitedError, ChallengeRequiredError)):
            _pacer.record_refusal()
        raise_tool_error(exc)
    except ToolError:
        raise
    except Exception as exc:
        log_event("avito_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error_from(exc, _redact(f"avito_search failed: {exc}"))


def _apply_sale(item: dict[str, Any], sale: dict[str, int] | None, conflict: str | None) -> list[str]:
    """Put Avito's sale discount on the card; a discount that does not add up leaves price_rub None.

    The page price read from Product or the DOM must be one of the two prices
    the card state names: the struck one (a Product offer can carry it) or the
    sale price the page shows.
    """
    if sale is not None and item.get("price_rub") not in (None, sale["price_rub"], sale["old_price_rub"]):
        conflict = f"page price {item.get('price_rub')} is neither {sale['price_rub']} nor {sale['old_price_rub']}"
    if conflict:
        item["price_rub"] = None
        return [f"price_conflict: {conflict}; price_rub is None"]
    if sale is not None:
        item["price_rub"] = float(sale["price_rub"])
        item["old_price_rub"] = float(sale["old_price_rub"])
        item["discount_percent"] = sale["discount_percent"]
    return []


def _apply_details(item: dict[str, Any], details: dict[str, Any]) -> None:
    """Merge page-state fields into the card; values already read from the card source win.

    Location privacy, fail-closed: the street address and map point are kept
    only when every seller-type source on the page that states a type (the
    page state's seller.isCompany and the DOM seller label) says company. A
    private seller, an unstated type, or sources that disagree get the city and
    nearby stations only.
    """
    for key in (
        "delivery_available",
        "delivery_services",
        "payment_on_delivery",
        "safe_deal",
        "price_offer_enabled",
        "bargain",
        "item_badges",
        "today_views",
    ):
        item[key] = details[key]
    item["condition"] = details["condition"] or item.get("condition")
    item["params"] = [AvitoParamOut(**p) for p in details["params"]]
    item["metro"] = [AvitoMetroOut(**m) for m in details["metro"]]
    for key in ("location", "posted_at", "views"):
        if item.get(key) is None:
            item[key] = details[key]
    seller = item.get("seller")
    stated = [
        value
        for value in (details["is_company"], seller.is_company if seller is not None else None)
        if value is not None
    ]
    company = bool(stated) and all(value is True for value in stated)
    if company:
        item["address"], item["coords"] = details["address"], details["coords"]
    extra = {k: v for k, v in details["seller"].items() if v}
    if seller is None and extra:
        item["seller"] = AvitoSellerOut(**extra)
    elif seller is not None:
        for key, value in extra.items():
            setattr(seller, key, value)


_MAX_PHOTOS = 3
_PHOTO_MAX_BYTES = 2 * 1024 * 1024
_PHOTO_TIMEOUT_S = 20.0
_PHOTO_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class _PhotoTooLarge(Exception):
    """The image is over _PHOTO_MAX_BYTES; the download was aborted."""


def _photo_magic_matches(content_type: str, data: bytes) -> bool:
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _sync_photo_get(url: str) -> tuple[int, str, bytes]:
    """One listing photo over the curl_cffi tier, streamed with a hard size cap."""
    if not url_on_hosts(url, BY_NAME["avito"].hosts) or not _state.photo_url_allowed(url):
        raise ValueError("disallowed photo URL")
    proxy = _proxy()
    kwargs: dict[str, Any] = {"proxies": {"http": proxy, "https": proxy}} if proxy else {}
    resp = cffi.get(url, impersonate=IMPERSONATE, timeout=15, stream=True, allow_redirects=False, **kwargs)
    try:
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if resp.status_code != 200:
            return resp.status_code, content_type, b""
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > _PHOTO_MAX_BYTES:
            raise _PhotoTooLarge
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > _PHOTO_MAX_BYTES:
                raise _PhotoTooLarge
            chunks.append(chunk)
        return resp.status_code, content_type, b"".join(chunks)
    finally:
        try:
            resp.close()
        except Exception:
            pass


async def _attach_photos(result: AvitoCardResponse, urls: list[str], limit: int) -> list[ImageContent]:
    """Fetch up to ``limit`` listing photos; stop at the first refusal, never retry.

    Only *.img.avito.st over https; each fetch waits on the source's own pacer
    (its gap and its back-off after a refusal), like every other Avito request,
    the first one included. Bytes stay in memory for this answer and are never
    written anywhere. photos_status names the outcome that ended the loop;
    fewer_on_page when every photo on the page arrived but there were fewer
    than asked.
    """
    images: list[ImageContent] = []
    wanted = max(0, min(limit, _MAX_PHOTOS))
    picked = [url for url in urls if _state.photo_url_allowed(url)][:wanted]
    if not picked:
        result.photos_status = "none_on_page"
        return images
    result.photos_status = "ok"
    for url in picked:
        entry = AvitoPhotoOut(url=url)
        result.photos.append(entry)
        await _polite_wait()
        try:
            status, content_type, data = await asyncio.wait_for(
                asyncio.to_thread(_sync_photo_get, url), timeout=_PHOTO_TIMEOUT_S
            )
        except _PhotoTooLarge:
            result.photos_status = "too_large"
            break
        except Exception:
            result.photos_status = "fetch_failed"
            break
        if status != 200:
            if status == 403:
                result.photos_status = "refused_403"
            elif status == 429:
                result.photos_status = "rate_limited"
            else:
                result.photos_status = "refused_http"
            if status in (403, 429):
                _pacer.record_refusal()
            break
        if content_type not in _PHOTO_TYPES or not data or not _photo_magic_matches(content_type, data):
            result.photos_status = "bad_type"
            break
        entry.delivered = True
        entry.bytes = len(data)
        images.append(ImageContent(type="image", data=base64.b64encode(data).decode("ascii"), mime_type=content_type))
    if result.photos_status == "ok" and len(picked) < wanted:
        result.photos_status = "fewer_on_page"
    return images


@mcp.tool(
    name="avito_card",
    annotations=ToolAnnotations(
        title="Avito Item Card", read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
    ),
)
async def avito_card(
    item_id_or_url: Annotated[
        str, Field(min_length=1, max_length=300, description="Item id, slug path or full avito.ru URL")
    ],
    photos: Annotated[
        int,
        Field(
            ge=0,
            le=3,
            description=(
                "Opt-in, costly in tokens: attach up to N listing photos (active ads only). Photos belong to the "
                "seller; do not describe faces, documents or plates in them. 0 = off. Details: guide://avito/avito_card"
            ),
        ),
    ] = 0,
    ctx: Context | None = None,
) -> AvitoCardResponse:
    """Read an Avito item over CDP: Product JSON-LD first, item-view DOM fallback.

    Full avito.ru URLs need an item-page path (/1234567890, /slug_1234567890 or
    /city/category/slug_1234567890); bare ids and slug paths use /{id}.
    price_rub is None when absent or non-RUB — never 0. Deal terms, seller
    checks, city/stations, date and views come from the same page; address and
    coords for companies only; availability from service signals only.

    Full field list and error codes: `guide://avito/avito_card`.
    """
    log_event("avito_card.start", input=item_id_or_url[:80])
    try:
        raw = item_id_or_url.strip()
        try:
            parts = urllib.parse.urlsplit(raw)
            port = parts.port
        except ValueError:
            raise_tool_error(BadRequestError("invalid Avito item URL"))
        if parts.scheme or parts.netloc:
            if (
                not url_on_hosts(raw, ALLOWED_HOSTS)
                or parts.username is not None
                or parts.password is not None
                or port not in (None, 80, 443)
            ):
                raise_tool_error(BadRequestError("pass an http(s) avito.ru item URL without credentials"))
            # A full URL is agent-controlled input aimed at the operator's
            # logged-in browser: the path must be an item page, never an
            # arbitrary avito.ru route, so only the item-page shapes above
            # survive here (R-INPUT-TRUST).
            #
            # An item path is a plain ASCII slug: a backslash or a percent
            # escape in it would let the browser read a different path than
            # this check does — Chromium treats "\" as "/" in http(s) URLs, and
            # a server decodes "%2F"/"%5C" into a separator — so
            # /profile\x_1234567890 and /profile%2Fx_1234567890 would open the
            # personal route the check exists to refuse (R-AVITO-ITEM-PATH).
            unsafe_path = "\\" in parts.path or "%" in parts.path
            path_match = _ITEM_URL_PATH_RE.fullmatch(parts.path)
            root = parts.path.strip("/").split("/", 1)[0].lower()
            if unsafe_path or path_match is None or root in _NON_ITEM_ROOTS:
                raise_tool_error(
                    BadRequestError(
                        f"URL path is not an Avito item page: {item_id_or_url!r}; "
                        "pass /<id>, /<slug>_<id> or /<city>/<category>/<slug>_<id>"
                    )
                )
            item_id = int(path_match.group(1))
            # Rebuilt from validated pieces only: query and fragment are dropped.
            url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        else:
            extracted = _extract_item_id(parts.path)
            if extracted is None:
                raise_tool_error(
                    BadRequestError(
                        f"could not extract an item id from {item_id_or_url!r}; "
                        "pass bare digits, a /slug_1234567890 path, or a full avito.ru URL"
                    )
                )
            item_id = extracted
            # Live 2026-10-12: /{id} redirects to the canonical item page, while
            # /_/{id} answers Avito's 404 page.
            url = f"{SITE_BASE}/{item_id}"
        status, body, tier = await _fetch_item_page(url, ctx)
        _check_rendered_page(status, body, tier, "card")
        try:
            item = parse_item_page(body, item_id)
        except (ParserDriftError, NotFoundError) as exc:
            raise_tool_error(exc)
        details = _state.card_details(body, item_id, item.get("description"))
        seller_data = parse_item_seller(body, company_hint=details["is_company"])
        # The card hands on the id avito_seller opens as this same profile (R-AVITO-SELLER-ROUNDTRIP).
        seller_data["seller_id"] = seller_handle(seller_data.get("profile_url"))
        item["seller"] = (
            AvitoSellerOut(**seller_data) if any(value is not None for value in seller_data.values()) else None
        )
        item["url"] = item["url"] or url
        item["availability"], item["availability_evidence"] = _state.card_availability(body, item_id)
        _apply_details(item, details)
        price_warnings = _apply_sale(item, *_state.card_sale_price(body, item_id))
        result = AvitoCardResponse(**item, tier_used="cdp")
        _pacer.record_success()
        # The card's own fields; `seller` carries the seller's rating, not the
        # listing's, and must stay out of the offer invariants.
        card = result.model_dump(by_alias=True, exclude={"meta", "seller"})
        attached = R.attach_meta(card, [*R.validate_offer(card), *price_warnings], source="avito_card")
        result.meta = MetaOut(**attached["_meta"])
        if photos <= 0:
            return result
        if result.availability != _state.ACTIVE:
            result.photos_status = "skipped_inactive"
            return result
        images = await _attach_photos(result, _state.photo_urls(body, item_id), photos)
        payload = result.model_dump(by_alias=True, mode="json")
        content: list[TextContent | ImageContent] = [
            TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))
        ]
        content.extend(images)
        # FastMCP passes a ToolResult through as is; the declared output schema stays the card's.
        return ToolResult(content=content, structured_content=payload)  # type: ignore[return-value]
    except ToolError:
        raise
    except Exception as exc:
        log_event("avito_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error_from(exc, _redact(f"avito_card failed: {exc}"))


@mcp.tool(
    name="avito_seller",
    annotations=ToolAnnotations(
        title="Avito Seller Profile",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
async def avito_seller(
    seller_id_or_url: Annotated[
        str, Field(min_length=1, max_length=300, description="Seller id or profile URL from a card/search hit")
    ],
    ctx: Context | None = None,
) -> AvitoSellerResponse:
    """Read an Avito seller's rendered profile page over paced, allowlisted CDP.

    Accept an avito.ru /brands/<id> or /user/<id>/profile URL/path (query and
    fragment stripped), a bare hash/id, or brands/<id> / user/<id>. Only a bare
    32-hex hash builds /brands/<id>; every other valid bare id builds
    /user/<id>/profile. seller_id from avito_card always opens that same seller.
    Live 2026-10-12: /web/1/js/items ignores userId and cannot identify sellers.

    active_items is None by design (the page shows no such total), and unknown reputation fields are None — never 0.

    Full field list and error codes: `guide://avito/avito_seller`.
    """
    log_event("avito_seller.start", input=seller_id_or_url[:80])
    try:
        try:
            url = _seller_profile_url(seller_id_or_url)
        except BadRequestError as exc:
            raise_tool_error(exc)
        seller_id = seller_handle(url)
        status, body, tier = await _fetch_item_page(url, ctx)
        _check_rendered_page(status, body, tier, "seller")
        try:
            seller_data = parse_seller_profile(body, url)
        except (ParserDriftError, NotFoundError) as exc:
            raise_tool_error(exc)
        seller = AvitoSellerOut(**seller_data, seller_id=seller_id, profile_url=url)
        result = AvitoSellerResponse(seller=seller, active_items=None, tier_used="cdp")
        _pacer.record_success()
        warnings = ["seller_identity_missing"] if seller.name is None else []
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="avito_seller")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("avito_seller.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error_from(exc, _redact(f"avito_seller failed: {exc}"))


# CLI-only drift canary: ``marketplace-mcp doctor`` imports and calls avito_selfcheck()
# directly. It is deliberately NOT registered as an MCP tool — selfchecks are
# operator diagnostics, and their input/output schemas would be billed in every
# client request.
async def avito_selfcheck(ctx: Context | None = None) -> AvitoSelfcheckResponse:
    """Structural drift canary for Avito (tri-state: success / drift_detected /
    inconclusive). Chains search -> its first item's card -> that card's seller
    profile, exercising the same CDP paths and parsers as the public tools.
    Card health requires a title (price is optional); seller health requires name or score.
    Without a seller profile link the seller probe is inconclusive/no_seller_link.

    A firewall/challenge block or CDP-down is ``inconclusive`` (transport), NEVER
    drift: from a datacenter IP that is the expected state. A reached page that
    fails its parser smoke (including unusable Product and item DOM) is ``drift``.

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
        raise_tool_error_from(exc, _redact(f"avito_selfcheck failed: {exc}"))


async def _avito_selfcheck_impl(ctx: Context | None) -> AvitoSelfcheckResponse:
    checks: dict[str, dict] = {}
    probe_item: dict[str, Any] | None = None

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
            verdict, reason = smoke(payload)
        except ConnectorError as exc:
            drift = isinstance(exc, ParserDriftError)
            if isinstance(exc, (RateLimitedError, ChallengeRequiredError)):
                _pacer.record_refusal()
            checks[name] = R.selfcheck_entry(
                "drift" if drift else "inconclusive",
                baseline=baseline,
                reason="parse_smoke_failed" if drift else exc.code.value,
                notes=[_redact(str(exc))[:160]],
            )
            return
        except Exception as exc:
            checks[name] = R.selfcheck_entry(
                "drift", baseline=baseline, reason="parse_smoke_failed", notes=[str(exc)[:160]]
            )
            return
        checks[name] = R.selfcheck_entry(
            "drift" if reason else "healthy", baseline=baseline, reason=reason, notes=[verdict]
        )

    def _search_smoke(payload: Any) -> tuple[str, str | None]:
        nonlocal probe_item
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
        probe_item = first
        # R-SELFCHECK-GATES-PRICE: the search tool's own price (price_rub).
        priced = [it for it in items if it.get("price_rub") is not None]
        reason = R.price_gate(len(items), len(priced))
        return (f"{len(items)} items, {len(priced)} priced", reason)

    def _page_failure(name: str, baseline: str, exc: Exception) -> None:
        text = _redact(str(exc))
        drift = isinstance(exc, ParserDriftError) or "parser_drift" in text
        checks[name] = R.selfcheck_entry(
            "drift" if drift else "inconclusive",
            baseline=baseline,
            reason="parse_smoke_failed" if drift else "transport_down",
            notes=[text[:160]],
        )

    await _check(
        "search",
        _build_search_url("ноутбук", 1, _settings.location_id, None),
        _search_smoke,
        "js-items-search-v1",
    )
    card_baseline = "item-page-product-v1"
    seller_baseline = "seller-profile-page-v1"
    seller_url: str | None = None
    item_handle = (probe_item.get("url") or str(probe_item.get("item_id") or "")) if probe_item else ""
    if item_handle:
        try:
            async with asyncio.timeout(60):
                card = await avito_card(item_handle, ctx=ctx)
            # A missing price is legal (exchange, free, price on request); the title
            # is what proves the item page was read (Product or DOM).
            healthy = bool(card.title)
            notes = ["item page card parsed"] if healthy else ["card carries no title"]
            if healthy and card.price_rub is None:
                notes.append("probe item has no price")
            checks["card"] = R.selfcheck_entry(
                "healthy" if healthy else "drift",
                baseline=card_baseline,
                reason=None if healthy else "parse_smoke_failed",
                notes=notes,
            )
            seller_url = card.seller.profile_url if card.seller else None
        except Exception as exc:
            _page_failure("card", card_baseline, exc)
    else:
        checks["card"] = R.selfcheck_entry("inconclusive", baseline=card_baseline, reason="no_item_link")

    if seller_url:
        try:
            async with asyncio.timeout(60):
                profile = await avito_seller(seller_url, ctx=ctx)
            healthy = profile.seller is not None and (
                bool(profile.seller.name) or profile.seller.rating_score is not None
            )
            checks["seller"] = R.selfcheck_entry(
                "healthy" if healthy else "drift",
                baseline=seller_baseline,
                reason=None if healthy else "parse_smoke_failed",
                notes=["profile name or score parsed" if healthy else "profile carries no name or score"],
            )
        except Exception as exc:
            _page_failure("seller", seller_baseline, exc)
    else:
        checks["seller"] = R.selfcheck_entry("inconclusive", baseline=seller_baseline, reason="no_seller_link")

    result_dict = R.selfcheck_result(
        "avito",
        checks,
        required=("search", "card", "seller"),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return AvitoSelfcheckResponse(**result_dict)


# Advertised output schemas are the dominant constant cost of an MCP mount:
# replace the full Pydantic tree with top-level field names (~64 % fewer
# wire tokens on the unified server).
apply_compact_output_schemas(mcp)

# The reference half of every tool description is served as an MCP resource
# (guide://<source>/<tool>) and the error taxonomy as marketplace://errors, so
# the agent pays for them when it reads them instead of on every request.
register_reference_resources(mcp)
