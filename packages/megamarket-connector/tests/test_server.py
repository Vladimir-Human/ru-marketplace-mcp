"""Offline tests for the Megamarket connector.

CDP posting is monkeypatched out: the suite runs with no Chrome and no network.
Fixtures mirror the mobile-API envelope documented in ANTI_BOT.md, including
the code-7 ServicePipe refusal that must never be read as data.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from megamarket_connector import server

SEARCH_PAYLOAD = {
    "total": 541,
    "items": [
        {
            "id": 1234567,
            "title": "Стиральная машина узкая LG",
            "price": 32990,
            "oldPrice": 41990,
            "rating": 4.7,
            "reviewCount": 213,
            "url": "https://megamarket.ru/product/stiralnaya-mashina-1234567",
        },
        {"id": 1234568, "title": "Стиральная машина без цены", "price": None, "url": ""},
    ],
}

CARD_PAYLOAD = {
    "goods": {
        "id": 1234567,
        "title": "Стиральная машина узкая LG F2J3HS0W",
        "price": 32990,
        "oldPrice": 41990,
        "isAvailable": True,
        "rating": 4.7,
        "reviewCount": 213,
        "webUrl": "https://megamarket.ru/product/stiralnaya-mashina-1234567",
    }
}

IP_BLOCK_PAYLOAD = {"error": "Произошла ошибка. Попробуйте отключить VPN…", "code": 7, "ip": "3.220.149.31"}


# The autouse fixture stubs _final_catalog_url so unrelated tests never reach for
# Chrome. Keep a handle on the real one for the tests that are about it.
_REAL_FINAL_CATALOG_URL = server._final_catalog_url


async def _no_redirect(url, ctx=None):
    """Stand in for the CDP redirect probe: the URL is already final."""
    return url


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()
    # The resolved delivery address is cached for the process lifetime, so it
    # has to be forgotten between tests or they become order-dependent.
    monkeypatch.setattr(server, "_address_id", None)
    monkeypatch.setattr(server, "_address_resolved", False)
    monkeypatch.setattr(server, "_search_params_cache", {})
    # The pacer is real and its gap is seconds; an offline suite must not sleep it.
    monkeypatch.setattr(server, "_min_gap", 0.0)
    server._pacer.reset()
    # The redirect step opens a real page. Default to "no redirect" so tests that
    # are not about redirects never reach for Chrome.
    monkeypatch.setattr(server, "_final_catalog_url", _no_redirect)


def _patch_post(monkeypatch, payload):
    async def fake_post(api_path, body, ctx, what):
        return payload

    monkeypatch.setattr(server, "_post", fake_post)


# ---------------------------------------------------------- megamarket_search ----


async def test_search_parses_items_and_total(monkeypatch):
    _patch_post(monkeypatch, SEARCH_PAYLOAD)

    result = await server.megamarket_search("стиральная машина")

    assert result.count == 2
    assert result.total_count == 541
    assert result.items[0].price_rub == 32990
    assert result.items[0].old_price_rub == 41990


async def test_search_a_pricelss_item_is_none_never_zero(monkeypatch):
    _patch_post(monkeypatch, SEARCH_PAYLOAD)

    result = await server.megamarket_search("стиральная машина")

    assert result.items[1].price_rub is None
    assert result.items[1].price_rub != 0


async def test_search_raises_drift_on_empty_items_with_nonzero_total(monkeypatch):
    """total=10 with nothing parseable is a contradiction, not a soft warning.

    This used to attach a warning and return success. compare_prices reads
    status, not warnings, so a shape change here left the comparison marked
    complete with Megamarket contributing nothing.
    """
    _patch_post(monkeypatch, {"total": 10, "items": []})

    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("редкость")

    assert "parser_drift" in str(excinfo.value)


async def test_search_raises_drift_when_the_items_container_disappears(monkeypatch):
    """Megamarket renaming the array must fail loudly, not return zero results."""
    _patch_post(monkeypatch, {"total": 10, "results": [{"id": "1", "title": "x", "price": 10}]})

    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("стиральная машина")

    assert "parser_drift" in str(excinfo.value)


async def test_search_allows_a_genuinely_empty_result(monkeypatch):
    """An empty array under a known key means the query matched nothing.

    That must stay a successful zero-count answer — the drift guard exists to
    catch a moved shape, not to turn every no-results query into an error.
    """
    _patch_post(monkeypatch, {"total": 0, "items": []})

    result = await server.megamarket_search("заведомо несуществующий товар")

    assert result.count == 0
    assert result.items == []


async def test_search_maps_code7_to_transport_down(monkeypatch):
    async def fake_post(api_path, body, ctx, what):
        if server._is_ip_block(IP_BLOCK_PAYLOAD):
            raise ToolError(server._blocked_error(str(IP_BLOCK_PAYLOAD)[:200]))
        return IP_BLOCK_PAYLOAD

    monkeypatch.setattr(server, "_post", fake_post)

    with pytest.raises(ToolError):
        await server.megamarket_search("телефон")


# ------------------------------------------------------------ megamarket_card ----


async def test_card_parses_the_goods_envelope(monkeypatch):
    _patch_post(monkeypatch, CARD_PAYLOAD)

    result = await server.megamarket_card("1234567")

    assert result.title == "Стиральная машина узкая LG F2J3HS0W"
    assert result.price_rub == 32990
    assert result.is_available is True
    assert result.rating_count == 213


async def test_card_accepts_a_product_url(monkeypatch):
    _patch_post(monkeypatch, CARD_PAYLOAD)

    result = await server.megamarket_card("https://megamarket.ru/product/stiralnaya-1234567")

    assert result.item_id == "1234567"


async def test_card_rejects_input_without_an_id():
    with pytest.raises(ToolError):
        await server.megamarket_card("no-digits-here")


async def test_card_maps_an_empty_card_to_not_found(monkeypatch):
    _patch_post(monkeypatch, {"goods": {}})

    with pytest.raises(ToolError):
        await server.megamarket_card("1234567")


# ------------------------------------------------------- megamarket_selfcheck ----


async def test_selfcheck_healthy_when_items_parse(monkeypatch):
    _patch_post(monkeypatch, SEARCH_PAYLOAD)

    result = await server.megamarket_selfcheck()

    assert result.status == "success"
    assert result.healthy is True


async def test_selfcheck_block_is_inconclusive(monkeypatch):
    async def fake_post(api_path, body, ctx, what):
        raise ToolError(server._blocked_error(str(IP_BLOCK_PAYLOAD)[:200]))

    monkeypatch.setattr(server, "_post", fake_post)

    result = await server.megamarket_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None


async def test_selfcheck_drift_when_no_items(monkeypatch):
    _patch_post(monkeypatch, {"total": 5, "items": []})

    result = await server.megamarket_selfcheck()

    assert result.status == "drift_detected"


# ------------------------------------------------------------------- helpers ----


def test_is_ip_block_detects_code7_and_vpn_text():
    assert server._is_ip_block(IP_BLOCK_PAYLOAD) is True
    assert server._is_ip_block({"code": 7}) is True
    assert server._is_ip_block({"error": "something else"}) is False
    assert server._is_ip_block([1, 2]) is False


def test_extract_item_id():
    assert server._extract_item_id("1234567") == "1234567"
    assert server._extract_item_id("https://megamarket.ru/product/x-1234567") == "1234567"
    assert server._extract_item_id("телефон") is None


# --------------------------------------------------- logged-out vs no results ----
#
# Verified live July 2026: ServicePipe passes, the API answers a valid envelope,
# and `items` comes back empty for every query. The parser is not broken — the
# session is not authenticated. The publicly maintained mmparser project
# documents the same behaviour: since early 2025 Megamarket does not serve an
# anonymous client, and it answers with emptiness rather than an error.


async def test_an_empty_result_warns_that_it_may_mean_logged_out(monkeypatch):
    _patch_post(monkeypatch, {"total": 0, "items": []})

    result = await server.megamarket_search("заведомо несуществующий товар")

    assert result.count == 0
    assert any("empty_result" in w for w in result.meta.warnings), result.meta.warnings
    assert any("session" in w for w in result.meta.warnings)


async def test_a_populated_result_carries_no_such_warning(monkeypatch):
    _patch_post(monkeypatch, SEARCH_PAYLOAD)

    result = await server.megamarket_search("стиральная машина")

    assert not any("empty_result" in w for w in result.meta.warnings)


async def test_selfcheck_calls_an_empty_canary_not_authenticated(monkeypatch):
    """Empty on a canary is a session problem, and must not read as drift.

    Reporting drift sends the operator to read parser code that is working.
    The parser found the container and read it — what is missing is the login.
    """
    _patch_post(monkeypatch, {"total": 0, "items": []})

    result = await server.megamarket_selfcheck()

    assert result.status != "drift_detected"
    assert result.checks["search"].state == "inconclusive"
    assert result.checks["search"].reason == "not_authenticated"


async def test_selfcheck_still_calls_a_missing_container_drift(monkeypatch):
    """A renamed array is a real parser problem and must stay loud."""
    _patch_post(monkeypatch, {"total": 10, "results": [{"id": "1"}]})

    result = await server.megamarket_selfcheck()

    assert result.status == "drift_detected"
    assert result.checks["search"].reason == "parse_smoke_failed"


# ------------------------------------------------------- the real API schema ----
#
# The connector used to post {"text": ..., "page": 1} and read flat fields. The
# mobile API accepts that with HTTP 200 and answers an empty items array, which
# is why Megamarket looked like "ServicePipe passed but there are no products".
# The real request keys on searchText with limit/offset paging, and a result
# item nests the product under `goods` and its price under `favoriteOffer`.
# Field names verified against the maintained xob0t/mmparser, which drives the
# same endpoint.

REAL_SEARCH_PAYLOAD = {
    "success": True,
    "limit": 44,
    "offset": 0,
    "items": [
        {
            "goods": {
                "goodsId": "100032156789_9200",
                "title": "Стиральная машина узкая LG F2J3HS0W",
                "webUrl": "https://megamarket.ru/catalog/details/stiralnaya-mashina-100032156789/",
                "titleImage": "https://main-cdn.sbermegamarket.ru/x.jpg",
            },
            "favoriteOffer": {
                "finalPrice": 32990,
                "bonusAmount": 5940,
                "merchantName": "Партнёр",
                "merchantId": "9200",
                "availableQuantity": 4,
            },
            "isAvailable": True,
            "offerCount": 3,
            "hasOtherOffers": True,
        },
        {
            "goods": {"goodsId": "100032156790_1", "title": "Стиральная машина без цены", "webUrl": ""},
            "favoriteOffer": {"finalPrice": None, "merchantName": "Другой"},
            "isAvailable": False,
            "offerCount": 1,
        },
    ],
}


def test_the_search_body_uses_the_real_schema():
    body = server._search_body("стиральная машина", 1)

    assert body["searchText"] == "стиральная машина"
    assert body["requestVersion"] == server._REQUEST_VERSION
    assert body["limit"] == server._PAGE_SIZE
    assert body["offset"] == 0
    # "text" and "page" were the invented keys that made the API answer empty.
    assert "text" not in body
    assert "page" not in body


def test_paging_uses_offset_not_a_page_number():
    assert server._search_body("x", 2)["offset"] == server._PAGE_SIZE
    assert server._search_body("x", 3)["offset"] == server._PAGE_SIZE * 2
    # A nonsense page must not produce a negative offset.
    assert server._search_body("x", 0)["offset"] == 0


async def test_the_real_nested_payload_parses(monkeypatch):
    _patch_post(monkeypatch, REAL_SEARCH_PAYLOAD)

    result = await server.megamarket_search("стиральная машина")

    assert result.count == 2
    first = result.items[0]
    assert first.title == "Стиральная машина узкая LG F2J3HS0W"
    assert first.price_rub == 32990, "price lives in favoriteOffer.finalPrice"
    assert first.is_available is True
    assert first.url.endswith("stiralnaya-mashina-100032156789/")


def test_the_merchant_suffix_is_stripped_from_the_goods_id():
    """goodsId arrives as <id>_<merchantId>; the card endpoint wants the bare id."""
    items, _total, _found = server._parse_items(REAL_SEARCH_PAYLOAD)

    assert items[0]["item_id"] == "100032156789"


async def test_a_nested_missing_price_is_none_never_zero(monkeypatch):
    _patch_post(monkeypatch, REAL_SEARCH_PAYLOAD)

    result = await server.megamarket_search("стиральная машина")

    assert result.items[1].price_rub is None
    assert result.items[1].price_rub != 0
    assert result.items[1].is_available is False


async def test_a_flat_payload_still_parses(monkeypatch):
    """The tolerant reader keeps working if the shape ever flattens."""
    _patch_post(monkeypatch, SEARCH_PAYLOAD)

    result = await server.megamarket_search("стиральная машина")

    assert result.items[0].price_rub == 32990


def test_stock_is_none_when_the_payload_omits_it():
    items, _total, _found = server._parse_items({"items": [{"goods": {"goodsId": "1", "title": "x"}}]})

    assert items[0]["is_available"] is None, "unknown stock must not read as out of stock"


# ------------------------------------------------------ delivery address ----
#
# Observed live in July 2026 from a logged-in, challenge-passed session:
# HTTP 200, success=True, listingSize=44, items=[]. Not a block, not a
# logged-out session, not a moved shape — the request carried no delivery
# address. Every Megamarket offer has its own deliveryPossibilities, so with no
# address there is no deliverable offer and the array comes back empty while
# listingSize still counts what the catalog matched. The maintained
# xob0t/mmparser resolves an address three ways before it ever searches.

PROFILE_ADDRESSES = {
    "profileAddresses": [
        {"addressId": "a-111", "regionId": "77", "region": "Москва", "full": "Москва, ...", "isDefault": False},
        {"addressId": "a-222", "regionId": "78", "region": "СПб", "full": "СПб, ...", "isDefault": True},
    ]
}

SUGGESTED_ADDRESSES = {"items": [{"addressId": "s-999", "regionId": "77", "region": "Москва", "full": "Москва"}]}


def _patch_routes(monkeypatch, routes: dict[str, object]):
    """Answer per endpoint, so address resolution and search are separable."""

    async def fake_post(api_path, body, ctx, what):
        for path, payload in routes.items():
            if path in api_path:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected endpoint {api_path}")

    monkeypatch.setattr(server, "_post", fake_post)


async def test_the_profile_default_address_wins(monkeypatch):
    """The operator's own default address makes prices match what they see."""
    _patch_routes(monkeypatch, {"/profileService/address/list": PROFILE_ADDRESSES})

    assert await server._resolve_address_id(None) == "a-222"


