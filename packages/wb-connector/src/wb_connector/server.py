"""Wildberries MCP connector.

Public WB internal APIs (catalog parsing, not Seller API). Anti-bot light;
residential IP works without proxies. No credentials needed.

Endpoints (verified Nov 2026 against live data):
  - https://card.wb.ru/cards/v4/detail        (batch product cards)
  - https://basket-NN.wbbasket.ru/...          (root_id / variants / specs)
  - https://feedbacks2.wb.ru/feedbacks/v2/{imt_id}   (review pool)
  - https://search.wb.ru/exactmatch/...        (search; 429-prone)

CRITICAL Nov 2026 discovery:
  Top-level priceU/salePriceU are NOW null. Real prices live in
  sizes[0].price.{product, basic} as integer kopecks. Code extracts both.

Mandatory query params:
  - dest=-1257786 (Moscow). WITHOUT dest: empty stocks/wrong prices/Cloudflare HTML.
  - appType=1 (desktop client).
  - curr=rub.
  - locale=ru.

Polite rate limit: 1.5s gap on residential IP. Don't disable.

NEVER use print() in stdio MCP — corrupts JSON-RPC. Use ctx.debug/info/error.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import string
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
    BadRequestError,
    NotFoundError,
    ParserDriftError,
    RateLimitedError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport import get_text_budgeted, proxy_from_env
from pydantic import Field

from wb_connector.models_output import (
    MetaOut,
    WbCardItem,
    WbCardResponse,
    WbCategoriesResponse,
    WbCategoryNode,
    WbCategoryProductsResponse,
    WbNoResultsResponse,
    WbQuestionItem,
    WbQuestionsResponse,
    WbReviewItem,
    WbReviewsResponse,
    WbRootInfoResponse,
    WbSearchResponse,
    WbSelfCheckResponse,
    WbSellerResponse,
)
from wb_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "1.1.0"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

mcp = FastMCP(
    name="wb-connector",
    version=SERVER_VERSION,
)
mcp.add_middleware(RetryMiddleware(max_retries=3, base_delay=1.0))

WB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
WB_TIMEOUT = httpx.Timeout(_settings.timeout, connect=5.0)
WB_WALL_TIMEOUT = _settings.wall_timeout
WB_DEFAULT_DEST = _settings.default_dest  # Moscow
MAX_BODY_BYTES = _settings.max_body_bytes  # 50 MB hard cap (CDN compromise / MITM defense)
_SELFCHECK_NM = 5535522  # DEFENDER filter — golden-fixture baseline SKU
_SELFCHECK_IMT = 1002173489  # its imt_id (review pool baseline)
WB_REVIEW_HOSTS = ("feedbacks2.wb.ru", "feedbacks1.wb.ru")
# Buyer questions live on their own host with NO mirrors — verified Jul 2026:
# questions1/questions2.wildberries.ru and questions*.wb.ru all answer 502, so the
# feedbacks-style host fallback above deliberately has no counterpart here.
#
# Trap worth knowing: feedbacks{1,2}.wb.ru answer ANY questions-shaped path with an
# identical 277-byte empty-feedbacks stub. It looks like a valid "no questions"
# response, which is exactly why questions must never be requested from there.
WB_QUESTIONS_HOST = "questions.wildberries.ru"
# take is hard-capped upstream: take=31 returns HTTP 400 (verified Jul 2026).
# Both take and skip are MANDATORY — omitting either yields
# {"questions": null, "count": 0} for every product, which reads as "no questions"
# but is really a malformed request. That silent-empty failure is the single
# easiest way to build a confidently-wrong tool on this endpoint.
WB_QUESTIONS_PAGE_SIZE = 30
# Category feeds live at catalog.wb.ru with the shard in the PATH. Verified live
# Jul 2026: only the v4 shard-path form answers 200 — v2, v8, v9 and the
# shard-less /catalog/v4/catalog variant all 404.
WB_CATEGORY_PAGE_SIZE = 100
# WB's own marker for a category that has no listable feed. Several of its largest
# sections carry it (smartphones cat=9455, laptops cat=9491, TV/audio cat=9834):
# they exist as navigation only. Confirmed live — the first probe answered 429,
# which looked like throttling, but four further probes spaced 8s apart all
# returned a clean empty 404. Treated as a refusal, never as an empty category.
WB_UNLISTABLE_SHARD = "blackhole"
# Upstream ordering values accepted by the catalog feed.
WB_CATEGORY_SORTS = frozenset({"popular", "priceup", "pricedown", "newly", "rate", "benefit"})
_WB_SHARD_RE = re.compile(r"^[a-z0-9_-]{2,60}$", re.IGNORECASE)
# The selector is appended to the URL verbatim, so it is constrained to the two
# real forms rather than trusted: this string reaches an outbound request.
_WB_CATEGORY_QUERY_RE = re.compile(r"^(?:cat|subject|kind|brand|xsubject)=[0-9;,]{1,80}$", re.IGNORECASE)
# Static CDN mirrors serving seller registration records and the catalog menu.
# Verified live Jul 2026: vol0/data/supplier-by-id/{id}.json and
# vol0/data/main-menu-ru-ru-v3.json. Several mirrors exist; they are tried in
# order because individual basket hosts go down for maintenance independently.
WB_STATIC_HOSTS = (
    "static-basket-01.wbbasket.ru",
    "static-basket-02.wbbasket.ru",
    "basket-01.wbbasket.ru",
)
# The full menu is ~800 KB of JSON — far past what belongs in a tool response,
# hence the node budget below and the depth/root slicing in wb_categories.
WB_MENU_MAX_NODES = 400
# Broad evergreen search baseline (audit 2026-06-01): a high-volume RU query that
# always returns ids on search-goods, so 0 recoverable ids = shape drift, not rot.
_SELFCHECK_SEARCH_QUERY = "телефон"

# Verified live Nov 2026: feedbacks/v2/{imt} IGNORES order=/sort=/take=/skip= and
# always returns the same pool (~1000 most-recent, newest-first). So sorting is
# done CLIENT-SIDE over that returned pool. Aliases mirror ozon_reviews.
_WB_REVIEW_SORTS = {"recent", "best", "worst"}
_WB_REVIEW_SORT_ALIASES = {
    "default": "recent",
    "newest": "recent",
    "highest": "best",
    "lowest": "worst",
    "complaints": "worst",
}

_last_request_ts = 0.0
_min_gap = _settings.min_gap
_rate_lock = asyncio.Lock()

# Bounded retry for TRANSIENT network faults only (connect/read timeout, conn
# reset). Verified live Nov 2026: wb_search intermittently throws ConnectTimeout
# on the first hit and a single retry succeeds. We retry ONLY on httpx transport
# exceptions — never on an HTTP status (a 429/5xx retry would worsen rate-limit).
_NET_RETRIES = _settings.net_retries  # retries after the first try (so up to 3 attempts)
_NET_BACKOFF_S = _settings.net_backoff_s

# Cache the (status, text, err) triple keyed by URL. An agent conversation walks
# the same SKU repeatedly — price, then reviews, then a cross-marketplace
# comparison — and each of those is a fresh HTTP call without this. Caching the
# whole triple rather than just successes is deliberate: a rate-limit or block is
# also worth remembering for a moment, since retrying it immediately is exactly
# what provoked it. WB_CACHE_TTL=0 disables.
_cache: TTLCache[tuple[int, str | None, str | None]] = TTLCache(ttl_s=_settings.cache_ttl, max_entries=256)


def _proxy() -> str | None:
    """Resolve WB's proxy: explicit ``WB_PROXY`` first, then the standard vars."""
    return (_settings.proxy or "").strip() or proxy_from_env("WB_PROXY")


def _wb_client() -> httpx.AsyncClient:
    """Build WB's HTTP client.

    Kept as a helper so proxy resolution happens in exactly one place. Redirects
    stay off, matching the previous inline construction: WB answers some
    datacenter requests with a self-referential 307 and following it burns the
    retry budget instead of surfacing the block.
    """
    return httpx.AsyncClient(timeout=WB_TIMEOUT, headers=WB_HEADERS, proxy=_proxy())


class _PoliteGate:
    """Adapter exposing WB's module-level polite gate as a ``RateLimiter``.

    Core's ``get_text_budgeted`` re-enters a limiter between retries. WB's gate
    is module state (``_min_gap``, ``_last_request_ts``) that tests monkeypatch
    directly, and ``_polite_wait`` is itself patched in several tests. Reading
    both through the live module attribute keeps every one of those seams working
    while still handing core a limiter to call.
    """

    async def wait(self) -> None:
        await _polite_wait()


async def _safe_get_text(client: httpx.AsyncClient, url: str) -> tuple[int, str | None, str | None]:
    """GET with body cap, wall-clock budget, and bounded transient-network retry.

    Thin delegation to ``mcp_core.transport.get_text_budgeted``. That function is
    WB's own logic promoted into the shared runtime, so behaviour is unchanged:
    ``(status, text, err)`` with the same ``timeout:`` / ``network:`` /
    ``http_status:`` classification, retries only on transport faults (never on an
    HTTP status, so rate-limit handling stays the caller's explicit decision), the
    whole operation bounded by ``WB_WALL_TIMEOUT``, and the polite gate re-entered
    before each retry.

    The module-level knobs are read at call time, not captured at import, because
    tests patch them per scenario.

    **Only successful reads are cached.** A 200 with a body is idempotent catalog
    data and safe to replay for the TTL. A failure is not: caching a transient
    ConnectTimeout would keep failing the tool for the whole TTL window even
    though the very next attempt would have succeeded, turning a blip into an
    outage. Statuses like 429 and 5xx stay uncached for the same reason — the
    caller's error path should see live upstream state, not a stale verdict.
    """
    cached = _cache.get(url)
    if cached is not None:
        return cached

    result = await get_text_budgeted(
        client,
        url,
        max_bytes=MAX_BODY_BYTES,
        wall_timeout_s=WB_WALL_TIMEOUT,
        retries=_NET_RETRIES,
        backoff_s=_NET_BACKOFF_S,
        limiter=_PoliteGate(),
    )

    status, text, err = result
    if err is None and status == 200 and text is not None:
        _cache.set(url, result)
    return result


async def _polite_wait():
    global _last_request_ts
    async with _rate_lock:
        elapsed = time.monotonic() - _last_request_ts
        if elapsed < _min_gap:
            await asyncio.sleep(_min_gap - elapsed)
        _last_request_ts = time.monotonic()


