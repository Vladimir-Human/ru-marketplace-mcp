"""Megamarket MCP connector.

Megamarket gates its whole site behind ServicePipe: the internal mobile API
accepts requests and returns valid JSON, but the JSON is always
``{"error": "Произошла ошибка. Попробуйте отключить VPN…", "code": 7}`` — an
IP-reputation refusal echoed back with your own address. No fingerprint clears
it; the fix is the same as Ozon's: run the fetch inside a browser that has
already passed the ServicePipe JS challenge, over CDP. In that browser the
mobile API answers real catalog JSON.

Verified live July 2026 from a datacenter IP (docs/ANTI_BOT.md):
  - ``POST /api/mobile/v1/catalogService/catalog/search`` — code 7 IP echo
  - homepage ships a ServicePipe JS challenge (``X-SP-CRID``)

The CDP route needs verification from the operator's machine: these endpoints
were probed anonymously and documented, but the in-browser fetch shape is the
part only a passed-challenge session can confirm. Run megamarket_selfcheck
from your Chrome first — its verdict tells you which case you are in.

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

from megamarket_connector.models_output import (
    MegamarketCardResponse,
    MegamarketSearchItemOut,
    MegamarketSearchResponse,
    MegamarketSelfcheckResponse,
    MetaOut,
)
from megamarket_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "1.3.1"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://megamarket.ru"
API_BASE = f"{SITE_BASE}/api/mobile/v1"

TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes
_min_gap = _settings.min_gap

_ITEM_ID_RE = re.compile(r"(\d{5,})")

# The mobile API selects its response shape by requestVersion, and pages by
# limit/offset rather than a page number. Both values and the field names below
# come from the maintained xob0t/mmparser, which drives the same endpoint.
_REQUEST_VERSION = 10
_PAGE_SIZE = 44

# Resolved once per process and reused. Megamarket needs a delivery address to
# decide which offers exist at all, so this is not an optional refinement — see
# _resolve_address_id.
_ADDRESS = _settings.address
_address_id: str | None = None
_address_resolved = False

mcp = FastMCP(
    name="megamarket-connector",
    version=SERVER_VERSION,
    instructions=(
        "Megamarket catalog: search and product cards. Every read runs in the "
        "operator's own Chrome over CDP — ServicePipe blocks the mobile API by "
        "IP reputation, so there is no anonymous tier. Start with "
        "megamarket_search; megamarket_card takes a goods id or product URL."
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
    """Pull a goods id out of a megamarket.ru URL or a bare numeric id."""
    raw = raw.strip()
    if raw.isdigit():
        return raw
    match = _ITEM_ID_RE.search(raw)
    return match.group(1) if match else None


async def _cdp_post_json(api_path: str, body: dict[str, Any], ctx: Context | None) -> tuple[int, str]:
    """POST JSON to the mobile API from inside the operator's Chrome.

    The fetch carries the browser's ServicePipe cookies natively
    (``credentials: 'include'``), which is the whole reason this tier exists.
    Serialized via _cdp_lock; body-capped like any HTTP read.
    """
    url = f"{API_BASE}{api_path}"

    async def _attempt() -> tuple[int, str]:
        async with _cdp_lock:
            await _polite_wait()
            async with open_page(f"{SITE_BASE}/", wait_ms=4000) as page:
                raw = await asyncio.wait_for(
                    page.evaluate(
                        """async (args) => {
                        const res = await fetch(args.url, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                            body: JSON.stringify(args.body)
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
                        {"url": url, "body": body, "cap": MAX_BODY_BYTES},
                    ),
                    timeout=30.0,
                )
        result = json.loads(raw) if isinstance(raw, str) else raw
        return result["status"], result["text"]

    try:
        return await asyncio.wait_for(_attempt(), timeout=max(0.01, float(TIMEOUT)))
    except TimeoutError:
        return 0, f"CDP timeout after {TIMEOUT}s"


def _is_ip_block(payload: Any) -> bool:
    """The code-7 VPN/IP refusal is a transport verdict, not data."""
    if not isinstance(payload, dict):
        return False
    return payload.get("code") == 7 or "VPN" in str(payload.get("error") or "")