async def test_the_suggest_endpoint_is_the_fallback(monkeypatch):
    """Without a session the public suggest endpoint still yields an address."""
    _patch_routes(
        monkeypatch,
        {
            "/profileService/address/list": ToolError("not authenticated"),
            "/addressSuggestService/address/suggest": SUGGESTED_ADDRESSES,
        },
    )

    assert await server._resolve_address_id(None) == "s-999"


async def test_the_address_is_resolved_once_per_process(monkeypatch):
    calls: list[str] = []

    async def counting_post(api_path, body, ctx, what):
        calls.append(api_path)
        return PROFILE_ADDRESSES

    monkeypatch.setattr(server, "_post", counting_post)

    first = await server._resolve_address_id(None)
    second = await server._resolve_address_id(None)

    assert first == second == "a-222"
    assert len(calls) == 1, "a second search must not re-resolve the address"


async def test_an_unresolvable_address_is_not_fatal(monkeypatch):
    _patch_routes(
        monkeypatch,
        {
            "/profileService/address/list": ToolError("nope"),
            "/addressSuggestService/address/suggest": ToolError("nope"),
        },
    )

    assert await server._resolve_address_id(None) is None


def test_the_address_reaches_the_search_body():
    assert server._search_body("x", 1, "a-222")["addressId"] == "a-222"
    assert server._search_body("x", 1)["addressId"] is None