def _basket_for_sku(nm_id: int) -> str:
    """Compute basket CDN host. Probe range up to 28 for new SKUs."""
    vol = nm_id // 100000
    table = [
        (143, "01"),
        (287, "02"),
        (431, "03"),
        (719, "04"),
        (1007, "05"),
        (1061, "06"),
        (1115, "07"),
        (1169, "08"),
        (1313, "09"),
        (1601, "10"),
        (1655, "11"),
        (1919, "12"),
        (2045, "13"),
        (2189, "14"),
        (2405, "15"),
        (2621, "16"),
        (2837, "17"),
        (3053, "18"),
        (3269, "19"),
        (3485, "20"),
        (3701, "21"),
        (3917, "22"),
        (4133, "23"),
        (4349, "24"),
        (4565, "25"),
        (4781, "26"),
        (4997, "27"),
    ]
    for upper, nn in table:
        if vol <= upper:
            return f"basket-{nn}.wbbasket.ru"
    log_event("wb.basket_fallback_used", vol=vol, fallback=get_settings().basket_fallback)
    return f"{get_settings().basket_fallback}.wbbasket.ru"


def _decode_mojibake(s: object) -> str:
    """Fix WB double-encoded UTF-8. Best-effort: keeps correct strings alone.

    Accepts ANY type and returns a str: a drifted/null field (text=None, or a
    rich-text dict) must not crash the downstream [:1500] slice — this helper is
    the single chokepoint every WB text field flows through (audit wave-2 DRIFT:
    text/pros/cons/user=None raised 'NoneType not subscriptable')."""
    if not isinstance(s, str):
        return ""
    if not s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        cyr_orig = sum("\u0400" <= c <= "\u04ff" for c in s)
        cyr_fixed = sum("\u0400" <= c <= "\u04ff" for c in fixed)
        return fixed if cyr_fixed > cyr_orig else s
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _card_item_dict(p: dict[str, Any]) -> dict[str, Any]:
    """Flatten one WB product object into the shared card-item shape.

    Used by wb_card, wb_search and wb_category_products so a product looks the
    same however it was found — a category walk and a text search stay directly
    comparable. Price extraction and the in_stock rule live in one place here,
    which matters because "in stock" means *both* a positive quantity and a real
    price: a quantity with no price is an unsellable listing, and calling it
    available would rank a dead item as the cheapest option.
    """
    cur_rub, orig_rub = _extract_price_rub(p)
    qty = R.coerce_int(p.get("totalQuantity"))
    return {
        "nm_id": p.get("id"),
        "name": _decode_mojibake(p.get("name", "")),
        "brand": _decode_mojibake(p.get("brand", "")),
        "supplier": _decode_mojibake(p.get("supplier", "")),
        "supplier_id": p.get("supplierId"),
        "supplier_rating": p.get("supplierRating"),
        "review_rating": p.get("reviewRating"),
        "feedbacks": p.get("feedbacks"),
        "total_quantity": qty,
        "in_stock": qty is not None and qty > 0 and cur_rub is not None,
        "price_rub": cur_rub,
        "price_original_rub": orig_rub,
    }


def _normalise_ws(value: object) -> str:
    """Collapse runs of whitespace into single spaces.

    Seller answers to buyer questions arrive with literal newlines and tabs in
    them (verified live Jul 2026). Left alone, a multi-line answer breaks any
    single-line rendering an agent produces, and a naive slice can end mid-escape.
    Accepts any type and always returns a str, matching ``_decode_mojibake``.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _extract_price_rub(p: dict) -> tuple[float | None, float | None]:
    """Return (current_rub, original_rub), or (None, None) if no live offer.

    WB v4 (Nov 2026): top-level priceU/salePriceU are null. Prices live in
    sizes[0].price.{product, basic} as integer kopecks.

    None (not 0.0) is returned when the SKU has no sellable price — verified
    Nov 2026 that this coincides with totalQuantity==0 (out of stock). A 0.0
    sentinel would falsely rank a dead listing as the cheapest item, corrupting
    any "find the best/cheapest" comparison.
    """
    if not isinstance(p, dict):
        return (None, None)  # defensive: a non-dict product never has a price
    sizes = p.get("sizes") or []
    # A null / non-dict size entry must NOT crash the whole tool (audit
    # 2026-06-01 CONFIRMED: sizes:[None] raised AttributeError before attach_meta
    # could emit a drift warning). Find the first dict size with a dict price.
    if isinstance(sizes, list):
        for sz in sizes:
            if not isinstance(sz, dict):
                continue
            price = sz.get("price")
            if not isinstance(price, dict):
                continue
            # coerce so a number->string price drift ('12300') doesn't crash the
            # `/100` math (audit wave-2 round-2 DRIFT). coerce_price already
            # rejects <=0/junk and returns None.
            product = _kopeck_to_rub(price.get("product") or price.get("total"))
            basic = _kopeck_to_rub(price.get("basic")) or product
            if product:
                return (product, basic or product)
    sale = _kopeck_to_rub(p.get("salePriceU"))
    base = _kopeck_to_rub(p.get("priceU"))
    if sale or base:
        return ((sale or base), (base or sale))
    return (None, None)


def _kopeck_to_rub(v: object) -> float | None:
    """WB integer kopecks -> float rubles, tolerant of a number->string drift.
    Returns None for null/<=0/unparseable (never crashes the /100 math)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 100 if v > 0 else None
    if isinstance(v, str):
        stripped = v.strip()
        if not stripped or any(c not in string.digits for c in stripped):
            return None
        try:
            amount = int(stripped)
        except ValueError:
            return None
        return (amount / 100) if amount > 0 else None
    return None