def _blocked_error(detail: str = "") -> TransportDownError:
    message = (
        "Megamarket refused the session by IP reputation (ServicePipe code 7). "
        "Open megamarket.ru in the Chrome scraping profile, let the challenge pass, then retry; "
        "a Russian residential IP for that browser is the durable fix."
    )
    hint = _pacer.rotation_hint()
    if hint:
        message += f" {hint}"
    if detail:
        message += f" Detail: {detail[:160]}"
    return TransportDownError(message, status_code=403)


async def _post(api_path: str, body: dict[str, Any], ctx: Context | None, what: str) -> dict[str, Any]:
    """POST through CDP and unwrap the envelope, mapping failures to the taxonomy."""
    cache_key = f"{api_path}:{json.dumps(body, sort_keys=True, ensure_ascii=False)}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        status, text = await _cdp_post_json(api_path, body, ctx)
    except NavBlocked as exc:
        raise_tool_error(TransportDownError(f"Megamarket navigation blocked (HTTP {exc.status})."))
    if status != 200:
        if status in (401, 403, 429):
            _pacer.record_refusal()
            raise_tool_error(_blocked_error(text))
        raise_tool_error(TransportDownError(f"Megamarket {what} fetch failed: HTTP {status}. {text[:120]}"))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise_tool_error(ParserDriftError(f"non-JSON {what} body; preview: {text[:200]}"))
    if _is_ip_block(payload):
        # A code-7 body arrives with HTTP 200, so the pacer would never hear
        # about it from the status alone. It is still a refusal.
        _pacer.record_refusal()
        raise_tool_error(_blocked_error(str(payload)[:200]))
    if not isinstance(payload, dict):
        raise_tool_error(ParserDriftError(f"{what} payload is not a JSON object"))
    _pacer.record_success()
    _cache.set(cache_key, payload)
    return payload


async def _resolve_address_id(ctx: Context | None) -> str | None:
    """Find a delivery addressId, because search results depend on one.

    This is the field that made search look broken. Megamarket answers a body
    with ``addressId: None`` using HTTP 200, ``listingSize`` counting the
    products it found, and an **empty** ``items`` array — because every offer
    carries its own deliveryPossibilities and none of them can be delivered to
    nowhere. Confirmed against the maintained xob0t/mmparser, which resolves an
    address three different ways before it ever searches.

    Two sources, in order of trust:

    1. the logged-in profile's default address — exactly what the operator sees
       on the site, so prices and availability match their own experience;
    2. the configured city (MEGAMARKET_ADDRESS, Moscow by default) through the
       public suggest endpoint, which works without a session.

    A failure here is not fatal: the caller still gets a search, and the empty
    result is reported honestly rather than as a mysterious zero.
    """
    global _address_id, _address_resolved
    if _address_resolved:
        return _address_id

    _address_resolved = True
    try:
        profile = await _post("/profileService/address/list", {}, ctx, "address list")
        addresses = profile.get("profileAddresses")
        if isinstance(addresses, list) and addresses:
            preferred = next(
                (a for a in addresses if isinstance(a, dict) and a.get("isDefault") is True),
                next((a for a in addresses if isinstance(a, dict)), None),
            )
            if preferred and preferred.get("addressId"):
                _address_id = str(preferred["addressId"])
                log_event("megamarket.address", source="profile", region=str(preferred.get("region") or "")[:40])
                return _address_id
    except Exception as exc:
        # Not logged in, or the profile endpoint refused. Fall through.
        log_event("megamarket.address_profile_failed", error=_redact(str(exc))[:120])

    try:
        suggested = await _post(
            "/addressSuggestService/address/suggest",
            {"count": 10, "isSkipRegionFilter": True, "query": _ADDRESS},
            ctx,
            "address suggest",
        )
        items = suggested.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            candidate = items[0].get("addressId")
            if candidate:
                _address_id = str(candidate)
                log_event("megamarket.address", source="suggest", query=_ADDRESS[:40])
                return _address_id
    except Exception as exc:
        log_event("megamarket.address_suggest_failed", error=_redact(str(exc))[:120])

    log_event("megamarket.address_unresolved", configured=_ADDRESS[:40])
    return None


# url/parse returns filter bounds as words; the search endpoint wants the codes.
_FILTER_TYPE_CODES = {"EXACT_VALUE": 0, "LEFT_BOUND": 1, "RIGHT_BOUND": 2}