async def test_products_matched_but_no_offers_is_reported_not_swallowed(monkeypatch):
    """The exact live symptom: listingSize=44 with items=[].

    Returning success with zero results here would tell a shopper the product
    does not exist on Megamarket, when the catalog just said it has 44 of them.
    """
    _patch_routes(
        monkeypatch,
        {
            "/profileService/address/list": ToolError("nope"),
            "/addressSuggestService/address/suggest": ToolError("nope"),
            "/catalogService/catalog/search": {"success": True, "listingSize": 44, "items": []},
        },
    )

    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("ноутбук")

    message = str(excinfo.value)
    assert "44" in message
    assert "MEGAMARKET_ADDRESS" in message, "the error must say how to fix it"


async def test_a_genuine_zero_result_stays_a_success(monkeypatch):
    """listingSize 0 means the catalog really matched nothing."""
    _patch_routes(
        monkeypatch,
        {
            "/profileService/address/list": PROFILE_ADDRESSES,
            "/catalogService/catalog/search": {"success": True, "listingSize": 0, "items": []},
        },
    )

    result = await server.megamarket_search("заведомо несуществующий товар")

    assert result.count == 0
    assert any("empty_result" in w for w in result.meta.warnings)


# ---------------------------------------------- url/parse before the search ----
#
# A hand-built body with the right field names, a resolved address and
# requestVersion 10 still answered listingSize>0 with items=[]. The missing step
# was never in the body we wrote — it was the request we never made. Megamarket
# maps a text query to an assumed collection through urlService/url/parse, and
# the search endpoint wants that collection in collectionId and
# selectedAssumedCollectionId. The maintained xob0t/mmparser never builds a
# search body from a query either: it POSTs the catalog URL to url/parse first.