def _expect_json_object(value: Any, label: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(obj, None)`` for a JSON object, else ``(None, error_dict)``.

    Exactly one element is ever non-None. Callers that raise on the error branch
    should pass the result through ``_require_object`` afterwards so the
    invariant is visible to type checkers instead of merely implied.
    """
    if isinstance(value, dict):
        return value, None
    return None, {
        "status": "error",
        "type": "parse",
        "message": f"{label} expected object, got {type(value).__name__}",
    }


def _require_object(obj: dict[str, Any] | None, label: str) -> dict[str, Any]:
    """Assert the non-error branch of ``_expect_json_object``.

    Reached only when the caller already handled the error tuple, so a None here
    means the two branches drifted apart in code — a bug, surfaced loudly as
    parser drift rather than an AttributeError three frames later.
    """
    if obj is None:
        raise_tool_error(ParserDriftError(f"{label}: expected an object", provider="wb"))
    return obj


def _card_products_checked(data: dict[str, Any]) -> tuple[list[Any], str | None]:
    if "products" in data:
        products = data.get("products")
        return (products, None) if isinstance(products, list) else ([], "products_not_list")
    nested = data.get("data")
    if isinstance(nested, dict):
        if "products" not in nested:
            return [], "products_missing"
        products = nested.get("products")
        return (products, None) if isinstance(products, list) else ([], "products_not_list")
    if "data" in data:
        return [], "data_not_object"
    return [], "products_missing"


def _card_products(data: dict[str, Any]) -> list[Any]:
    products, _error = _card_products_checked(data)
    return products


@mcp.tool(
    name="wb_card",
    annotations=ToolAnnotations(
        title="WB Product Cards",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_card(
    nm_ids: Annotated[
        list[int],
        Field(
            description="1..100 nmId integers (positive). Batch product cards from WB v4 API.",
        ),
    ],
    dest: Annotated[
        str,
        Field(
            default=WB_DEFAULT_DEST,
            description="Region ID. Default -1257786 (Moscow). Required for valid prices/stocks. "
            "Other examples: -1257786 Moscow, -1029256 Saint Petersburg.",
        ),
    ] = WB_DEFAULT_DEST,
    ctx: Context | None = None,
) -> WbCardResponse:
    """Fetch product card data from WB v4 API.

    Returns prices in rubles, brand, supplier, supplier_rating, review_rating,
    feedbacks count, total_quantity for up to 100 SKUs.

    Args:
        nm_ids: 1..100 nmId integers.
        dest: Region ID (default Moscow -1257786). Required for valid prices.
              Other examples: -1257786 Moscow, -1029256 Saint Petersburg.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    log_event("wb_card.start", nm_count=len(nm_ids), dest=dest)
    if ctx is not None:
        await ctx.info(f"wb_card: {len(nm_ids)} nm_ids dest={dest}")

    if not nm_ids:
        log_event("wb_card.validation_failed", reason="empty_nm_ids")
        raise_tool_error(BadRequestError("nm_ids empty"))
    if len(nm_ids) > 100:
        log_event("wb_card.validation_failed", reason="too_many_nm_ids", count=len(nm_ids))
        raise_tool_error(BadRequestError(f"max 100 nm_ids, got {len(nm_ids)}"))
    if any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in nm_ids):
        log_event("wb_card.validation_failed", reason="non_positive_nm_ids")
        raise_tool_error(BadRequestError("nm_ids must be positive integers"))

    nm_param = ";".join(str(n) for n in nm_ids)
    params = httpx.QueryParams(
        {
            "appType": "1",
            "curr": "rub",
            "dest": dest,
            "locale": "ru",
            "spp": "30",
            "nm": nm_param,
        }
    )
    url = f"https://card.wb.ru/cards/v4/detail?{params}"
    if ctx:
        await ctx.debug(f"wb_card: {len(nm_ids)} nm_ids dest={dest}")
    await _polite_wait()

    try:
        try:
            async with _wb_client() as client:
                status_code, text, err = await _safe_get_text(client, url)
            if err:
                log_event("wb_card.network_error", error=_redact(err))
                raise_tool_error(TransportDownError(err, provider="wb"))
            if status_code == 429:
                log_event("wb_card.rate_limited")
                raise_tool_error(RateLimitedError("wb", retry_after_s=30.0))
            if text and "<html" in text[:200].lower():
                log_event("wb_card.blocked", reason="cloudflare_html")
                raise_tool_error(TransportDownError("Cloudflare HTML page (likely missing dest param)", provider="wb"))
            if status_code != 200:
                log_event("wb_card.http_error", status=status_code)
                raise_tool_error(
                    TransportDownError(f"wb_card HTTP {status_code}", provider="wb", status_code=status_code)
                )
            try:
                data = json.loads(text or "")
            except json.JSONDecodeError as exc:
                log_event("wb_card.parse_error", error=_redact(str(exc)))
                raise_tool_error(ParserDriftError(str(exc), provider="wb"))
        except httpx.HTTPError as exc:
            log_event("wb_card.network_error", error=_redact(str(exc)), exc_type=type(exc).__name__)
            raise_tool_error(TransportDownError(str(exc), provider="wb"))

        data, shape_error = _expect_json_object(data, "wb_card response")
        data = _require_object(data, "wb_card response")
        if shape_error:
            log_event("wb_card.shape_error", error=_redact(shape_error.get("message", "")))
            raise_tool_error(ParserDriftError(shape_error.get("message", ""), provider="wb"))
        data = _require_object(data, "wb_card response")
        products, products_error = _card_products_checked(data)
        if products_error:
            log_event("wb_card.shape_error", error=products_error)
            raise_tool_error(ParserDriftError(f"wb_card response {products_error}", provider="wb"))
        item_dicts: list[dict[str, Any]] = []
        for p in products:
            if not isinstance(p, dict):
                continue  # non-object product entry must not crash (audit wave-2)
            item_dicts.append(_card_item_dict(p))
        warnings = _aggregate_offer_warnings(item_dicts)
        log_event("wb_card.done", nm_count=len(nm_ids), items=len(item_dicts))
        return WbCardResponse(
            dest=dest,
            count=len(item_dicts),
            items=[WbCardItem(**d) for d in item_dicts],
            meta=MetaOut(source="wb_card", healthy=not warnings, warnings=warnings),
        )
    except ToolError:
        raise
    except Exception as exc:
        log_event("wb_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(str(exc)), provider="wb"))


@mcp.tool(
    name="wb_root_info",
    annotations=ToolAnnotations(
        title="WB Root Info (imt_id)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_root_info(
    nm_id: Annotated[
        int,
        Field(
            gt=0,
            description="nmId (positive integer). The SKU whose basket CDN card.json is fetched for imt_id resolution.",
        ),
    ],
    ctx: Context | None = None,
) -> WbRootInfoResponse:
    """Fetch full card metadata from basket CDN. Returns imt_id (root_id) for review pool.

    All variants of one product share imt_id. Reviews indexed by imt_id, NOT by nmId.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    log_event("wb_root_info.start", nm_id=nm_id)
    if ctx is not None:
        await ctx.info(f"wb_root_info: nm_id={nm_id}")

    host = _basket_for_sku(nm_id)
    vol = nm_id // 100000
    part = nm_id // 1000
    url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    if ctx:
        await ctx.debug(f"wb_root_info: {url}")
    await _polite_wait()

    try:
        try:
            async with _wb_client() as client:
                status_code, text, err = await _safe_get_text(client, url)
            if err:
                log_event("wb_root_info.network_error", error=_redact(err))
                raise_tool_error(TransportDownError(f"{err} [url={url}]", provider="wb"))
            if status_code == 404:
                log_event("wb_root_info.not_found", nm_id=nm_id)
                raise_tool_error(NotFoundError(f"basket card.json not found for nm_id={nm_id}", provider="wb"))
            if status_code != 200:
                log_event("wb_root_info.http_error", status=status_code)
                raise_tool_error(
                    TransportDownError(
                        f"wb_root_info HTTP {status_code} [url={url}]", provider="wb", status_code=status_code
                    )
                )
            try:
                data = json.loads(text or "")
            except json.JSONDecodeError as exc:
                log_event("wb_root_info.parse_error", error=_redact(str(exc)))
                raise_tool_error(ParserDriftError(f"{exc} [url={url}]", provider="wb"))
        except httpx.HTTPError as exc:
            log_event("wb_root_info.network_error", error=_redact(str(exc)), exc_type=type(exc).__name__)
            raise_tool_error(TransportDownError(f"{exc} [url={url}]", provider="wb"))

        data, shape_error = _expect_json_object(data, "wb_root_info response")
        data = _require_object(data, "wb_root_info response")
        if shape_error:
            log_event("wb_root_info.shape_error", error=_redact(shape_error.get("message", "")))
            raise_tool_error(ParserDriftError(f"{shape_error.get('message', '')} [url={url}]", provider="wb"))
        imt = R.first_present(data, "imt_id", "imtId")
        if imt is None and isinstance(data.get("data"), dict):
            imt = R.first_present(data["data"], "imt_id", "imtId")
        if imt is None:
            log_event("wb_root_info.imt_missing", nm_id=nm_id)
            raise_tool_error(
                ParserDriftError(
                    "imt_id_missing: no imt_id/imtId at top level or under data "
                    "(schema drift — wb_reviews needs this id)",
                    provider="wb",
                )
            )
        imt_int = R.coerce_int(imt)
        if imt_int is None or imt_int <= 0:
            log_event("wb_root_info.imt_unusable", nm_id=nm_id, imt=imt)
            raise_tool_error(ParserDriftError(f"imt_id unusable: {imt!r} (expected positive integer)", provider="wb"))
        opts = data.get("options")
        log_event("wb_root_info.done", nm_id=nm_id, imt_id=imt_int)
        return WbRootInfoResponse(
            imt_id=imt_int,
            subj_name=_decode_mojibake(data.get("subj_name", "")),
            subj_root_name=_decode_mojibake(data.get("subj_root_name", "")),
            colors=data.get("colors") or data.get("nm_colors_names"),
            compositions=data.get("compositions"),
            options=(opts[:30] if isinstance(opts, list) else []),
            host_used=host,
            meta=MetaOut(source="wb_root_info", healthy=True, warnings=[]),
        )
    except ToolError:
        raise
    except Exception as exc:
        log_event("wb_root_info.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(str(exc)), provider="wb"))


def _aggregate_offer_warnings(items: list[dict]) -> list[str]:
    """Roll per-item validation into connector-level warnings (systemic drift only)."""
    if not items:
        return ["no_items: zero parseable products returned"]
    from collections import Counter

    tally: Counter = Counter()
    for it in items:
        for w in R.validate_offer(it, require_title=True):
            tally[w.split(":")[0]] += 1
    n = len(items)
    # Threshold: half the items (systemic drift), but ALWAYS warn when a single
    # item is affected in a 1-item result — wb_card([sku]) / a 1-hit search must
    # not silently drop a real price_zero/title_missing warning (audit 2026-06-01
    # CONFIRMED: max(2, n//2) needed 2 hits so n==1 drift was suppressed).
    threshold = max(1, n // 2)
    return [
        f"{code}: {hits}/{n} items affected (likely schema drift)" for code, hits in tally.items() if hits >= threshold
    ]


def _wb_review_date_ts(created: object) -> float:
    """Parse WB ISO createdDate to a unix ts for tiebreak sorting (0 if absent
    or non-string — a drifted numeric/None date must not crash the sort key,
    audit wave-2)."""
    if not isinstance(created, str) or not created:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


@mcp.tool(
    name="wb_reviews",
    annotations=ToolAnnotations(
        title="WB Reviews by imt_id",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_reviews(
    imt_id: Annotated[
        int,
        Field(
            gt=0,
            description="Root ID (imt_id) from wb_root_info. All product variants share one review pool indexed by imt_id, NOT by nmId.",
        ),
    ],
    limit: Annotated[
        int,
        Field(
            default=20,
            ge=1,
            le=100,
            description="Max review texts to return (1..100). Counts always full.",
        ),
    ] = 20,
    sort: Annotated[
        str,
        Field(
            default="recent",
            description='"recent"/"newest"/"default" (as returned, newest-first), '
            '"best"/"highest" (highest rating first), '
            '"worst"/"lowest"/"complaints" (LOWEST rating first — finds downsides). '
            "Reorders the ~1000-review pool WB returns, not all feedbacks.",
        ),
    ] = "recent",
    ctx: Context | None = None,
) -> WbReviewsResponse:
    """Fetch reviews by imt_id (root_id from wb_root_info).

    All product variants share one review pool, indexed by imt_id NOT nmId.

    The WB feedbacks endpoint returns a fixed pool (~1000 most-recent reviews,
    newest-first) and IGNORES server-side order params (verified Nov 2026), so
    `sort` is applied CLIENT-SIDE over that pool. To surface complaints, "worst"
    reorders the returned pool by lowest rating first.

    Args:
        imt_id: Root ID from wb_root_info.
        limit: Max review texts to return (1..100). Counts always full.
        sort: "recent"/"newest"/"default" (as returned, newest-first),
              "best"/"highest" (highest rating first),
              "worst"/"lowest"/"complaints" (LOWEST rating first — finds downsides).
              Note: reorders the ~1000-review pool WB returns, not all feedbacks.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    log_event("wb_reviews.start", imt_id=imt_id, limit=limit, sort=sort)
    if ctx is not None:
        await ctx.info(f"wb_reviews: imt_id={imt_id} limit={limit} sort={sort}")

    if limit < 1 or limit > 100:
        log_event("wb_reviews.validation_failed", reason="bad_limit", limit=limit)
        raise_tool_error(BadRequestError("limit 1..100"))

    sort_norm = (sort or "recent").strip().lower()
    sort_mode = _WB_REVIEW_SORT_ALIASES.get(sort_norm, sort_norm)
    if sort_mode not in _WB_REVIEW_SORTS:
        log_event("wb_reviews.validation_failed", reason="bad_sort", sort=sort)
        raise_tool_error(
            BadRequestError(
                f"sort must be one of {sorted(_WB_REVIEW_SORT_ALIASES)} or {sorted(_WB_REVIEW_SORTS)} — got {sort!r}"
            )
        )
    await _polite_wait()

    try:
        attempts: list[dict[str, Any]] = []
        for host in WB_REVIEW_HOSTS:
            url = f"https://{host}/feedbacks/v2/{imt_id}"
            try:
                async with _wb_client() as client:
                    status_code, text, err = await _safe_get_text(client, url)
                if err:
                    attempts.append({"host": host, "status": status_code, "error": err})
                    continue
                if status_code != 200:
                    attempts.append({"host": host, "status": status_code, "error": f"HTTP {status_code}"})
                    continue
                try:
                    data = json.loads(text or "")
                except json.JSONDecodeError as exc:
                    attempts.append({"host": host, "status": status_code, "error": f"parse {exc}"})
                    continue
                data, shape_error = _expect_json_object(data, "wb_reviews response")
                data = _require_object(data, "wb_reviews response")
                if shape_error:
                    attempts.append({"host": host, "status": status_code, "error": shape_error["message"]})
                    continue
            except httpx.HTTPError as exc:
                attempts.append({"host": host, "status": 0, "error": f"{type(exc).__name__}: {exc}"})
                continue

            if "feedbacks" not in data:
                log_event("wb_reviews.shape_error", host=host, reason="feedbacks_missing")
                raise_tool_error(ParserDriftError(f"{host}: feedbacks missing from 200 response", provider="wb"))
            feedbacks_raw = data.get("feedbacks")
            if not isinstance(feedbacks_raw, list):
                log_event("wb_reviews.shape_error", host=host, reason="feedbacks_not_list")
                raise_tool_error(
                    ParserDriftError(
                        f"{host}: feedbacks expected list, got {type(feedbacks_raw).__name__}", provider="wb"
                    )
                )
            pool = [f for f in feedbacks_raw if isinstance(f, dict)]

            # WB returns the pool newest-first. Apply client-side reorder for
            # best/worst BEFORE slicing to limit, so "worst" actually surfaces the
            # lowest-rated reviews from the whole returned pool, not just page 1.
            # productValuation is coerced (audit: a string '5' crashed unary minus).
            def _val(f: dict) -> int:
                v = R.coerce_int(f.get("productValuation"))
                return v if v is not None else 0

            if sort_mode == "worst":
                pool.sort(key=lambda f: (_val(f) or 99, -_wb_review_date_ts(f.get("createdDate"))))
            elif sort_mode == "best":
                pool.sort(key=lambda f: (-_val(f), -_wb_review_date_ts(f.get("createdDate"))))
            # "recent": leave as returned (already newest-first)

            feedbacks: list[dict[str, Any]] = []
            for f in pool[:limit]:
                ud = f.get("wbUserDetails")
                uname = ud.get("name", "") if isinstance(ud, dict) else ""
                feedbacks.append(
                    {
                        "rating": f.get("productValuation"),
                        "text": _decode_mojibake(f.get("text", ""))[:1500],
                        "pros": _decode_mojibake(f.get("pros", ""))[:500],
                        "cons": _decode_mojibake(f.get("cons", ""))[:500],
                        "user": _decode_mojibake(uname)[:60],
                        "date": f.get("createdDate"),
                    }
                )
            warnings = R.validate_review_block(
                {
                    "returned": len(feedbacks),
                    "feedbacks": feedbacks,
                    "feedback_count": data.get("feedbackCount", len(feedbacks_raw)),
                    "valuation": data.get("valuation"),
                }
            )
            log_event("wb_reviews.done", imt_id=imt_id, host=host, returned=len(feedbacks), pool=len(feedbacks_raw))
            return WbReviewsResponse(
                imt_id=imt_id,
                sort=sort_mode,
                pool_size=len(feedbacks_raw),
                feedback_count=data.get("feedbackCount", len(feedbacks_raw)),
                valuation=data.get("valuation"),
                valuation_distribution=data.get("valuationDistribution"),
                feedbacks=[WbReviewItem(**f) for f in feedbacks],
                host_used=host,
                meta=MetaOut(source="wb_reviews", healthy=not warnings, warnings=warnings),
            )

        message = (
            "; ".join(f"{attempt['host']}: {attempt['error']}" for attempt in attempts) or "all review hosts failed"
        )
        log_event("wb_reviews.all_hosts_failed", attempts=len(attempts))
        raise_tool_error(TransportDownError(f"wb_reviews: {message}", provider="wb"))
    except ToolError:
        raise
    except Exception as exc:
        log_event("wb_reviews.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(str(exc)), provider="wb"))


@mcp.tool(
    name="wb_questions",
    annotations=ToolAnnotations(
        title="WB Buyer Questions by imt_id",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_questions(
    imt_id: Annotated[
        int,
        Field(
            gt=0,
            description="Root ID (imt_id) from wb_root_info. Questions are pooled per imt_id across every variant, NOT by nmId.",
        ),
    ],
    limit: Annotated[
        int,
        Field(
            default=30,
            ge=1,
            le=100,
            description="Max questions to return (1..100). Fetched in pages of 30, which is the upstream cap.",
        ),
    ] = 30,
    skip: Annotated[
        int,
        Field(
            default=0,
            ge=0,
            description="Offset into the question pool, for walking past the first page.",
        ),
    ] = 0,
    answered_only: Annotated[
        bool,
        Field(
            default=False,
            description="Return only questions the seller has answered. Unanswered questions carry no product information.",
        ),
    ] = False,
    ctx: Context | None = None,
) -> WbQuestionsResponse:
    """Fetch buyer questions and seller answers by imt_id (root_id from wb_root_info).

    Answers this tool exists for: buyers ask what a listing omits — "does it fit
    a 60cm opening", "is the cable included", "is this the 10A or the 16A model" —
    and the seller's reply is often the only public statement of that fact.
    Reviews describe the experience of owning the product; questions clarify what
    it actually is.

    Keyed by imt_id, exactly like ``wb_reviews``: every colour and size variant
    shares one question pool. Passing an nmId returns an empty pool with no error,
    so resolve the root id via ``wb_root_info`` first.

    Args:
        imt_id: Root ID from wb_root_info.
        limit: Max questions to return (1..100).
        skip: Offset into the pool, for pagination.
        answered_only: Keep only questions that have a seller answer.
        ctx: MCP context, when called as a tool.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    log_event("wb_questions.start", imt_id=imt_id, limit=limit, skip=skip, answered_only=answered_only)
    if ctx is not None:
        await ctx.info(f"wb_questions: imt_id={imt_id} limit={limit} skip={skip}")

    if limit < 1 or limit > 100:
        log_event("wb_questions.validation_failed", reason="bad_limit", limit=limit)
        raise_tool_error(BadRequestError("limit 1..100"))
    if skip < 0:
        log_event("wb_questions.validation_failed", reason="bad_skip", skip=skip)
        raise_tool_error(BadRequestError("skip must be >= 0"))

    try:
        collected: list[dict[str, Any]] = []
        total_available = 0
        offset = skip
        # Pages of 30 (the upstream cap), so a limit above 30 needs several passes.
        # answered_only filters after fetching, so the loop keeps going until the
        # caller's limit is met or the pool runs out — otherwise asking for 20
        # answered questions could return 3 simply because page 1 was mostly
        # unanswered.
        while len(collected) < limit:
            params = httpx.QueryParams(
                {
                    "imtId": str(imt_id),
                    "take": str(WB_QUESTIONS_PAGE_SIZE),
                    "skip": str(offset),
                }
            )
            url = f"https://{WB_QUESTIONS_HOST}/api/v1/questions?{params}"

            await _polite_wait()
            async with _wb_client() as client:
                status_code, text, err = await _safe_get_text(client, url)

            if err:
                log_event("wb_questions.network_error", error=_redact(err), skip=offset)
                raise_tool_error(TransportDownError(f"wb_questions: {err}", provider="wb"))
            if status_code == 429:
                log_event("wb_questions.rate_limited")
                raise_tool_error(RateLimitedError("wb", retry_after_s=30.0))
            if status_code != 200:
                log_event("wb_questions.http_error", status=status_code, skip=offset)
                raise_tool_error(
                    TransportDownError(f"wb_questions HTTP {status_code}", provider="wb", status_code=status_code)
                )

            try:
                data = json.loads(text or "")
            except json.JSONDecodeError as exc:
                log_event("wb_questions.parse_error", error=str(exc))
                raise_tool_error(ParserDriftError(f"wb_questions: invalid JSON ({exc})", provider="wb"))
            data, shape_error = _expect_json_object(data, "wb_questions response")
            data = _require_object(data, "wb_questions response")
            if shape_error:
                log_event("wb_questions.shape_error", reason=shape_error["message"])
                raise_tool_error(ParserDriftError(f"wb_questions: {shape_error['message']}", provider="wb"))

            # "count" is the contract's presence marker. Its absence means the
            # response shape changed, which must be loud — unlike a zero count,
            # which is a legitimate "nobody has asked anything yet".
            if "count" not in data:
                log_event("wb_questions.shape_error", reason="count_missing")
                raise_tool_error(ParserDriftError("wb_questions: count missing from 200 response", provider="wb"))

            total_available = R.coerce_int(data.get("count")) or 0

            raw_questions = data.get("questions")
            # A product with no questions returns questions: null, not [].
            if raw_questions is None:
                break
            if not isinstance(raw_questions, list):
                log_event("wb_questions.shape_error", reason="questions_not_list")
                raise_tool_error(
                    ParserDriftError(
                        f"wb_questions: questions expected list or null, got {type(raw_questions).__name__}",
                        provider="wb",
                    )
                )

            page = [q for q in raw_questions if isinstance(q, dict)]
            if not page:
                break

            for raw in page:
                if len(collected) >= limit:
                    break
                answer = raw.get("answer")
                answer = answer if isinstance(answer, dict) else {}
                answer_text = _normalise_ws(_decode_mojibake(answer.get("text")))
                if answered_only and not answer_text:
                    continue
                user_details = raw.get("wbUserDetails")
                user_name = user_details.get("name", "") if isinstance(user_details, dict) else ""
                collected.append(
                    {
                        "question_id": str(raw.get("id") or ""),
                        "text": _normalise_ws(_decode_mojibake(raw.get("text")))[:1500],
                        "date": raw.get("createdDate"),
                        "user": _decode_mojibake(user_name)[:60],
                        "answered": bool(answer_text),
                        "answer_text": answer_text[:1500],
                        "answer_date": answer.get("createDate"),
                        "nm_id": R.coerce_int(raw.get("nmId")),
                    }
                )

            offset += len(page)
            # A short page means the pool is exhausted; so does reaching the
            # reported total. Either way, stop rather than requesting past the end.
            if len(page) < WB_QUESTIONS_PAGE_SIZE or offset >= total_available:
                break

        answered_count = sum(1 for q in collected if q["answered"])
        warnings: list[str] = []
        if answered_only and not collected and total_available:
            warnings.append(f"none of the {total_available} question(s) on this product have a seller answer yet")

        log_event(
            "wb_questions.done",
            imt_id=imt_id,
            returned=len(collected),
            answered=answered_count,
            total=total_available,
        )
        return WbQuestionsResponse(
            imt_id=imt_id,
            total_available=total_available,
            returned=len(collected),
            skip=skip,
            answered_count=answered_count,
            has_more=offset < total_available,
            questions=[WbQuestionItem(**q) for q in collected],
            meta=MetaOut(source="wb_questions", healthy=not warnings, warnings=warnings),
        )
    except ToolError:
        raise
    except Exception as exc:
        log_event("wb_questions.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(str(exc)), provider="wb"))


@mcp.tool(
    name="wb_search",
    annotations=ToolAnnotations(
        title="WB Catalog Search",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_search(
    query: Annotated[
        str,
        Field(
            min_length=1,
            description="Russian text search query for the WB catalog.",
        ),
    ],
    dest: Annotated[
        str,
        Field(
            default=WB_DEFAULT_DEST,
            description="Region ID. Default -1257786 (Moscow). Required for valid prices/stocks.",
        ),
    ] = WB_DEFAULT_DEST,
    page: Annotated[
        int,
        Field(
            default=1,
            ge=1,
            le=20,
            description="Page number 1..20 (each page returns ~30 IDs from the long list).",
        ),
    ] = 1,
    ctx: Context | None = None,
) -> WbSearchResponse | WbNoResultsResponse:
    """Search WB catalog by text query.

    Uses the lightweight `search-goods.wildberries.ru/search` endpoint which
    returns just product IDs (no PoW protection, very high rate limit). Then
    enriches via `wb_card` for full details.

    Args:
        query: Russian text.
        dest: Region. Default Moscow.
        page: 1..20 (each page returns ~30 IDs from the long list).

    ## Return Format

    On a match, returns WbSearchResponse. When the catalog has no ids for the
    query (or the page is past the end), returns WbNoResultsResponse — this is
    NOT an error. On validation/transport/parse failure, raises ToolError.
    """
    log_event("wb_search.start", query=query[:100], page=page, dest=dest)
    if ctx is not None:
        await ctx.info(f"wb_search: query={query[:80]!r} page={page} dest={dest}")

    if page < 1 or page > 20:
        log_event("wb_search.validation_failed", reason="bad_page", page=page)
        raise_tool_error(BadRequestError("page 1..20"))

    try:
        # PRIMARY: search.wb.ru v9 returns fully-populated product objects — same
        # shape as card/v4 — in a single request, so prices and stock come straight
        # from the search index.
        #
        # This replaced a two-step search-goods -> card/v4 pipeline (fixed Jul 2026).
        # search-goods.wildberries.ru serves a *stale* id list: for "кроссовки
        # мужские" every id it returned was a delisted SKU with totalQuantity=0 and
        # price=null, while v9 returned 100 in-stock products with real prices. The
        # old path produced technically-valid responses in which nothing had a
        # price — worse than an error, because it looked like an answer.
        v9_params = httpx.QueryParams(
            {
                "appType": "1",
                "curr": "rub",
                "dest": dest,
                "locale": "ru",
                "query": query,
                "resultset": "catalog",
                "page": str(page),
                "spp": "30",
            }
        )
        v9_url = f"https://search.wb.ru/exactmatch/ru/common/v9/search?{v9_params}"
        if ctx:
            await ctx.debug(f"wb_search: {v9_url}")
        await _polite_wait()

        products: list[Any] = []
        total_found: int | None = None
        v9_error: str | None = None

        try:
            async with _wb_client() as client:
                status_code, text, err = await _safe_get_text(client, v9_url)
            if err:
                v9_error = err
            elif status_code == 429:
                # v9 is 429-prone by design; surface it honestly rather than
                # silently degrading to the stale-id path.
                log_event("wb_search.rate_limited", endpoint="v9")
                raise_tool_error(RateLimitedError("wb", retry_after_s=60.0))
            elif status_code != 200:
                v9_error = f"HTTP {status_code}"
            else:
                try:
                    v9_data = json.loads(text or "")
                except json.JSONDecodeError as exc:
                    v9_error = f"invalid JSON: {exc}"
                else:
                    v9_obj, v9_shape_error = _expect_json_object(v9_data, "wb_search v9 response")
                    if v9_shape_error or v9_obj is None:
                        v9_error = "unexpected response shape"
                    else:
                        raw_products = v9_obj.get("products")
                        if not isinstance(raw_products, list):
                            nested = v9_obj.get("data")
                            if isinstance(nested, dict) and isinstance(nested.get("products"), list):
                                raw_products = nested["products"]
                        if isinstance(raw_products, list):
                            products = raw_products
                            total_found = R.coerce_int(v9_obj.get("total"))
                            if total_found is None:
                                metadata = v9_obj.get("metadata")
                                if isinstance(metadata, dict):
                                    total_found = R.coerce_int(metadata.get("total"))
                        else:
                            v9_error = "no products array in response"
        except ToolError:
            raise
        except httpx.HTTPError as exc:
            v9_error = f"{type(exc).__name__}: {exc}"

        fallback_used = False
        if not products:
            # FALLBACK: the legacy two-step path. Its ids skew stale, but a stale
            # result beats no result when v9 is unavailable, and the response is
            # flagged so the caller knows which path produced it.
            log_event("wb_search.v9_unavailable", error=_redact(v9_error or "empty result"))
            fallback_used = True
            products, total_found = await _search_via_search_goods(query, dest, page, ctx)

        if not products:
            log_event("wb_search.no_results", query=query[:100])
            return WbNoResultsResponse(query=query, page=page, total_ids=total_found or 0)

        page_size = 100 if not fallback_used else 30
        item_dicts: list[dict[str, Any]] = []
        for p in products:
            if not isinstance(p, dict):
                continue  # a non-object entry must never crash the tool
            item_dicts.append(_card_item_dict(p))

        warnings = _aggregate_offer_warnings(item_dicts)
        if fallback_used:
            warnings.append(
                "fallback: v9 search was unavailable, results came from the legacy id path "
                "and may include delisted items without prices"
            )
        priced = sum(1 for d in item_dicts if d["price_rub"] is not None)
        if item_dicts and priced == 0:
            warnings.append("no_prices: every result lacks a price, which usually means the whole page is delisted")

        log_event(
            "wb_search.done",
            query=query[:100],
            page=page,
            items=len(item_dicts),
            priced=priced,
            total=total_found,
            fallback=fallback_used,
        )
        return WbSearchResponse(
            query=query,
            page=page,
            page_size=page_size,
            total_ids=total_found or len(item_dicts),
            count=len(item_dicts),
            items=[WbCardItem(**d) for d in item_dicts],
            meta=MetaOut(source="wb_search", healthy=not warnings, warnings=warnings),
        )
    except ToolError:
        raise
    except Exception as exc:
        log_event("wb_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(str(exc)), provider="wb"))


def _recover_search_ids(ids: Any) -> list[int]:
    """PURE: recover numeric product ids from a search-goods response (no network).

    Factored from wb_search STEP 2 (audit 2026-06-01) so wb_selfcheck can assert
    the id-list shape offline. Handles the live shapes the wb_search drift-guard
    already covers: bare ints, numeric strings, and id-objects ({id|nmId|nm_id}).
    """
    out: list[int] = []
    if not isinstance(ids, list):
        return out

    def _as_int(s: str) -> int | None:
        # str.isdigit() is True for non-decimal Unicode digits (e.g. '²') that
        # int() rejects — guard with int() in a try so a hostile id string can
        # never crash the canary (audit 2026-06-01 CRASH_HANG).
        s = s.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    for i in ids:
        if isinstance(i, bool):
            continue
        if isinstance(i, int):
            if i > 0:
                out.append(i)
        elif isinstance(i, str):
            v = _as_int(i)
            if v is not None and v > 0:
                out.append(v)
        elif isinstance(i, dict):
            cand = R.first_present(i, "id", "nmId", "nm_id")
            if isinstance(cand, bool):
                continue
            if isinstance(cand, int):
                if cand > 0:
                    out.append(cand)
            elif isinstance(cand, str):
                v = _as_int(cand)
                if v is not None and v > 0:
                    out.append(v)
    return out


def _static_seller_urls(supplier_id: int) -> list[str]:
    """Candidate URLs for a seller record, one per static CDN mirror."""
    return [f"https://{h}/vol0/data/supplier-by-id/{supplier_id}.json" for h in WB_STATIC_HOSTS]


def _static_menu_urls() -> list[str]:
    """Candidate URLs for the catalog menu, one per static CDN mirror."""
    return [f"https://{h}/vol0/data/main-menu-ru-ru-v3.json" for h in WB_STATIC_HOSTS]


async def _fetch_first_json(urls: list[str], label: str, ctx: Context | None) -> tuple[dict | list, str]:
    """GET each URL in turn, returning the first parsed JSON body and its host.

    Mirror hosts fail independently, so a single 404/5xx is not a real outage —
    only exhausting every mirror is. The last error is surfaced so the caller can
    distinguish "all mirrors down" from "this id does not exist".
    """
    last_error = ""
    async with _wb_client() as client:
        for url in urls:
            await _polite_wait()
            if ctx:
                await ctx.debug(f"{label}: {url}")
            status_code, text, err = await _safe_get_text(client, url)
            if err:
                last_error = f"{err} [url={url}]"
                continue
            if status_code == 404:
                last_error = f"HTTP 404 [url={url}]"
                continue
            if status_code == 429:
                raise_tool_error(RateLimitedError("wb"))
            if status_code != 200:
                last_error = f"HTTP {status_code} [url={url}]"
                continue
            if not text:
                last_error = f"empty body [url={url}]"
                continue
            try:
                return json.loads(text), url.split("/")[2]
            except json.JSONDecodeError as exc:
                last_error = f"invalid JSON from {url.split('/')[2]}: {exc}"
                continue
    raise_tool_error(TransportDownError(_redact(last_error) or f"{label}: all mirrors failed", provider="wb"))
    raise AssertionError("unreachable")  # pragma: no cover - raise_tool_error is NoReturn


async def _search_via_search_goods(
    query: str, dest: str, page: int, ctx: Context | None
) -> tuple[list[Any], int | None]:
    """Legacy fallback: search-goods ids, then enrich through card/v4.

    Kept only as a safety net for when ``search.wb.ru`` v9 is unreachable. Its ids
    skew stale — verified Jul 2026, a query whose v9 results were all in stock came
    back from this path entirely delisted — so callers are warned when it is used.

    Returns ``(products, total)`` and never raises for an empty result: an empty
    list simply means "nothing usable", which the caller reports as no results.
    """
    sg_params = httpx.QueryParams({"query": query, "dest": dest})
    sg_url = f"https://search-goods.wildberries.ru/search?{sg_params}"
    if ctx:
        await ctx.debug(f"wb_search fallback: {sg_url}")
    await _polite_wait()

    try:
        async with _wb_client() as client:
            status_code, text, err = await _safe_get_text(client, sg_url)
        if err or status_code != 200 or not text:
            log_event("wb_search.fallback_failed", status=status_code, error=_redact(err or ""))
            return [], None
        ids = json.loads(text)
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log_event("wb_search.fallback_failed", error=_redact(str(exc)))
        return [], None

    if not isinstance(ids, list) or not ids:
        return [], None

    page_size = 30
    start = (page - 1) * page_size
    page_ids = _recover_search_ids(ids[start : start + page_size])
    if not page_ids:
        return [], len(ids)

    card_params = httpx.QueryParams(
        {
            "appType": "1",
            "curr": "rub",
            "dest": dest,
            "locale": "ru",
            "spp": "30",
            "nm": ";".join(str(n) for n in page_ids),
        }
    )
    card_url = f"https://card.wb.ru/cards/v4/detail?{card_params}"
    await _polite_wait()

    try:
        async with _wb_client() as client:
            status_code, text, err = await _safe_get_text(client, card_url)
        if err or status_code != 200 or not text:
            log_event("wb_search.fallback_enrich_failed", status=status_code, error=_redact(err or ""))
            return [], len(ids)
        card_data = json.loads(text)
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log_event("wb_search.fallback_enrich_failed", error=_redact(str(exc)))
        return [], len(ids)

    card_obj, shape_error = _expect_json_object(card_data, "wb_search fallback card response")
    if shape_error or card_obj is None:
        return [], len(ids)
    products, products_error = _card_products_checked(card_obj)
    if products_error:
        log_event("wb_search.fallback_enrich_failed", error=products_error)
        return [], len(ids)
    return products, len(ids)


@mcp.tool(
    name="wb_seller",
    annotations=ToolAnnotations(
        title="WB Seller Legal Info",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_seller(
    supplier_id: Annotated[
        int,
        Field(
            gt=0,
            description="WB supplier id (positive integer). Get it from wb_card's supplier_id field.",
        ),
    ],
    ctx: Context | None = None,
) -> WbSellerResponse:
    """Look up the registered legal entity behind a WB seller.

    Answers "who actually ships this?" — the question a marketplace listing is
    worst at. Returns the registered name, tax ids (INN/KPP/OGRN) and legal
    address, which is how you tell an official brand store from a reseller
    trading under a lookalike name, and how you spot several storefronts sharing
    one entity.

    Chain from `wb_card`: its `supplier_id` field feeds straight into this tool.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    log_event("wb_seller.start", supplier_id=supplier_id)
    if ctx is not None:
        await ctx.info(f"wb_seller: supplier_id={supplier_id}")

    data, host_used = await _fetch_first_json(_static_seller_urls(supplier_id), "wb_seller", ctx)

    obj, drift = _expect_json_object(data, "wb_seller")
    if obj is None:
        raise_tool_error(ParserDriftError(f"wb_seller: unexpected payload shape {drift}", provider="wb"))

    warnings: list[str] = []
    name = _decode_mojibake(R.first_present(obj, "supplierName", "name", default=""))
    if not name:
        # An empty record means WB has no entity for this id — a caller passing a
        # nmId instead of a supplier_id lands here, so say so explicitly.
        raise_tool_error(
            NotFoundError(
                f"no seller record for supplier_id={supplier_id} (is this a supplier_id from wb_card, not an nmId?)",
                provider="wb",
            )
        )

    foreign = {key: str(obj[key]).strip() for key in ("unp", "bin", "unn") if str(obj.get(key) or "").strip()}
    inn = str(R.first_present(obj, "inn", default="") or "").strip()
    if not inn and not foreign:
        warnings.append("no_tax_id: neither INN nor an EAEU registration code is present")

    result = WbSellerResponse(
        supplier_id=supplier_id,
        name=name,
        full_name=_decode_mojibake(R.first_present(obj, "supplierFullName", "fullName", default="")),
        trademark=_decode_mojibake(R.first_present(obj, "trademark", default="")),
        inn=inn,
        kpp=str(R.first_present(obj, "kpp", default="") or "").strip(),
        ogrn=str(R.first_present(obj, "ogrn", "ogrnip", default="") or "").strip(),
        legal_address=_decode_mojibake(R.first_present(obj, "legalAddress", "address", default="")),
        taxpayer_code=str(R.first_present(obj, "taxpayerCode", default="") or "").strip(),
        foreign_codes=foreign,
        host_used=host_used,
        meta=MetaOut(source="wb_seller", healthy=not warnings, warnings=warnings),
    )
    log_event("wb_seller.done", supplier_id=supplier_id, has_inn=bool(inn))
    return result


def _menu_node(raw: Any, depth: int, budget: list[int], max_depth: int) -> WbCategoryNode | None:
    """Convert one raw menu entry into a bounded WbCategoryNode.

    ``budget`` is a single-element list used as a mutable counter: the whole
    traversal shares one node allowance so a wide subtree cannot blow the
    response size. Returns None once the budget is exhausted.
    """
    if budget[0] <= 0 or not isinstance(raw, dict):
        return None
    budget[0] -= 1

    raw_children = raw.get("childs")
    children_list = raw_children if isinstance(raw_children, list) else []

    children: list[WbCategoryNode] = []
    if depth < max_depth:
        for child in children_list:
            node = _menu_node(child, depth + 1, budget, max_depth)
            if node is None:
                break
            children.append(node)

    return WbCategoryNode(
        id=R.coerce_int(R.first_present(raw, "id", default=None)),
        name=_decode_mojibake(R.first_present(raw, "name", default="")),
        url=str(R.first_present(raw, "url", default="") or ""),
        shard=str(R.first_present(raw, "shard", default="") or ""),
        query=str(R.first_present(raw, "query", default="") or ""),
        depth=depth,
        children_count=len(children_list),
        children=children,
    )


def _find_menu_subtree(nodes: list[Any], needle: str) -> dict | None:
    """Breadth-first search for a category by name, url or id.

    Breadth-first on purpose: a short query like "обувь" should resolve to the
    top-level section, not to whichever deep subcategory happens to match first.
    """
    needle_low = needle.strip().lower()
    queue: list[Any] = list(nodes)
    while queue:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        name = _decode_mojibake(node.get("name", "")).strip().lower()
        url = str(node.get("url") or "").strip().lower()
        node_id = str(node.get("id") or "")
        if needle_low in (name, url, node_id) or url.endswith(f"/{needle_low}"):
            return node
        children = node.get("childs")
        if isinstance(children, list):
            queue.extend(children)
    return None


@mcp.tool(
    name="wb_categories",
    annotations=ToolAnnotations(
        title="WB Catalog Categories",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_categories(
    root: Annotated[
        str,
        Field(
            default="top",
            max_length=200,
            description=(
                "'top' for the top-level sections, or a category name / URL path / id to expand "
                "(e.g. 'Электроника', '/catalog/elektronika', '8126')."
            ),
        ),
    ] = "top",
    max_depth: Annotated[
        int,
        Field(
            default=1,
            ge=1,
            le=3,
            description="How many levels below the root to include. 1 = direct children only.",
        ),
    ] = 1,
    ctx: Context | None = None,
) -> WbCategoriesResponse:
    """Browse the Wildberries catalog tree.

    Use this to discover what exists before searching: `wb_search` needs a query
    string, but a shopper's question is often "what categories of humidifiers are
    there?". Each node carries WB's own `shard` and `query` selectors, which are
    the addressing needed to pull a category feed.

    The live menu is ~800 KB, so responses are always a bounded slice — start at
    'top', then expand the branch you care about.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    log_event("wb_categories.start", root=root, max_depth=max_depth)
    if ctx is not None:
        await ctx.info(f"wb_categories: root={root!r} depth={max_depth}")

    data, host_used = await _fetch_first_json(_static_menu_urls(), "wb_categories", ctx)

    if not isinstance(data, list) or not data:
        raise_tool_error(
            ParserDriftError(
                f"wb_categories: expected a non-empty menu array, got {type(data).__name__}",
                provider="wb",
            )
        )

    requested = (root or "top").strip()
    if requested.lower() in ("", "top", "root", "all"):
        nodes: list[Any] = data
        resolved = "top"
    else:
        subtree = _find_menu_subtree(data, requested)
        if subtree is None:
            raise_tool_error(
                NotFoundError(
                    f"category {requested!r} not found in the WB menu — call wb_categories('top') to list sections",
                    provider="wb",
                )
            )
        children = subtree.get("childs")
        nodes = children if isinstance(children, list) else []
        resolved = _decode_mojibake(subtree.get("name", requested))

    budget = [WB_MENU_MAX_NODES]
    items: list[WbCategoryNode] = []
    for raw in nodes:
        node = _menu_node(raw, 0, budget, max_depth - 1)
        if node is None:
            break
        items.append(node)

    truncated = budget[0] <= 0
    total = WB_MENU_MAX_NODES - budget[0]
    warnings = ["truncated: node budget reached — narrow the root or lower max_depth"] if truncated else []

    log_event("wb_categories.done", root=resolved, returned=total, truncated=truncated)
    return WbCategoriesResponse(
        root=resolved,
        max_depth=max_depth,
        total_returned=total,
        truncated=truncated,
        items=items,
        host_used=host_used,
        meta=MetaOut(source="wb_categories", healthy=not truncated, warnings=warnings),
    )


@mcp.tool(
    name="wb_category_products",
    annotations=ToolAnnotations(
        title="WB Category Product Listing",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_category_products(
    shard: Annotated[
        str,
        Field(
            max_length=120,
            description="WB catalog shard from wb_categories, e.g. 'electronic58'. The literal 'blackhole' means the category has no listable feed.",
        ),
    ],
    query: Annotated[
        str,
        Field(
            max_length=200,
            description="WB catalog selector from wb_categories, e.g. 'cat=9845' or 'subject=1234'.",
        ),
    ],
    page: Annotated[
        int,
        Field(
            default=1,
            ge=1,
            le=100,
            description="Page number. Each page carries up to 100 products.",
        ),
    ] = 1,
    sort: Annotated[
        str,
        Field(
            default="popular",
            description="Upstream ordering: popular, priceup, pricedown, newly, rate, or benefit.",
        ),
    ] = "popular",
    dest: Annotated[
        str,
        Field(
            default="",
            description="WB region id. Defaults to WB_DEFAULT_DEST (Moscow). Prices and stock are region-specific.",
        ),
    ] = "",
    ctx: Context | None = None,
) -> WbCategoryProductsResponse:
    """List the products in a catalog category, using the shard and query from wb_categories.

    This closes the loop `wb_categories` opens. That tool hands back WB's own
    `shard` and `query` selectors — the address of a category feed — and this is
    the tool that fetches it. Browsing "what humidifiers exist" no longer requires
    inventing a search phrase and hoping WB's relevance ranking agrees with you.

    Items come back in the same shape `wb_search` and `wb_card` return, so a
    category walk and a text search are directly comparable.

    **Not every category has a feed.** WB marks those with the shard
    `blackhole`, and several of its largest sections (smartphones, laptops, TV and
    audio) are among them: they exist as navigation, not as a listable endpoint.
    Asking for one raises a clear error naming the alternative rather than
    returning an empty list, because an empty list here would read as "this
    category has no products", which is false.

    Args:
        shard: Catalog shard from wb_categories.
        query: Catalog selector from wb_categories (`cat=` or `subject=`).
        page: Page number, up to 100 products each.
        sort: Upstream ordering.
        dest: WB region id; defaults to WB_DEFAULT_DEST.
        ctx: MCP context, when called as a tool.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    shard_norm = (shard or "").strip()
    query_norm = (query or "").strip().lstrip("?&")
    sort_norm = (sort or "popular").strip().lower()
    dest_used = (dest or "").strip() or WB_DEFAULT_DEST

    log_event("wb_category_products.start", shard=shard_norm, query=query_norm, page=page, sort=sort_norm)
    if ctx is not None:
        await ctx.info(f"wb_category_products: shard={shard_norm} {query_norm} page={page}")

    if not shard_norm:
        log_event("wb_category_products.validation_failed", reason="empty_shard")
        raise_tool_error(BadRequestError("shard is required — take it from wb_categories"))
    if not query_norm:
        log_event("wb_category_products.validation_failed", reason="empty_query")
        raise_tool_error(BadRequestError("query is required — take it from wb_categories, e.g. 'cat=9845'"))
    if sort_norm not in WB_CATEGORY_SORTS:
        log_event("wb_category_products.validation_failed", reason="bad_sort", sort=sort)
        raise_tool_error(BadRequestError(f"sort must be one of {sorted(WB_CATEGORY_SORTS)} — got {sort!r}"))
    if not _WB_SHARD_RE.match(shard_norm):
        log_event("wb_category_products.validation_failed", reason="bad_shard", shard=shard_norm)
        raise_tool_error(BadRequestError("shard must look like a WB shard name, e.g. 'electronic58'"))
    if not _WB_CATEGORY_QUERY_RE.match(query_norm):
        log_event("wb_category_products.validation_failed", reason="bad_query", query=query_norm)
        raise_tool_error(BadRequestError("query must be a WB catalog selector such as 'cat=9845' or 'subject=1234'"))

    # Refused before spending a request: 'blackhole' is WB's marker for a category
    # with no feed of its own, confirmed live (a clean 404 on every retry). Saying
    # so is the honest answer; an empty item list would claim the category is
    # empty, which is a different and false statement.
    if shard_norm == WB_UNLISTABLE_SHARD:
        log_event("wb_category_products.unlistable", query=query_norm)
        raise_tool_error(
            BadRequestError(
                f"this category is not directly listable: wb_categories reported shard "
                f"'{WB_UNLISTABLE_SHARD}', which WB uses for sections that exist only as "
                f"navigation. Expand it with wb_categories(root=...) and list a subcategory, "
                f"or use wb_search for a text query."
            )
        )

    params = httpx.QueryParams(
        {
            "appType": "1",
            "curr": "rub",
            "dest": dest_used,
            "locale": "ru",
            "page": str(page),
            "sort": sort_norm,
            "spp": "30",
        }
    )
    # query already carries its own key=value (cat=/subject=), so it is appended
    # verbatim after validation rather than re-encoded.
    url = f"https://catalog.wb.ru/catalog/{shard_norm}/v4/catalog?{params}&{query_norm}"

    await _polite_wait()
    try:
        async with _wb_client() as client:
            status_code, text, err = await _safe_get_text(client, url)

        if err:
            log_event("wb_category_products.network_error", error=_redact(err))
            raise_tool_error(TransportDownError(f"wb_category_products: {err}", provider="wb"))
        if status_code == 429:
            log_event("wb_category_products.rate_limited")
            raise_tool_error(RateLimitedError("wb", retry_after_s=30.0))
        if status_code == 404:
            # A real shard answering 404 means this shard/query pair is not a
            # category feed — usually a stale selector from a cached menu.
            log_event("wb_category_products.not_found", shard=shard_norm, query=query_norm)
            raise_tool_error(
                NotFoundError(
                    f"no category feed at shard {shard_norm!r} for {query_norm!r} — "
                    "re-read the selectors from wb_categories, they change with the menu",
                    provider="wb",
                )
            )
        if text and "<html" in text[:200].lower():
            log_event("wb_category_products.blocked", reason="cloudflare_html")
            raise_tool_error(TransportDownError("Cloudflare HTML page (likely missing dest param)", provider="wb"))
        if status_code != 200:
            log_event("wb_category_products.http_error", status=status_code)
            raise_tool_error(
                TransportDownError(f"wb_category_products HTTP {status_code}", provider="wb", status_code=status_code)
            )

        try:
            data = json.loads(text or "")
        except json.JSONDecodeError as exc:
            log_event("wb_category_products.parse_error", error=_redact(str(exc)))
            raise_tool_error(ParserDriftError(f"wb_category_products: invalid JSON ({exc})", provider="wb"))

        data, shape_error = _expect_json_object(data, "wb_category_products response")
        data = _require_object(data, "wb_category_products response")
        if shape_error:
            log_event("wb_category_products.shape_error", error=_redact(shape_error.get("message", "")))
            raise_tool_error(ParserDriftError(shape_error.get("message", ""), provider="wb"))

        products, products_error = _card_products_checked(data)
        if products_error:
            log_event("wb_category_products.shape_error", error=products_error)
            raise_tool_error(ParserDriftError(f"wb_category_products response {products_error}", provider="wb"))

        item_dicts = [_card_item_dict(p) for p in products if isinstance(p, dict)]
        warnings = _aggregate_offer_warnings(item_dicts)

        log_event(
            "wb_category_products.done",
            shard=shard_norm,
            query=query_norm,
            page=page,
            count=len(item_dicts),
        )
        return WbCategoryProductsResponse(
            shard=shard_norm,
            query=query_norm,
            page=page,
            sort=sort_norm,
            dest=dest_used,
            count=len(item_dicts),
            # A full page implies more may follow; WB reports no total here.
            has_more=len(item_dicts) >= WB_CATEGORY_PAGE_SIZE,
            items=[WbCardItem(**d) for d in item_dicts],
            meta=MetaOut(source="wb_category_products", healthy=not warnings, warnings=warnings),
        )
    except ToolError:
        raise
    except Exception as exc:
        log_event("wb_category_products.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(str(exc)), provider="wb"))


@mcp.tool(
    name="wb_selfcheck",
    annotations=ToolAnnotations(
        title="WB Self-check (drift canary)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def wb_selfcheck(ctx: Context | None = None) -> WbSelfCheckResponse:
    """Structural drift canary for WB (tri-state: success / drift_detected /
    inconclusive). Probes EVERY endpoint family the tools depend on:
      * card        — card.wb.ru v4 (wb_card / wb_search enrich): critical fields
                      + price extract.
      * reviews     — feedbacks2.wb.ru pool (wb_reviews): texts + productValuation.
      * search_goods— search-goods.wildberries.ru (wb_search STEP 1): the id list
                      must still be a non-empty list of recoverable ids on a broad
                      evergreen query, else wb_search silently returns no_results.
      * root_basket — basket-NN.wbbasket.ru (wb_root_info): imt_id must resolve,
                      else wb_root_info AND wb_reviews (indexed by imt_id) break.

    Tri-state (audit 2026-06-01): an http!=200 / network error / OOS baseline is
    `inconclusive` (transport/baseline rot), NEVER drift. Only a reached-200 body
    whose parser-critical anchor is gone is `drift`. Run on demand before trusting
    a batch.
    """
    log_event("wb_selfcheck.start")
    if ctx is not None:
        await ctx.info("wb_selfcheck: probing card / reviews / search_goods / root_basket")
    checks: dict[str, dict] = {}

    # --- card v4 ---
    nm = _SELFCHECK_NM
    card_url = f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest={WB_DEFAULT_DEST}&locale=ru&spp=30&nm={nm}"
    await _polite_wait()
    try:
        async with asyncio.timeout(45):  # whole-subcheck wall-clock (audit CRASH_HANG)
            async with _wb_client() as client:
                status_code, text, err = await _safe_get_text(client, card_url)
            if err or status_code != 200:
                checks["card"] = R.selfcheck_entry(
                    "inconclusive", baseline=str(nm), reason="transport_down", notes=[f"http {status_code} err={err}"]
                )
            else:
                try:
                    data = json.loads(text or "")
                except json.JSONDecodeError as exc:
                    checks["card"] = R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        reason="parse_error",
                        notes=[f"card 200 JSON parse error: {str(exc)[:120]}"],
                    )
                    data = None
                if not isinstance(data, dict):
                    if data is not None:
                        checks["card"] = R.selfcheck_entry(
                            "drift",
                            baseline=str(nm),
                            reason="schema_drift",
                            notes=[f"card 200 root is {type(data).__name__}, not object"],
                        )
                    elif "card" not in checks:
                        checks["card"] = R.selfcheck_entry(
                            "drift",
                            baseline=str(nm),
                            reason="schema_drift",
                            notes=["card 200 root is null, not object"],
                        )
                    products: list[Any] = []
                else:
                    products, products_error = _card_products_checked(data)
                    if products_error:
                        checks["card"] = R.selfcheck_entry(
                            "drift",
                            baseline=str(nm),
                            reason="schema_drift",
                            notes=[f"card 200 products container error: {products_error}"],
                        )
                if not isinstance(data, dict) or ("card" in checks and checks["card"].get("state") == "drift"):
                    pass
                elif not products:
                    # A SINGLE-SKU baseline returning 0 products is indistinguishable
                    # from deletion/OOS at this endpoint — baseline rot, NOT drift
                    # (audit 2026-06-01 FALSE_POSITIVE: don't cry wolf on a gone SKU).
                    checks["card"] = R.selfcheck_entry(
                        "inconclusive",
                        baseline=str(nm),
                        reason="baseline_gone",
                        notes=[
                            "no products on 200 body (baseline SKU deleted/OOS, "
                            "or schema drift — cannot distinguish on one SKU)"
                        ],
                    )
                else:
                    p = products[0]
                    if not isinstance(p, dict):
                        checks["card"] = R.selfcheck_entry(
                            "drift",
                            baseline=str(nm),
                            reason="schema_drift",
                            notes=[f"card product entry is {type(p).__name__}, not object"],
                        )
                    else:
                        missing = [
                            k
                            for k in ("id", "name", "sizes", "reviewRating", "feedbacks", "totalQuantity")
                            if k not in p
                        ]
                        cur, _orig = _extract_price_rub(p)
                        qty = R.coerce_int(p.get("totalQuantity"))
                        if missing:
                            checks["card"] = R.selfcheck_entry(
                                "drift",
                                baseline=str(nm),
                                missing_fields=missing,
                                notes=[f"card missing critical fields: {missing}"],
                            )
                        elif qty is None:
                            checks["card"] = R.selfcheck_entry(
                                "drift",
                                baseline=str(nm),
                                reason="quantity_shape_drift",
                                notes=["card totalQuantity is present but not parseable as an integer"],
                            )
                        elif cur is None and qty and qty > 0:
                            checks["card"] = R.selfcheck_entry(
                                "drift",
                                baseline=str(nm),
                                reason="price_shape_drift",
                                notes=["in-stock baseline has positive totalQuantity but no parseable price"],
                            )
                        elif cur is None:
                            # price None is legit if the baseline SKU went OOS — rot, not drift.
                            checks["card"] = R.selfcheck_entry(
                                "inconclusive",
                                baseline=str(nm),
                                reason="baseline_gone",
                                notes=["price None (baseline SKU may be out of stock)"],
                            )
                        else:
                            checks["card"] = R.selfcheck_entry("healthy", baseline=str(nm), price=cur)
    except (TimeoutError, Exception) as exc:
        checks["card"] = R.selfcheck_entry(
            "inconclusive",
            baseline=str(nm),
            reason="timeout" if isinstance(exc, asyncio.TimeoutError) else "transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )

    # --- reviews (feedbacks pool by imt_id) ---
    await _polite_wait()
    try:
        async with asyncio.timeout(45):
            attempts: list[dict[str, Any]] = []
            review_payload: Any = None
            used_host = ""
            async with _wb_client() as client:
                for host in WB_REVIEW_HOSTS:
                    status_code, text, err = await _safe_get_text(
                        client, f"https://{host}/feedbacks/v2/{_SELFCHECK_IMT}"
                    )
                    if err or status_code != 200:
                        attempts.append(
                            {
                                "host": host,
                                "status": status_code,
                                "error": err or f"HTTP {status_code}",
                            }
                        )
                        continue
                    used_host = host
                    try:
                        review_payload = json.loads(text or "")
                    except json.JSONDecodeError as exc:
                        checks["reviews"] = R.selfcheck_entry(
                            "drift",
                            baseline=str(_SELFCHECK_IMT),
                            reason="parse_error",
                            notes=[f"{host} returned HTTP 200 but invalid JSON: {exc}"],
                        )
                        review_payload = None
                    break
            if review_payload is None and used_host and "reviews" not in checks:
                checks["reviews"] = R.selfcheck_entry(
                    "drift",
                    baseline=str(_SELFCHECK_IMT),
                    reason="schema_drift",
                    notes=[f"{used_host} returned null, not object"],
                )
            elif review_payload is None and "reviews" not in checks:
                checks["reviews"] = R.selfcheck_entry(
                    "inconclusive",
                    baseline=str(_SELFCHECK_IMT),
                    reason="transport_down",
                    notes=[f"{a['host']} http {a['status']} err={a['error']}" for a in attempts],
                )
            if review_payload is None:
                pass
            else:
                if not isinstance(review_payload, dict):
                    checks["reviews"] = R.selfcheck_entry(
                        "drift",
                        baseline=str(_SELFCHECK_IMT),
                        reason="schema_drift",
                        notes=[f"{used_host or 'feedbacks'} returned {type(review_payload).__name__}, not object"],
                    )
                    review_payload = None
                if review_payload is None:
                    pass
                else:
                    if "feedbacks" not in review_payload:
                        checks["reviews"] = R.selfcheck_entry(
                            "drift",
                            baseline=str(_SELFCHECK_IMT),
                            reason="schema_drift",
                            notes=["feedbacks key missing from 200 response"],
                        )
                    else:
                        fb = review_payload.get("feedbacks")
                    if "reviews" in checks and checks["reviews"].get("state") == "drift":
                        pass
                    elif not isinstance(fb, list):
                        checks["reviews"] = R.selfcheck_entry(
                            "drift",
                            baseline=str(_SELFCHECK_IMT),
                            reason="schema_drift",
                            notes=[f"feedbacks is {type(fb).__name__}, not list"],
                        )
                    else:

                        def _has_review_text(f: dict) -> bool:
                            for key in ("text", "pros", "cons"):
                                value = f.get(key)
                                if isinstance(value, str) and value.strip():
                                    return True
                            return False

                        with_text = sum(1 for f in fb if isinstance(f, dict) and _has_review_text(f))
                        has_rating = any(isinstance(f, dict) and "productValuation" in f for f in fb)
                        if not fb:
                            # empty pool on a known-reviewed baseline is rot/transport, not drift
                            checks["reviews"] = R.selfcheck_entry(
                                "inconclusive",
                                baseline=str(_SELFCHECK_IMT),
                                reason="baseline_gone",
                                notes=["empty feedbacks pool (baseline may have lost reviews)"],
                            )
                        elif with_text == 0 or not has_rating:
                            checks["reviews"] = R.selfcheck_entry(
                                "drift",
                                baseline=str(_SELFCHECK_IMT),
                                pool_size=len(fb),
                                with_text=with_text,
                                has_productValuation=has_rating,
                                notes=[
                                    "pool present but 0 texts or no productValuation key (content/rating key drift)"
                                ],
                            )
                        else:
                            checks["reviews"] = R.selfcheck_entry(
                                "healthy", baseline=str(_SELFCHECK_IMT), pool_size=len(fb), with_text=with_text
                            )
    except (TimeoutError, Exception) as exc:
        checks["reviews"] = R.selfcheck_entry(
            "inconclusive",
            baseline=str(_SELFCHECK_IMT),
            reason="timeout" if isinstance(exc, asyncio.TimeoutError) else "transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )

    # --- search_goods (wb_search STEP 1 — the id list) ---
    sq = _SELFCHECK_SEARCH_QUERY
    sg_params = httpx.QueryParams({"query": sq, "dest": WB_DEFAULT_DEST})
    sg_url = f"https://search-goods.wildberries.ru/search?{sg_params}"
    await _polite_wait()
    try:
        async with asyncio.timeout(45):
            async with _wb_client() as client:
                status_code, text, err = await _safe_get_text(client, sg_url)
            if err or status_code == 429:
                checks["search_goods"] = R.selfcheck_entry(
                    "inconclusive",
                    baseline=sq,
                    reason="rate_limited" if status_code == 429 else "transport_down",
                    notes=[f"http {status_code} err={err}"],
                )
            elif status_code != 200:
                checks["search_goods"] = R.selfcheck_entry(
                    "inconclusive", baseline=sq, reason="transport_down", notes=[f"http {status_code}"]
                )
            else:
                try:
                    ids = json.loads(text or "")
                except json.JSONDecodeError as exc:
                    checks["search_goods"] = R.selfcheck_entry(
                        "drift",
                        baseline=sq,
                        reason="parse_error",
                        notes=[f"search-goods returned HTTP 200 but invalid JSON: {exc}"],
                    )
                    ids = None
                if ids is None:
                    if "search_goods" not in checks:
                        checks["search_goods"] = R.selfcheck_entry(
                            "drift", baseline=sq, reason="schema_drift", notes=["search-goods returned null, not list"]
                        )
                elif not isinstance(ids, list):
                    checks["search_goods"] = R.selfcheck_entry(
                        "drift",
                        baseline=sq,
                        notes=[
                            f"search-goods returned {type(ids).__name__}, not a list "
                            "(response shape drift — wb_search would error/empty)"
                        ],
                    )
                elif not ids:
                    # An EMPTY list (vs a populated list we can't parse) is more likely a
                    # transient WB hiccup than a shape drift — inconclusive, not a false
                    # alarm (audit 2026-06-01 FALSE_POSITIVE).
                    checks["search_goods"] = R.selfcheck_entry(
                        "inconclusive",
                        baseline=sq,
                        reason="transport_down",
                        notes=[
                            "search-goods returned an empty list (transient / no results — cannot prove id-shape drift)"
                        ],
                    )
                else:
                    recovered = _recover_search_ids(ids)
                    if not recovered:
                        # list HAS items but 0 recoverable ids = the real id-shape drift
                        # the wb_search id-recovery guard exists for. LOUD.
                        checks["search_goods"] = R.selfcheck_entry(
                            "drift",
                            baseline=sq,
                            raw_len=len(ids),
                            notes=[
                                "search-goods list has items but 0 recoverable numeric "
                                "ids (id-shape drift — see wb_search recovery guard)"
                            ],
                        )
                    else:
                        checks["search_goods"] = R.selfcheck_entry("healthy", baseline=sq, recovered_ids=len(recovered))
    except (TimeoutError, Exception) as exc:
        checks["search_goods"] = R.selfcheck_entry(
            "inconclusive",
            baseline=sq,
            reason="timeout" if isinstance(exc, asyncio.TimeoutError) else "transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )

    # --- root_basket (wb_root_info imt_id resolution) ---
    await _polite_wait()
    try:
        async with asyncio.timeout(45):
            host = _basket_for_sku(nm)
            vol = nm // 100000
            part = nm // 1000
            basket_url = f"https://{host}/vol{vol}/part{part}/{nm}/info/ru/card.json"
            async with _wb_client() as client:
                status_code, text, err = await _safe_get_text(client, basket_url)
            if err or status_code == 404 or status_code != 200:
                checks["root_basket"] = R.selfcheck_entry(
                    "inconclusive",
                    baseline=str(nm),
                    reason="transport_down",
                    notes=[f"basket http {status_code} err={err} host={host}"],
                )
            else:
                try:
                    data = json.loads(text or "")
                except json.JSONDecodeError as exc:
                    checks["root_basket"] = R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        reason="parse_error",
                        notes=[f"basket 200 JSON parse error: {str(exc)[:120]}"],
                    )
                    data = None
                imt = None
                if isinstance(data, dict):
                    imt = R.first_present(data, "imt_id", "imtId")
                    if imt is None and isinstance(data.get("data"), dict):
                        imt = R.first_present(data["data"], "imt_id", "imtId")
                elif data is not None:
                    checks["root_basket"] = R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        reason="schema_drift",
                        notes=[f"basket 200 root is {type(data).__name__}, not object"],
                    )
                # imt_id must be a usable POSITIVE INT (or int-like string) — a
                # drifted object/bool/empty/wrong-type value is unusable by
                # wb_reviews and is DRIFT, not healthy (audit FALSE_NEGATIVE).
                imt_int: int | None = None
                if isinstance(imt, bool):
                    imt_int = None
                elif isinstance(imt, int):
                    imt_int = imt
                elif isinstance(imt, str) and imt.strip().isdigit():
                    imt_int = int(imt.strip())
                if imt_int is None or imt_int <= 0:
                    checks["root_basket"] = R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        notes=[
                            f"basket card.json 200 but imt_id unusable ({imt!r}) "
                            "(schema drift — wb_root_info + wb_reviews break)"
                        ],
                    )
                elif imt_int != _SELFCHECK_IMT:
                    checks["root_basket"] = R.selfcheck_entry(
                        "drift",
                        baseline=str(nm),
                        imt_id=imt_int,
                        notes=[f"basket imt_id {imt_int} differs from known baseline {_SELFCHECK_IMT}"],
                    )
                else:
                    checks["root_basket"] = R.selfcheck_entry(
                        "healthy", baseline=str(nm), imt_id=imt_int, matches_known=(imt_int == _SELFCHECK_IMT)
                    )
    except (TimeoutError, Exception) as exc:
        checks["root_basket"] = R.selfcheck_entry(
            "inconclusive",
            baseline=str(nm),
            reason="timeout" if isinstance(exc, asyncio.TimeoutError) else "transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )

    try:
        settings = get_settings()
        config_loaded = bool(settings)
    except Exception:
        config_loaded = False
    try:
        tool_count = len(await mcp.list_tools())
    except Exception:
        tool_count = 0

    result = R.selfcheck_result(
        "wb",
        checks,
        required=("card", "reviews", "search_goods", "root_basket"),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=os.getpid(),
    )
    result["config_loaded"] = config_loaded
    result["tool_count"] = tool_count
    log_event("wb_selfcheck.done", status=result.get("status"), checks=len(checks))
    return WbSelfCheckResponse.model_validate(result)


if __name__ == "__main__":
    mcp.run(transport="stdio")