# Resolved search params are cached per query for the process lifetime. Each
# resolution costs a browser navigation plus an API call, and an agent asks the
# same question repeatedly.
_search_params_cache: dict[str, dict[str, Any]] = {}


async def _final_catalog_url(catalog_url: str, ctx: Context | None) -> str:
    """Follow Megamarket's search-to-category redirect and return where it lands.

    ``/catalog/?q=ноутбук`` redirects to ``/catalog/noutbuki/``. That matters
    because url/parse answers ``collection: None`` for the generic search URL and
    a real collection for the category it redirects to — and without a collection
    the search returns listingSize>0 with an empty items array.

    Only a browser follows this redirect the way the site intends, so the probe
    runs over CDP. The lock is released before the caller's API request: holding
    it across both would deadlock against the non-reentrant lock that
    _cdp_post_json takes.

    A failure returns the original URL. The search then runs without collection
    hints, which is weaker but not broken.
    """
    try:
        async with _cdp_lock:
            await _polite_wait()
            async with open_page(catalog_url, wait_ms=4000) as page:
                final_url = page.url
    except Exception as exc:
        log_event("megamarket.redirect_failed", error=_redact(str(exc))[:120])
        return catalog_url
    if final_url and final_url not in (catalog_url, "about:blank"):
        log_event("megamarket.redirect", to=final_url[:120])
        return final_url
    return catalog_url


async def _resolve_search_params(query: str, ctx: Context | None) -> dict[str, Any]:
    """Ask Megamarket how to read its own search URL, then search with that.

    This is the step that was missing, and it is why a hand-built body came back
    with listingSize counting products and items empty. A text query is not just
    a string to Megamarket: url/parse maps it to an assumed collection, and the
    search endpoint wants that collection in `collectionId` and
    `selectedAssumedCollectionId`. Without them the listing has nothing to list.

    The maintained xob0t/mmparser never constructs a search body from a query
    either — it POSTs the catalog URL to urlService/url/parse first and feeds the
    returned params into the search. We do the same.

    Returns the fields to merge into the body, or an empty dict if url/parse is
    unavailable — in which case the caller still searches, just without the
    collection hints.
    """
    cached = _search_params_cache.get(query.strip())
    if cached is not None:
        return cached

    catalog_url = await _final_catalog_url(f"{SITE_BASE}/catalog/?q={urllib.parse.quote(query.strip())}", ctx)
    try:
        parsed = await _post("/urlService/url/parse", {"url": catalog_url}, ctx, "url parse")
    except Exception as exc:
        log_event("megamarket.url_parse_failed", error=_redact(str(exc))[:120])
        return {}

    params = parsed.get("params")
    if not isinstance(params, dict):
        return {}

    collection = params.get("collection")
    if not isinstance(collection, dict):
        # A menu node carries the collection one level down.
        menu_node = params.get("menuNode")
        collection = menu_node.get("collection") if isinstance(menu_node, dict) else None
    collection_id = collection.get("collectionId") if isinstance(collection, dict) else None

    merchant = params.get("merchant")
    merchant_id = merchant.get("id") if isinstance(merchant, dict) else None

    filters = params.get("selectedListingFilters")
    converted: list[Any] = []
    if isinstance(filters, list):
        for entry in filters:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            code = _FILTER_TYPE_CODES.get(str(item.get("type")))
            if code is not None:
                item["type"] = code
            converted.append(item)

    resolved: dict[str, Any] = {
        "searchText": params.get("searchText") or query.strip(),
        "collectionId": collection_id,
        "selectedAssumedCollectionId": collection_id,
        "merchant": {"id": merchant_id} if merchant_id else None,
        "selectedFilters": converted,
        "isMultiCategorySearch": bool(params.get("isMultiCategorySearch", False)),
    }
    log_event(
        "megamarket.search_params",
        collection=str(collection_id or "")[:40],
        multi=resolved["isMultiCategorySearch"],
    )
    _search_params_cache[query.strip()] = resolved
    return resolved


