"""Offline tests for the Lamoda connector.

GraphQL and CDP rendering are monkeypatched out: the suite runs with no network
and no Chrome. Fixtures mirror the shapes documented in ANTI_BOT.md — real
GraphQL product JSON, and the search-tile extraction from a rendered page.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from lamoda_connector import server

GRAPHQL_PRODUCT = {
    "sku": "MP002XM1RMM3",
    "name": "Кроссовки Nike Air Max",
    "brand_name": "Nike",
    "price_amount": 12990.0,
    "old_price": 16990.0,
    "is_available": True,
    "sizes": [
        {"size": "40", "is_available": True},
        {"size": "41", "is_available": False},
    ],
}

SEARCH_EXTRACTED = {
    "title": "кроссовки — Lamoda",
    "items": [
        {
            "sku": "MP002XM1RMM3",
            "title": "Кроссовки Nike Air Max",
            "brand": None,
            "price_rub": 12990.0,
            "old_price_rub": 16990.0,
            "url": "https://www.lamoda.ru/p/mp002xm1rmm3/",
        },
        {
            "sku": "MP002XM1RMM4",
            "title": "Кроссовки без цены",
            "brand": None,
            "price_rub": None,
            "old_price_rub": None,
            "url": "",
        },
    ],
}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()
    # The pacer is real, and its default gap is seconds. An offline suite that
    # actually sleeps it turns a 3-second run into a 90-second one and teaches
    # everyone to distrust the timings.
    monkeypatch.setattr(server, "_min_gap", 0.0)
    server._pacer.reset()


def _patch_graphql(monkeypatch, product):
    async def fake_graphql(sku, ctx):
        return product

    monkeypatch.setattr(server, "_graphql_card", fake_graphql)


def _patch_render(monkeypatch, payload):
    async def fake_render(query, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render_search", fake_render)


# --------------------------------------------------------------- lamoda_card ----


async def test_card_parses_graphql_product(monkeypatch):
    _patch_graphql(monkeypatch, GRAPHQL_PRODUCT)

    result = await server.lamoda_card("MP002XM1RMM3")

    assert result.title == "Кроссовки Nike Air Max"
    assert result.brand == "Nike"
    assert result.price_rub == 12990.0
    assert result.old_price_rub == 16990.0
    assert result.is_available is True
    assert len(result.sizes) == 2
    assert result.sizes[1].is_available is False
    assert result.tier_used == "graphql"


async def test_card_accepts_a_product_url(monkeypatch):
    _patch_graphql(monkeypatch, GRAPHQL_PRODUCT)

    result = await server.lamoda_card("https://www.lamoda.ru/p/mp002xm1rmm3/shoes-krossovki/")

    assert result.sku == "MP002XM1RMM3"


@pytest.mark.parametrize("bad", ["", "no-sku-here", "123456"])
async def test_card_rejects_input_without_a_sku(bad):
    with pytest.raises(ToolError):
        await server.lamoda_card(bad)


async def test_card_maps_an_empty_product_list_to_not_found(monkeypatch):
    async def fake_graphql(sku, ctx):
        raise ToolError(server.NotFoundError(f"Lamoda SKU {sku} returned no product."))

    monkeypatch.setattr(server, "_graphql_card", fake_graphql)

    with pytest.raises(ToolError):
        await server.lamoda_card("MP002XM1RMM3")


# ------------------------------------------------------------- lamoda_search ----


async def test_search_parses_tiles(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.lamoda_search("кроссовки")

    assert result.count == 2
    assert result.items[0].sku == "MP002XM1RMM3"
    assert result.items[0].price_rub == 12990.0


async def test_search_a_pricelss_item_is_none_never_zero(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.lamoda_search("кроссовки")

    assert result.items[1].price_rub is None
    assert result.items[1].price_rub != 0


async def test_search_warns_when_no_tile_has_a_price(monkeypatch):
    payload = {"title": "x", "items": [dict(SEARCH_EXTRACTED["items"][1])]}
    _patch_render(monkeypatch, payload)

    result = await server.lamoda_search("кроссовки")

    assert "no_prices_on_page" in result.meta.warnings


async def test_search_maps_zero_skus_to_parser_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "кроссовки", "items": []})

    with pytest.raises(ToolError):
        await server.lamoda_search("кроссовки")


# ---------------------------------------------------------- lamoda_selfcheck ----


async def test_selfcheck_healthy_when_both_tiers_answer(monkeypatch):
    _patch_graphql(monkeypatch, GRAPHQL_PRODUCT)
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.lamoda_selfcheck()

    assert result.status == "success"
    assert result.healthy is True
    assert result.checks["card_graphql"].state == "healthy"
    assert result.checks["search"].state == "healthy"


async def test_selfcheck_graphql_down_is_inconclusive(monkeypatch):
    async def fake_graphql(sku, ctx):
        raise ToolError(server.TransportDownError("Lamoda GraphQL answered HTTP 502"))

    monkeypatch.setattr(server, "_graphql_card", fake_graphql)
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.lamoda_selfcheck()

    assert result.status == "inconclusive"
    assert result.checks["card_graphql"].state == "inconclusive"


async def test_selfcheck_cdp_drift_is_flagged(monkeypatch):
    _patch_graphql(monkeypatch, GRAPHQL_PRODUCT)
    _patch_render(monkeypatch, {"title": "x", "items": []})

    result = await server.lamoda_selfcheck()

    assert result.status == "drift_detected"
    assert result.checks["search"].state == "drift"


# ------------------------------------------------------------------- helpers ----


def test_extract_sku():
    assert server._extract_sku("MP002XM1RMM3") == "MP002XM1RMM3"
    assert server._extract_sku("https://www.lamoda.ru/p/mp002xm1rmm3/shoes/") == "MP002XM1RMM3"
    assert server._extract_sku("кроссовки") is None


# --------------------------------------------------------- GraphQL error block ----
#
# GraphQL answers HTTP 200 with an `errors` array when a field or argument is
# rejected. The connector used to read only `data.products`, so that case
# surfaced as "no data.products list" — parser drift — and sent the reader to
# inspect our parsing while the server had already explained itself. It also
# asked for `old_price`, which is not the published field name (`old_price_amount`),
# so the whole query was rejected.


def test_the_query_asks_for_the_published_field_names():
    assert "old_price_amount" in server._GRAPHQL_QUERY
    assert "price_amount" in server._GRAPHQL_QUERY
    # `old_price` alone was the name that made Lamoda reject the query.
    assert " old_price " not in server._GRAPHQL_QUERY


def test_the_card_reads_either_old_price_name():
    """Tolerant on the way in, so a rename in either direction keeps working."""
    from mcp_core import resilience as R

    assert R.first_present({"old_price_amount": 5990}, "old_price_amount", "old_price") == 5990
    assert R.first_present({"old_price": 5990}, "old_price_amount", "old_price") == 5990


async def test_a_graphql_error_block_is_reported_verbatim(monkeypatch):
    """The server's own message beats a generic drift verdict."""

    class FakeResponse:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"errors": [{"message": 'Cannot query field "old_price" on type "Product".'}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    monkeypatch.setattr(server, "build_client", lambda **kwargs: FakeClient())
    server._cache._data.clear()

    with pytest.raises(ToolError) as excinfo:
        await server._graphql_card("MP002XM1RMM3", None)

    message = str(excinfo.value)
    assert "rejected the query" in message
    assert "old_price" in message, "the server's own wording must survive"


# ------------------------------------------------------ GraphQL request shape ----


async def test_the_graphql_request_carries_a_referer_for_the_sku(monkeypatch):
    """The route supports a product page, and working requests name that page.

    Lamoda search works from the same IP while card_graphql does not, so the
    difference is in the request, not the network.
    """
    seen: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"data": {"products": [{"sku": "MP002XM1RMM3", "name": "x", "price_amount": 100}]}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    def capture(**kwargs):
        seen.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(server, "build_client", capture)
    server._cache._data.clear()

    await server._graphql_card("MP002XM1RMM3", None)

    referer = seen["headers"]["Referer"]
    assert referer == "https://www.lamoda.ru/p/mp002xm1rmm3/"
    # The shared header dict must not be mutated for every later request.
    assert "Referer" not in server._HEADERS


async def test_a_non_200_carries_a_body_preview(monkeypatch):
    """A bare status code is a dead end; the body is the clue."""

    class FakeResponse:
        status_code = 403
        content = b"blocked by policy"
        text = "blocked by policy"

        def json(self):
            return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    monkeypatch.setattr(server, "build_client", lambda **kwargs: FakeClient())
    server._cache._data.clear()

    with pytest.raises(ToolError) as excinfo:
        await server._graphql_card("MP002XM1RMM3", None)

    message = str(excinfo.value)
    assert "403" in message
    assert "blocked by policy" in message


# ------------------------------------------------- the real GraphQL envelope ----
#
# Captured live against https://www.lamoda.ru/goapi/v2/catalog/graphql/products/
# in July 2026. Lamoda does not use the standard GraphQL envelope:
#
#   found     {"error": null, "result": [{...}]}
#   unknown   {"error": null, "result": null}
#   bad field {"error": "Internal server error", "code": -32603}
#
# The connector read `data.products` and an `errors` array, so every response
# looked like parser drift — including the successful ones. Asking for
# `old_price` instead of the published `old_price_amount` produced exactly the
# -32603 body above.

REAL_FOUND = {
    "error": None,
    "result": [
        {
            "brand_name": "Finn Flare",
            "old_price_amount": 18499,
            "sizes": [
                {"is_available": False, "size": "48", "stock_remains": 0},
                {"is_available": False, "size": "50", "stock_remains": 0},
            ],
            "discount": 15,
            "sku": "MP002XM1RMM3",
            "name": "Куртка кожаная",
            "price_amount": 15724,
            "is_available": False,
            "is_sellable": True,
            "stock_remains": 0,
        }
    ],
}
REAL_UNKNOWN = {"error": None, "result": None}
REAL_BAD_FIELD = {"error": "Internal server error", "code": -32603}


def _patch_graphql_response(monkeypatch, payload, status=200):
    class FakeResponse:
        status_code = status
        content = b"{}"
        text = "{}"

        def json(self):
            return payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    monkeypatch.setattr(server, "build_client", lambda **kwargs: FakeClient())
    server._cache._data.clear()


async def test_the_real_found_envelope_parses(monkeypatch):
    _patch_graphql_response(monkeypatch, REAL_FOUND)

    product = await server._graphql_card("MP002XM1RMM3", None)

    assert product["name"] == "Куртка кожаная"
    assert product["price_amount"] == 15724
    assert product["old_price_amount"] == 18499


async def test_the_card_maps_the_real_envelope_end_to_end(monkeypatch):
    _patch_graphql_response(monkeypatch, REAL_FOUND)

    result = await server.lamoda_card("MP002XM1RMM3")

    assert result.price_rub == 15724
    assert result.old_price_rub == 18499, "old_price_amount must reach the response"
    assert result.brand == "Finn Flare"
    assert result.is_available is False


async def test_a_null_result_is_not_found_not_drift(monkeypatch):
    """`result: null` is how an unknown SKU comes back."""
    _patch_graphql_response(monkeypatch, REAL_UNKNOWN)

    with pytest.raises(ToolError) as excinfo:
        await server._graphql_card("ZZ999ZZ999Z9", None)

    assert "not_found" in str(excinfo.value)


async def test_the_single_error_string_is_reported(monkeypatch):
    """Their failure shape is one `error` string plus a code, not an errors array."""
    _patch_graphql_response(monkeypatch, REAL_BAD_FIELD)

    with pytest.raises(ToolError) as excinfo:
        await server._graphql_card("MP002XM1RMM3", None)

    message = str(excinfo.value)
    assert "Internal server error" in message
    assert "-32603" in message
    assert "field name" in message, "the message must point at the likely cause"


async def test_the_standard_graphql_shape_still_works(monkeypatch):
    """Fallback if Lamoda ever moves to data.products."""
    _patch_graphql_response(monkeypatch, {"data": {"products": [{"sku": "X", "name": "y", "price_amount": 1}]}})

    product = await server._graphql_card("X", None)

    assert product["name"] == "y"