URL_PARSE_SEARCH = {
    "type": "TYPE_SEARCH",
    "params": {
        "searchText": "ноутбук",
        "collection": {"collectionId": "1000_2000", "title": "Ноутбуки"},
        "merchant": None,
        "selectedListingFilters": [],
        "isMultiCategorySearch": True,
    },
}

URL_PARSE_WITH_FILTERS = {
    "type": "TYPE_LISTING",
    "params": {
        "searchText": None,
        "collection": {"collectionId": "77_88"},
        "merchant": {"id": "9200", "slug": "shop"},
        "selectedListingFilters": [
            {"filterId": "price", "type": "LEFT_BOUND", "value": "10000"},
            {"filterId": "brand", "type": "EXACT_VALUE", "value": "lenovo"},
        ],
        "isMultiCategorySearch": False,
    },
}


async def test_url_parse_supplies_the_assumed_collection(monkeypatch):
    _patch_routes(monkeypatch, {"/urlService/url/parse": URL_PARSE_SEARCH})

    resolved = await server._resolve_search_params("ноутбук", None)

    assert resolved["collectionId"] == "1000_2000"
    assert resolved["selectedAssumedCollectionId"] == "1000_2000"
    assert resolved["isMultiCategorySearch"] is True


async def test_the_collection_reaches_the_search_body(monkeypatch):
    _patch_routes(monkeypatch, {"/urlService/url/parse": URL_PARSE_SEARCH})
    resolved = await server._resolve_search_params("ноутбук", None)

    body = server._search_body("ноутбук", 1, "a-222", resolved)

    assert body["collectionId"] == "1000_2000"
    assert body["selectedAssumedCollectionId"] == "1000_2000"
    assert body["addressId"] == "a-222"
    assert body["searchText"] == "ноутбук"