def _search_body(
    query: str, page: int, address_id: str | None = None, resolved: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the search request the mobile API actually accepts.

    Every field here is load-bearing. The connector used to post
    ``{"text": ..., "page": 1}``, which the API accepts with HTTP 200 and
    answers with an empty ``items`` array — indistinguishable from "nothing
    matched" unless you know the real schema. The search term is ``searchText``,
    paging is ``limit``/``offset`` rather than a page number, and
    ``requestVersion`` selects the response shape.

    ``addressId`` stays None: this connector has no address concept, so
    Megamarket answers for a default region. The prices are therefore
    default-region prices, which is worth knowing before quoting them.
    """
    page = max(1, int(page))
    body: dict[str, Any] = {
        "requestVersion": _REQUEST_VERSION,
        "limit": _PAGE_SIZE,
        "offset": (page - 1) * _PAGE_SIZE,
        "isMultiCategorySearch": False,
        "searchByOriginalQuery": False,
        "selectedSuggestParams": [],
        "expandedFiltersIds": [],
        "sorting": 0,
        "ageMore18": None,
        "addressId": address_id,
        "showNotAvailable": True,
        "selectedFilters": [],
        "collectionId": None,
        "searchText": query.strip(),
        "selectedAssumedCollectionId": None,
        "merchant": None,
    }
    # url/parse knows which collection a query maps to; the plain body does not.
    if resolved:
        body.update({k: v for k, v in resolved.items() if v is not None or k in ("merchant", "collectionId")})
    return body


def _scoped(item: dict[str, Any], key: str) -> dict[str, Any]:
    """One nested object out of a search item, or an empty dict."""
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _parse_items(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None, bool]:
    """Parse the goods array out of a search payload.

    A search item is not flat. The real shape nests the product under ``goods``
    and its price under ``favoriteOffer``::

        {"goods": {"goodsId": "100012345_678", "title": …, "webUrl": …},
         "favoriteOffer": {"finalPrice": 32990, "merchantName": …},
         "isAvailable": true, "offerCount": 3}

    Flat aliases are tried afterwards, so a payload that ever flattens keeps
    parsing. ``goodsId`` carries a merchant suffix after an underscore and the
    card endpoint wants the bare id, so it is split off here.

    The third return value says whether an items *container* was found at all.
    That distinction is the whole point: an empty list under a known key means
    the query matched nothing, while no known key holding a list means
    Megamarket moved the shape under us. Collapsing both into "zero items"
    is how a parser breaks silently and still reports success.
    """
    candidates = (
        payload.get("items"),
        payload.get("goods"),
        payload.get("products"),
        payload.get("data", {}).get("items") if isinstance(payload.get("data"), dict) else None,
    )
    container = next((c for c in candidates if isinstance(c, list)), None)
    container_found = container is not None
    items_raw = container if container is not None else []
    total = R.first_present(payload, "total", "totalCount", "count")
    if not isinstance(total, int):
        total = R.coerce_int(total)
    out: list[dict[str, Any]] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        goods = _scoped(it, "goods")
        offer = _scoped(it, "favoriteOffer")

        def pick(*aliases: str, _g: dict[str, Any] = goods, _o: dict[str, Any] = offer, _i: dict[str, Any] = it) -> Any:
            for scope in (_g, _o, _i):
                value = R.first_present(scope, *aliases)
                if value is not None:
                    return value
            return None

        raw_id = pick("goodsId", "id", "goods_id", "itemId")
        price = R.coerce_price(pick("finalPrice", "price", "currentPrice"))
        old_price = R.coerce_price(pick("oldPrice", "basePrice", "strikePrice"))
        available = it.get("isAvailable")
        out.append(
            {
                "item_id": str(raw_id).split("_")[0] if raw_id is not None else None,
                "title": pick("title", "name", "goodsName"),
                "price_rub": price,
                "old_price_rub": old_price,
                "rating": R.coerce_rating(pick("rating", "ratingScore")),
                "rating_count": R.coerce_int(pick("reviewCount", "reviewsCount", "ratingCount")),
                "url": pick("webUrl", "url", "link"),
                "is_available": available if isinstance(available, bool) else None,
            }
        )
    return out, total, container_found


@mcp.tool(
    name="megamarket_search",
    annotations=ToolAnnotations(
        title="Megamarket Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def megamarket_search(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Search text, e.g. 'стиральная машина'")],
    ctx: Context | None = None,
) -> MegamarketSearchResponse:
    """Search the Megamarket catalog via the mobile API, inside the operator's Chrome.

    ## Return Format

    MegamarketSearchResponse: {status, query, tier_used, count, total_count,
    items[], meta}. price_rub is None when absent — never 0.

    ## Error Format

    ToolError: TransportDownError on ServicePipe refusals (with the fix inline);
    ParserDriftError when a reached-200 body no longer parses.
    """
    log_event("megamarket_search.start", query=query[:60])
    try:
        address_id = await _resolve_address_id(ctx)
        resolved = await _resolve_search_params(query, ctx)
        payload = await _post(
            "/catalogService/catalog/search",
            _search_body(query, 1, address_id, resolved),
            ctx,
            "search",
        )
        items_raw, total, container_found = _parse_items(payload)
        listing_size = R.coerce_int(payload.get("listingSize"))
        items = [MegamarketSearchItemOut(**it) for it in items_raw]
        warnings: list[str] = []
        # A 200 that parses to nothing is the dangerous case: without these two
        # guards the tool reports success with zero offers, and compare_prices
        # then calls the comparison complete while Megamarket has silently
        # contributed nothing at all.
        if not container_found:
            raise_tool_error(
                ParserDriftError(
                    "search payload carried no items array under items/goods/products/data.items — "
                    "the response shape moved; verify manually before trusting Megamarket results"
                )
            )
        if not items and total not in (0, None):
            raise_tool_error(
                ParserDriftError(
                    f"search reported total={total} but no parseable items — item shape moved; verify manually"
                )
            )
        if not items and listing_size:
            # listingSize counts what the catalog matched, so a positive count
            # with an empty items array is not "nothing found" — the products
            # exist and no *offer* came back. That happens when the request
            # carries no delivery address: every offer has its own
            # deliveryPossibilities, and none can be delivered to nowhere.
            raise_tool_error(
                TransportDownError(
                    f"Megamarket matched {listing_size} products but returned no offers"
                    + (f" (collection {resolved.get('collectionId')})" if resolved.get("collectionId") else "")
                    + (
                        " and no delivery address could be resolved. Set MEGAMARKET_ADDRESS to a city, "
                        "or log the scraping-profile Chrome into Megamarket so its default address is used."
                        if not address_id
                        else f" for address {address_id}. The address may not be deliverable for this query; "
                        "try another city via MEGAMARKET_ADDRESS."
                    )
                )
            )
        if not items:
            # An empty array under a known key with listingSize 0 or absent is a
            # legitimate zero-result answer, so this stays a success.
            warnings.append(
                "empty_result: no items for this query. Megamarket also answers an unauthenticated or "
                "addressless session with an empty result rather than an error, so check the session "
                "before concluding the product does not exist"
            )
        result = MegamarketSearchResponse(
            query=query, tier_used="cdp", count=len(items), total_count=total, items=items
        )
        attached = R.attach_meta(
            result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="megamarket_search"
        )
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("megamarket_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"megamarket_search failed: {exc}")))


@mcp.tool(
    name="megamarket_card",
    annotations=ToolAnnotations(
        title="Megamarket Product Card",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def megamarket_card(
    item_id_or_url: Annotated[
        str, Field(min_length=1, max_length=300, description="Goods id or megamarket.ru product URL")
    ],
    ctx: Context | None = None,
) -> MegamarketCardResponse:
    """Fetch one Megamarket product card.

    ## Return Format

    MegamarketCardResponse: {status, item_id, title, price_rub, old_price_rub,
    is_available, rating, rating_count, url, tier_used, meta}.

    ## Error Format

    ToolError: BadRequestError on unparseable input; NotFoundError on a missing
    goods id; TransportDownError on ServicePipe refusals; ParserDriftError on
    envelope drift.
    """
    log_event("megamarket_card.start", input=item_id_or_url[:80])
    try:
        item_id = _extract_item_id(item_id_or_url)
        if item_id is None:
            raise_tool_error(BadRequestError(f"could not extract a goods id from {item_id_or_url!r}"))
        payload = await _post(
            # productCardMainInfo/get is the endpoint the mobile app uses;
            # productCard/get was a guess and is not part of the API.
            "/catalogService/productCardMainInfo/get",
            {"goodsId": item_id, "merchantId": "0"},
            ctx,
            "card",
        )
        card = payload.get("goods") if isinstance(payload.get("goods"), dict) else payload.get("data", payload)
        if not isinstance(card, dict) or not card:
            raise_tool_error(NotFoundError(f"Megamarket goods {item_id} returned an empty card."))
        result = MegamarketCardResponse(
            item_id=item_id,
            title=R.first_present(card, "title", "name", "goodsName"),
            price_rub=R.coerce_price(R.first_present(card, "price", "currentPrice", "finalPrice")),
            old_price_rub=R.coerce_price(R.first_present(card, "oldPrice", "basePrice")),
            is_available=R.first_present(card, "isAvailable", "available", "inStock"),
            rating=R.coerce_rating(R.first_present(card, "rating", "ratingScore")),
            rating_count=R.coerce_int(R.first_present(card, "reviewCount", "reviewsCount")),
            url=R.first_present(card, "url", "webUrl") or f"{SITE_BASE}/product/{item_id}",
            tier_used="cdp",
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), [], source="megamarket_card")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("megamarket_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"megamarket_card failed: {exc}")))


@mcp.tool(
    name="megamarket_selfcheck",
    annotations=ToolAnnotations(
        title="Megamarket Self-Check", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
)
async def megamarket_selfcheck(ctx: Context | None = None) -> MegamarketSelfcheckResponse:
    """Structural drift canary for Megamarket (tri-state). Posts one live search
    through CDP and checks items parse.

    A ServicePipe code-7 refusal or CDP-down is ``inconclusive`` (transport),
    NEVER drift. Only a reached-200 catalog body that fails the parse smoke is
    ``drift``. From a machine whose Chrome has not passed the challenge,
    inconclusive is the expected verdict.

    ## Return Format

    MegamarketSelfcheckResponse: {status, healthy, connector, checks, ...}.
    """
    log_event("megamarket_selfcheck.start")
    try:
        result = await _megamarket_selfcheck_impl(ctx)
        log_event("megamarket_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("megamarket_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"megamarket_selfcheck failed: {exc}")))


async def _megamarket_selfcheck_impl(ctx: Context | None) -> MegamarketSelfcheckResponse:
    checks: dict[str, dict] = {}
    baseline = "mobile-api-search-v1"
    try:
        async with asyncio.timeout(90):
            address_id = await _resolve_address_id(ctx)
            resolved = await _resolve_search_params("телефон", ctx)
            payload = await _post(
                "/catalogService/catalog/search",
                _search_body("телефон", 1, address_id, resolved),
                ctx,
                "selfcheck",
            )
    except ToolError as exc:
        reason = "blocked" if "IP reputation" in str(exc) else "transport_down"
        checks["search"] = R.selfcheck_entry("inconclusive", baseline=baseline, reason=reason, notes=[str(exc)[:160]])
    except Exception as exc:
        checks["search"] = R.selfcheck_entry(
            "inconclusive",
            baseline=baseline,
            reason="transport_down",
            notes=[f"{type(exc).__name__}: {str(exc)[:120]}"],
        )
    else:
        items_raw, total, container_found = _parse_items(payload)
        if items_raw:
            checks["search"] = R.selfcheck_entry("healthy", baseline=baseline, notes=[f"{len(items_raw)} items parsed"])
        elif container_found and total not in (0, None):
            # total says there are matches and none of them parsed. That is a
            # contradiction inside one payload, so the item shape moved.
            checks["search"] = R.selfcheck_entry(
                "drift",
                baseline=baseline,
                reason="parse_smoke_failed",
                notes=[f"total={total} but no items parsed — the item shape moved"],
            )
        elif container_found:
            # The canary is a common term that always has matches, so an empty
            # array is not "nothing found". The parser is fine — it found the
            # container and read it — which rules out drift. What is left is
            # the session: since early 2025 Megamarket answers an unauthenticated
            # client with an empty result rather than an error, a behaviour the
            # public mmparser project documents and works around with cookies.
            # Calling this drift would send the operator to read parser code
            # that has nothing wrong with it.
            checks["search"] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="not_authenticated",
                notes=[
                    "ServicePipe passed and the envelope parsed, but items is empty for a canary query — "
                    "the scraping-profile Chrome is most likely not logged in to Megamarket"
                ],
            )
        else:
            checks["search"] = R.selfcheck_entry(
                "drift",
                baseline=baseline,
                reason="parse_smoke_failed",
                notes=["no items container in the payload — the response shape moved"],
            )

    result_dict = R.selfcheck_result(
        "megamarket",
        checks,
        required=("search",),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return MegamarketSelfcheckResponse(**result_dict)