async def test_filter_bounds_are_converted_to_the_codes_the_api_wants(monkeypatch):
    """url/parse answers in words; the search endpoint wants 0/1/2."""
    _patch_routes(monkeypatch, {"/urlService/url/parse": URL_PARSE_WITH_FILTERS})

    resolved = await server._resolve_search_params("x", None)

    types = [f["type"] for f in resolved["selectedFilters"]]
    assert types == [1, 0], "LEFT_BOUND -> 1, EXACT_VALUE -> 0"
    assert resolved["merchant"] == {"id": "9200"}


async def test_a_menu_node_collection_is_picked_up(monkeypatch):
    _patch_routes(
        monkeypatch,
        {
            "/urlService/url/parse": {
                "type": "TYPE_MENU_NODE",
                "params": {
                    "searchText": None,
                    "collection": None,
                    "menuNode": {"collection": {"collectionId": "menu-42"}},
                    "selectedListingFilters": [],
                },
            }
        },
    )

    resolved = await server._resolve_search_params("x", None)

    assert resolved["collectionId"] == "menu-42"


async def test_a_failing_url_parse_does_not_block_the_search(monkeypatch):
    """Without the hints the search is weaker, not broken."""
    _patch_routes(monkeypatch, {"/urlService/url/parse": ToolError("nope")})

    assert await server._resolve_search_params("ноутбук", None) == {}


async def test_search_calls_url_parse_before_searching(monkeypatch):
    """The order matters: the collection has to be known before the query runs."""
    calls: list[str] = []

    async def tracking_post(api_path, body, ctx, what):
        calls.append(api_path)
        if "url/parse" in api_path:
            return URL_PARSE_SEARCH
        if "address" in api_path:
            return PROFILE_ADDRESSES
        return {
            "success": True,
            "listingSize": 1,
            "items": [
                {
                    "goods": {"goodsId": "1_2", "title": "Ноутбук", "webUrl": "u"},
                    "favoriteOffer": {"finalPrice": 50000},
                    "isAvailable": True,
                }
            ],
        }

    monkeypatch.setattr(server, "_post", tracking_post)

    result = await server.megamarket_search("ноутбук")

    assert result.count == 1
    assert result.items[0].price_rub == 50000
    search_at = next(i for i, c in enumerate(calls) if "catalog/search" in c)
    parse_at = next(i for i, c in enumerate(calls) if "url/parse" in c)
    assert parse_at < search_at, "url/parse must run before the search"


# ------------------------------------------- the search-to-category redirect ----
#
# The last piece, found live: /catalog/?q=ноутбук redirects to /catalog/noutbuki/.
# url/parse answers collection=None for the generic search URL and a real
# collection for the category it lands on. Feeding it the un-redirected URL is
# how the search kept returning listingSize=44 with items=[] even after the body
# and the address were right. Confirmed live: collection 502202, 44 items.

URL_PARSE_GENERIC = {
    "type": "TYPE_SEARCH",
    "params": {"searchText": "ноутбук", "collection": None, "merchant": None, "selectedListingFilters": []},
}
URL_PARSE_CATEGORY = {
    "type": "TYPE_LISTING",
    "params": {
        "searchText": None,
        "collection": {"collectionId": "502202", "title": "Ноутбуки"},
        "merchant": None,
        "selectedListingFilters": [],
    },
}


def _patch_page(monkeypatch, final_url):
    """Fake the CDP probe: open_page lands on final_url."""

    class FakePage:
        url = final_url

    class FakeCtx:
        async def __aenter__(self):
            return FakePage()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(server, "open_page", lambda url, wait_ms=0: FakeCtx())
    monkeypatch.setattr(server, "_min_gap", 0.0)


async def test_the_redirect_is_followed(monkeypatch):
    _patch_page(monkeypatch, "https://megamarket.ru/catalog/noutbuki/")

    final = await _REAL_FINAL_CATALOG_URL("https://megamarket.ru/catalog/?q=ноутбук", None)

    assert final == "https://megamarket.ru/catalog/noutbuki/"


async def test_no_redirect_keeps_the_original_url(monkeypatch):
    url = "https://megamarket.ru/catalog/?q=ноутбук"
    _patch_page(monkeypatch, url)

    assert await _REAL_FINAL_CATALOG_URL(url, None) == url


async def test_about_blank_is_not_mistaken_for_a_destination(monkeypatch):
    url = "https://megamarket.ru/catalog/?q=ноутбук"
    _patch_page(monkeypatch, "about:blank")

    assert await _REAL_FINAL_CATALOG_URL(url, None) == url


async def test_a_dead_browser_does_not_break_the_search(monkeypatch):
    """No Chrome means weaker results, not a failed tool."""
    url = "https://megamarket.ru/catalog/?q=ноутбук"

    def explode(*a, **k):
        raise RuntimeError("Chrome not reachable")

    monkeypatch.setattr(server, "open_page", explode)
    monkeypatch.setattr(server, "_min_gap", 0.0)

    assert await _REAL_FINAL_CATALOG_URL(url, None) == url


async def test_the_category_url_is_what_yields_a_collection(monkeypatch):
    """The whole bug in one test: same query, two URLs, two outcomes."""
    seen: list[str] = []

    async def fake_post(api_path, body, ctx, what):
        seen.append(body.get("url", ""))
        return URL_PARSE_CATEGORY if "noutbuki" in body.get("url", "") else URL_PARSE_GENERIC

    monkeypatch.setattr(server, "_post", fake_post)

    # Without following the redirect there is no collection.
    monkeypatch.setattr(server, "_final_catalog_url", _no_redirect)
    assert (await server._resolve_search_params("ноутбук", None))["collectionId"] is None

    # Following it produces the real category id.
    server._search_params_cache.clear()

    async def to_category(url, ctx=None):
        return "https://megamarket.ru/catalog/noutbuki/"

    monkeypatch.setattr(server, "_final_catalog_url", to_category)
    assert (await server._resolve_search_params("ноутбук", None))["collectionId"] == "502202"


async def test_resolved_params_are_cached_per_query(monkeypatch):
    """Each resolution costs a page load plus an API call."""
    calls: list[str] = []

    async def counting_post(api_path, body, ctx, what):
        calls.append(api_path)
        return URL_PARSE_CATEGORY

    monkeypatch.setattr(server, "_post", counting_post)
    monkeypatch.setattr(server, "_final_catalog_url", _no_redirect)

    first = await server._resolve_search_params("ноутбук", None)
    second = await server._resolve_search_params("ноутбук", None)

    assert first == second
    assert len(calls) == 1, "a repeated query must not re-open the browser"
