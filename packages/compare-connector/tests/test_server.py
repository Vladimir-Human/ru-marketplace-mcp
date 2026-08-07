"""Offline tests for cross-marketplace comparison.

Each marketplace's search is stubbed, so these tests exercise the part that only
this connector owns: merging, ranking, and reporting partial failures honestly.
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from compare_connector import server
from compare_connector.models_output import MarketOffer
from fastmcp.exceptions import ToolError
from pydantic import ValidationError


def offer(source: str, price: float | None, title: str = "товар", **kwargs) -> MarketOffer:
    return MarketOffer(source=source, title=title, price_rub=price, **kwargs)


def stub_sources(monkeypatch, impls: dict[str, object]):
    """Replace the per-marketplace search implementations.

    ``SOURCES`` is patched alongside so availability checks agree with the stubs.
    """
    monkeypatch.setattr(server, "_SEARCH_IMPLS", impls)
    monkeypatch.setattr(server, "SOURCES", dict.fromkeys(impls, object()))


def error_payload(err: ToolError) -> dict:
    return json.loads(str(err))


# ------------------------------------------------------------------ basics ----


def test_server_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == server.SERVER_VERSION


async def test_registered_tools_are_stable():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert names == {"compare_prices", "compare_sources"}


async def test_detsky_mir_is_not_a_comparison_source():
    """Detsky Mir has no working text search, so it must not join a text comparison.

    Including it would return products unrelated to the query — confidently wrong
    is worse than absent.
    """
    assert "detsky_mir" not in server.SEARCHABLE


# ----------------------------------------------------------------- ranking ----


async def test_offers_are_ranked_cheapest_first(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 1500.0), offer("wildberries", 700.0)]

    async def ya(query, limit):
        return [offer("yandex_market", 1000.0)]

    stub_sources(monkeypatch, {"wildberries": wb, "yandex_market": ya})

    result = await server.compare_prices(query="тест")

    assert [o.price_rub for o in result.offers] == [700.0, 1000.0, 1500.0]
    assert result.cheapest.price_rub == 700.0
    assert result.cheapest.source == "wildberries"
    assert result.price_spread_rub == 800.0
    assert result.complete is True


async def test_subscription_prices_never_win_the_ranking(monkeypatch):
    """A Plus-only price must not be presented as the cheapest available.

    Yandex's subscriber price runs 25-30% below its everyday price; ranking on it
    would fabricate a bargain that non-subscribers cannot buy.
    """

    async def wb(query, limit):
        return [offer("wildberries", 1000.0)]

    async def ya(query, limit):
        return [offer("yandex_market", 1200.0, price_with_subscription_rub=800.0)]

    stub_sources(monkeypatch, {"wildberries": wb, "yandex_market": ya})

    result = await server.compare_prices(query="тест")

    assert result.cheapest.source == "wildberries"
    assert result.cheapest.price_rub == 1000.0
    # The subscriber price is still reported, just not ranked on.
    yandex_offer = next(o for o in result.offers if o.source == "yandex_market")
    assert yandex_offer.price_with_subscription_rub == 800.0


async def test_unpriced_offers_are_kept_at_the_end(monkeypatch):
    """'Found but no price' is information; dropping it would hide stock reality."""

    async def wb(query, limit):
        return [offer("wildberries", None, title="без цены"), offer("wildberries", 500.0)]

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.total_offers == 2
    assert result.offers[0].price_rub == 500.0
    assert result.offers[-1].price_rub is None
    assert result.cheapest.price_rub == 500.0


async def test_spread_is_none_with_a_single_priced_offer(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 500.0)]

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.price_spread_rub is None


# ------------------------------------------------------- partial failures ----


async def test_one_source_failing_does_not_sink_the_comparison(monkeypatch):
    """The core resilience promise: three sources answering still beats nothing."""

    async def wb(query, limit):
        return [offer("wildberries", 500.0)]

    async def ozon(query, limit):
        raise RuntimeError('{"error": "transport_down", "message": "Cloudflare"}')

    stub_sources(monkeypatch, {"wildberries": wb, "ozon": ozon})

    result = await server.compare_prices(query="тест")

    assert result.total_offers == 1
    assert result.complete is False
    assert result.sources_ok == ["wildberries"]
    outcomes = {o.source: o for o in result.source_outcomes}
    assert outcomes["ozon"].status == "blocked"
    assert any("partial" in w for w in result.warnings)


async def test_a_timeout_is_reported_as_a_timeout(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 500.0)]

    async def slow(query, limit):
        await asyncio.sleep(5)
        return []

    stub_sources(monkeypatch, {"wildberries": wb, "yandex_market": slow})
    monkeypatch.setattr(server, "SOURCE_TIMEOUT_S", 0.05)

    result = await server.compare_prices(query="тест")

    outcomes = {o.source: o for o in result.source_outcomes}
    assert outcomes["yandex_market"].status == "timeout"
    assert result.complete is False
    assert result.total_offers == 1


async def test_a_generic_failure_is_reported_as_error_not_blocked(monkeypatch):
    """Anti-bot blocks and ordinary bugs need different responses, so they differ."""

    async def wb(query, limit):
        raise ValueError("unexpected shape in response")

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.source_outcomes[0].status == "error"
    assert result.total_offers == 0
    assert any("no_prices" in w for w in result.warnings)


async def test_sources_report_their_elapsed_time(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 100.0)]

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.source_outcomes[0].elapsed_ms >= 0
    assert result.source_outcomes[0].offers_returned == 1


async def test_a_missing_connector_is_distinguished_from_a_block(monkeypatch):
    """'Not installed' needs a different fix than 'refused us', so they differ."""

    async def wb(query, limit):
        return [offer("wildberries", 100.0)]

    monkeypatch.setattr(server, "_SEARCH_IMPLS", {"wildberries": wb, "ozon": wb})
    monkeypatch.setattr(server, "SOURCES", {"wildberries": object()})  # Ozon absent

    result = await server.compare_prices(query="тест", sources=["wildberries", "ozon"])

    outcomes = {o.source: o for o in result.source_outcomes}
    assert outcomes["ozon"].status == "not_installed"
    assert result.complete is False


async def test_sources_run_concurrently(monkeypatch):
    """Serial queries would make a four-source comparison unusably slow."""
    started: list[float] = []

    async def make(delay: float):
        async def impl(query, limit):
            started.append(asyncio.get_running_loop().time())
            await asyncio.sleep(delay)
            return [offer("x", 100.0)]

        return impl

    impls = {
        "wildberries": await make(0.1),
        "yandex_market": await make(0.1),
        "ozon": await make(0.1),
    }
    stub_sources(monkeypatch, impls)

    loop_start = asyncio.get_running_loop().time()
    await server.compare_prices(query="тест")
    elapsed = asyncio.get_running_loop().time() - loop_start

    assert len(started) == 3
    # Concurrent: ~0.1s total rather than ~0.3s serial.
    assert elapsed < 0.25


# -------------------------------------------------------------- validation ----


@pytest.mark.parametrize("bad_query", ["", " ", "x"])
async def test_short_queries_are_rejected(monkeypatch, bad_query):
    """Rejected either by the tool's own check or by pydantic's min_length.

    Both are correct outcomes; what matters is that no marketplace is queried.
    """

    async def fail(query, limit):
        raise AssertionError("a rejected query must never reach a marketplace")

    stub_sources(monkeypatch, {"wildberries": fail})

    with pytest.raises((ToolError, ValidationError)):
        await server.compare_prices(query=bad_query, sources=["wildberries"])


async def test_unknown_source_names_are_rejected(monkeypatch):
    async def wb(query, limit):
        return []

    stub_sources(monkeypatch, {"wildberries": wb})

    with pytest.raises(ToolError) as excinfo:
        await server.compare_prices(query="тест", sources=["aliexpress"])

    payload = error_payload(excinfo.value)
    assert payload["error"] == "bad_request"
    assert "aliexpress" in payload["message"]


async def test_all_requested_sources_missing_is_an_error(monkeypatch):
    """No installed source means the answer would be empty and misleading."""

    async def wb(query, limit):
        return []

    monkeypatch.setattr(server, "_SEARCH_IMPLS", {"wildberries": wb})
    monkeypatch.setattr(server, "SOURCES", {})

    with pytest.raises(ToolError) as excinfo:
        await server.compare_prices(query="тест", sources=["wildberries"])

    assert error_payload(excinfo.value)["error"] == "bad_request"


async def test_per_source_limit_is_passed_through(monkeypatch):
    seen: dict[str, int] = {}

    async def wb(query, limit):
        seen["limit"] = limit
        return []

    stub_sources(monkeypatch, {"wildberries": wb})

    await server.compare_prices(query="тест", per_source_limit=7, sources=["wildberries"])

    assert seen["limit"] == 7


# ----------------------------------------------------------- source report ----


async def test_compare_sources_reports_installed_and_missing(monkeypatch):
    monkeypatch.setattr(server, "SOURCES", {"wildberries": object(), "yandex_market": object()})

    report = await server.compare_sources()

    assert "wildberries" in report["installed"]
    assert "ozon" in report["not_installed"]
    assert report["server_version"] == server.SERVER_VERSION
    assert "source_timeout_s" in report


# --------------------------------------------------------- typed adapters ----


class _FakeOzonItem:
    """Mirrors OzonSearchItemOut: every value arrives as display text."""

    def __init__(self, **kw):
        self.sku = kw.get("sku")
        self.url = kw.get("url")
        self.canonical_path = kw.get("canonical_path")
        self.card_input = kw.get("card_input")
        self.title = kw.get("title")
        self.price = kw.get("price")
        self.price_original = kw.get("price_original")
        self.rating = kw.get("rating")
        self.rating_count = kw.get("rating_count")
        self.stock = kw.get("stock")


class _FakeResponse:
    def __init__(self, items):
        self.items = items


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 234 ₽", 1234.0),
        ("1 234 ₽", 1234.0),  # non-breaking space
        ("1 234,50 ₽", 1234.5),  # narrow no-break space, comma decimal
        ("999 ₽", 999.0),
        (1500, 1500.0),
        (1500.5, 1500.5),
        ("0 ₽", None),  # a zero price is not a bargain, it is no offer
        (0, None),
        ("", None),
        (None, None),
        ("нет цены", None),
        (True, None),  # a bool is not a price
    ],
)
def test_price_coercion_handles_ozon_display_strings(raw, expected):
    """Ozon reports prices as text; MarketOffer.price_rub is a float.

    Passing the raw string through raised a pydantic ValidationError, which would
    have taken down the entire Ozon source rather than one offer.
    """
    assert server._as_price(raw) == expected


def test_price_coercion_never_substitutes_zero():
    """A 0.0 would rank a dead listing as the cheapest option."""
    assert server._as_price("0 ₽") is None
    assert server._as_price(0) is None
    assert server._as_price(0.0) is None


def test_price_coercion_never_returns_a_negative():
    """A negative is not a price. Ranking one would crown it the cheapest
    offer, so it must degrade to no-offer exactly like a zero does."""
    assert server._as_price(-500) is None
    assert server._as_price(-500.0) is None
    assert server._as_price("-500 ₽") is None
    assert server._as_price("-1 234") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("24 086 отзывов", 24086),
        ("(15 374)", 15374),
        ("5", 5),
        (42, 42),
        (None, None),
        ("нет", None),
    ],
)
def test_count_coercion_handles_russian_review_labels(raw, expected):
    assert server._as_count(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("осталось 3 шт", True),
        ("осталось 0 шт", False),
        ("много шт", True),
        (5, True),
        (0, False),
        (None, None),  # unknown is not the same as out of stock
        ("", None),
    ],
)
def test_stock_label_coercion(raw, expected):
    assert server._stock_from_label(raw) is expected


async def test_ozon_adapter_reads_the_real_model_fields(monkeypatch):
    """The old adapter guessed keys OzonSearchItemOut does not declare.

    It read price_rub, reviews_count, feedbacks, name, id and brand — none exist,
    so each silently became None. This pins the real field names.
    """
    item = _FakeOzonItem(
        sku=123456,
        title="товар",
        price="1 999 ₽",
        rating="4,8",
        rating_count="24 086 отзывов",
        stock="осталось 3 шт",
        url="https://www.ozon.ru/product/123456/",
    )

    class FakeOzonServer:
        async def ozon_search(self, query):
            return _FakeResponse([item])

    monkeypatch.setitem(server.SOURCES, "ozon", FakeOzonServer())

    offers = await server._search_ozon("товар", 10)

    assert len(offers) == 1
    got = offers[0]
    assert got.product_id == "123456"
    assert got.price_rub == 1999.0
    assert got.rating == 4.8
    assert got.rating_count == 24086, "review count was always None before"
    assert got.in_stock is True, "stock was ignored entirely before"
    assert got.url == "https://www.ozon.ru/product/123456/"
    # Ozon search rows genuinely carry neither, so they are empty by definition.
    assert got.brand == ""
    assert got.seller == ""


async def test_ozon_adapter_survives_a_priceless_row(monkeypatch):
    class FakeOzonServer:
        async def ozon_search(self, query):
            return _FakeResponse([_FakeOzonItem(sku=1, title="без цены")])

    monkeypatch.setitem(server.SOURCES, "ozon", FakeOzonServer())

    offers = await server._search_ozon("тест", 10)

    assert offers[0].price_rub is None
    assert offers[0].in_stock is None


async def test_wildberries_adapter_reads_typed_attributes(monkeypatch):
    from wb_connector.models_output import WbCardItem

    class FakeWbServer:
        async def wb_search(self, query, page):
            return _FakeResponse(
                [
                    WbCardItem(
                        nm_id=5535522,
                        name="фильтр",
                        brand="DEFENDER",
                        supplier="Продавец",
                        review_rating=4.7,
                        feedbacks=120,
                        in_stock=True,
                        price_rub=1500.0,
                    )
                ]
            )

    monkeypatch.setitem(server.SOURCES, "wildberries", FakeWbServer())

    offers = await server._search_wildberries("фильтр", 10)

    got = offers[0]
    assert got.product_id == "5535522"
    assert got.price_rub == 1500.0
    assert got.rating == 4.7
    assert got.rating_count == 120
    assert got.in_stock is True
    assert got.url == "https://www.wildberries.ru/catalog/5535522/detail.aspx"


async def test_wildberries_adapter_tolerates_a_no_results_response(monkeypatch):
    """wb_search can return a distinct no-results model with no items at all."""
    from wb_connector.models_output import WbNoResultsResponse

    class FakeWbServer:
        async def wb_search(self, query, page):
            return WbNoResultsResponse(query="ничего")

    monkeypatch.setitem(server.SOURCES, "wildberries", FakeWbServer())

    assert await server._search_wildberries("ничего", 10) == []


async def test_yandex_adapter_keeps_the_subscriber_price_out_of_ranking(monkeypatch):
    from yandex_connector.models_output import YandexProduct

    class FakeYandexServer:
        async def yandex_search(self, query, page, limit):
            return _FakeResponse(
                [
                    YandexProduct(
                        product_id="777",
                        title="телефон",
                        brand="Бренд",
                        seller="Магазин",
                        price_rub=30000.0,
                        price_with_plus=21000.0,
                        rating=4.5,
                        rating_count=88,
                        in_stock=True,
                        url="https://market.yandex.ru/product/777",
                    )
                ]
            )

    monkeypatch.setitem(server.SOURCES, "yandex_market", FakeYandexServer())

    got = (await server._search_yandex("телефон", 10))[0]

    assert got.price_rub == 30000.0, "ranking must use the everyday price"
    assert got.price_with_subscription_rub == 21000.0


async def test_avito_adapter_maps_classified_fields(monkeypatch):
    from avito_connector.models_output import AvitoSearchItemOut

    class FakeAvitoServer:
        async def avito_search(self, query, page):
            return _FakeResponse(
                [
                    AvitoSearchItemOut(
                        item_id=4612345678,
                        title="Ноутбук ThinkPad",
                        price_rub=45500.0,
                        url="https://www.avito.ru/moskva/noutbuki/x_4612345678",
                        seller_name="ИП Сидоров",
                    ),
                    AvitoSearchItemOut(item_id=4612349999, title="Отдам даром", price_rub=None, url=""),
                ]
            )

    monkeypatch.setitem(server.SOURCES, "avito", FakeAvitoServer())

    got = await server._search_avito("ноутбук", 10)

    assert got[0].price_rub == 45500.0
    assert got[0].seller == "ИП Сидоров"
    assert got[1].price_rub is None, "a priceless ad stays priceless — never 0"


async def test_taobao_adapter_never_ranks_yuan_as_rubles(monkeypatch):
    from taobao_connector.models_output import TaobaoSearchItemOut

    class FakeTaobaoServer:
        async def taobao_search(self, query, page):
            return _FakeResponse(
                [
                    TaobaoSearchItemOut(
                        item_id="123456789012",
                        title="Apple iPhone 16",
                        price_cny=9999.0,
                        shop_name="苹果官方旗舰店",
                        url="https://item.taobao.com/item.htm?id=123456789012",
                    )
                ]
            )

    monkeypatch.setitem(server.SOURCES, "taobao", FakeTaobaoServer())

    got = await server._search_taobao("手机", 10)

    assert got[0].price_rub is None, "yuan must never enter ruble ranking"
    assert got[0].seller == "苹果官方旗舰店"
    assert "taobao" in server.FOREIGN_CURRENCY_SOURCES
    # The yuan figure must survive the adapter. Asserting only price_rub is
    # None proves nothing — that is hardcoded, so the old test passed while the
    # connector fetched a real price and threw it away.
    assert got[0].currency == "cny"
    assert got[0].price_native == 9999.0
    assert not server._ranks_in_rubles(got[0])


async def test_a_yuan_offer_never_becomes_cheapest(monkeypatch):
    """End-to-end: a cheap yuan number must not outrank a dearer rouble one.

    9999 ¥ is worth far more than 45500 ₽, but a naive comparator sees the
    smaller integer and calls Taobao the bargain. The ranking must exclude it
    on currency, and the response must still tell the user the yuan price.
    """
    offers = [
        MarketOffer(source="wildberries", product_id="1", title="iPhone", price_rub=45500.0),
        MarketOffer(
            source="taobao", product_id="2", title="iPhone", price_rub=None, currency="cny", price_native=9999.0
        ),
    ]

    priced = [offer for offer in offers if server._ranks_in_rubles(offer)]

    assert [offer.source for offer in priced] == ["wildberries"]
    assert offers[1].price_native == 9999.0, "the yuan price is reported, just not ranked"
    assert offers[0].price_native == 45500.0, "rouble sources mirror price_rub into price_native"


async def test_a_foreign_currency_offer_cannot_be_smuggled_into_the_ranking():
    """currency is checked independently of price_rub, on purpose.

    If an adapter regressed and filled price_rub with a yuan figure, the
    price_rub-is-not-None test alone would let it win. The currency guard is
    the backstop.
    """
    smuggled = MarketOffer(source="taobao", product_id="9", title="x", price_rub=300.0, currency="cny")

    assert not server._ranks_in_rubles(smuggled)


async def test_cdp_source_adapters_map_their_fields(monkeypatch):
    """Every CDP adapter must map its connector's typed model onto MarketOffer."""
    from megamarket_connector.models_output import MegamarketSearchItemOut

    class FakeMegamarket:
        async def megamarket_search(self, query):
            return _FakeResponse(
                [
                    MegamarketSearchItemOut(
                        item_id="1234567",
                        title="Стиральная машина",
                        price_rub=32990.0,
                        rating=4.7,
                        rating_count=213,
                        url="https://megamarket.ru/x",
                    )
                ]
            )

    monkeypatch.setitem(server.SOURCES, "megamarket", FakeMegamarket())
    got = await server._search_megamarket("стиральная машина", 10)
    assert got[0].price_rub == 32990.0
    assert got[0].rating == 4.7


async def test_every_searchable_source_has_an_impl_and_a_search_tool():
    """SEARCHABLE must never name a source that lacks a _SEARCH_IMPLS entry —
    that is the mismatch this audit caught."""
    for name in server.SEARCHABLE:
        assert name in server._SEARCH_IMPLS, f"{name} in SEARCHABLE but not in _SEARCH_IMPLS"
    for name in server._SEARCH_IMPLS:
        assert name in server.SEARCHABLE, f"{name} has an impl but is excluded from SEARCHABLE"


# ------------------------------------------------------------------ deduplication ----


def test_duplicate_listings_are_collapsed():
    """One listing must not occupy two ranking slots.

    A marketplace returning the same product twice (colour variants sharing an
    id, an overlapping page) used to produce two offers, so a single listing
    could be both cheapest and runner-up and read as two independent
    confirmations of the same price.
    """
    offers = [
        MarketOffer(source="wildberries", product_id="1", title="Ноутбук", price_rub=52999.0),
        MarketOffer(source="wildberries", product_id="1", title="Ноутбук", price_rub=52999.0),
        MarketOffer(source="ozon", product_id="1", title="Ноутбук", price_rub=51000.0),
    ]

    out = server._dedupe(offers)

    assert len(out) == 2, "same id on the same source is one listing"
    assert [(o.source, o.product_id) for o in out] == [("wildberries", "1"), ("ozon", "1")]


def test_dedupe_keeps_source_order():
    offers = [
        MarketOffer(source="wildberries", product_id="a", title="A", price_rub=3.0),
        MarketOffer(source="wildberries", product_id="b", title="B", price_rub=1.0),
        MarketOffer(source="wildberries", product_id="a", title="A", price_rub=3.0),
    ]

    assert [o.product_id for o in server._dedupe(offers)] == ["a", "b"]


def test_offers_without_an_id_are_never_merged():
    """A blank id is unknown, not shared — merging those would lose real offers."""
    offers = [
        MarketOffer(source="dns", product_id="", title="Первый", price_rub=100.0),
        MarketOffer(source="dns", product_id="", title="Второй", price_rub=200.0),
    ]

    assert len(server._dedupe(offers)) == 2


# ------------------------------------------------------------------- relevance ----
#
# Observed live in July 2026: a query for "iphone 15" returned a 34 224 ₽ offer
# ranked above genuine 52 049 ₽ ones. The tool trusts each marketplace's own
# relevance completely, so an accessory or a different configuration can become
# the headline "cheapest". These warn; they never drop a row, because no
# threshold tuned without live data should be allowed to hide a real bargain.


def test_an_accessory_as_cheapest_is_flagged():
    priced = [
        MarketOffer(source="wildberries", product_id="1", title="Чехол для iPhone 15 силиконовый", price_rub=990.0),
        MarketOffer(source="ozon", product_id="2", title="Apple iPhone 15 128GB", price_rub=52049.0),
    ]

    warnings = server._relevance_warnings("iphone 15", priced)

    assert any(w.startswith("relevance:") for w in warnings), warnings


def test_searching_for_the_accessory_itself_is_not_flagged():
    """Asking for a case and getting cases is the correct answer."""
    priced = [
        MarketOffer(source="wildberries", product_id="1", title="Чехол для iPhone 15", price_rub=990.0),
        MarketOffer(source="ozon", product_id="2", title="Чехол для iPhone 15 кожаный", price_rub=1990.0),
    ]

    assert server._relevance_warnings("чехол для iphone 15", priced) == []


def test_a_genuine_cheapest_is_left_alone():
    priced = [
        MarketOffer(source="wildberries", product_id="1", title="Apple iPhone 15 128GB", price_rub=49900.0),
        MarketOffer(source="ozon", product_id="2", title="Apple iPhone 15 128GB", price_rub=52049.0),
        MarketOffer(source="yandex_market", product_id="3", title="Apple iPhone 15", price_rub=60229.0),
    ]

    assert server._relevance_warnings("iphone 15", priced) == []


def test_a_price_far_below_the_median_is_flagged():
    priced = [
        MarketOffer(source="wildberries", product_id="1", title="iPhone 15 запчасть", price_rub=4000.0),
        MarketOffer(source="ozon", product_id="2", title="Apple iPhone 15 128GB", price_rub=52049.0),
        MarketOffer(source="yandex_market", product_id="3", title="Apple iPhone 15", price_rub=60229.0),
    ]

    warnings = server._relevance_warnings("iphone 15", priced)

    assert any(w.startswith("price_outlier:") for w in warnings), warnings


def test_the_outlier_check_needs_three_offers_to_have_a_median():
    priced = [
        MarketOffer(source="wildberries", product_id="1", title="iPhone 15", price_rub=1000.0),
        MarketOffer(source="ozon", product_id="2", title="iPhone 15", price_rub=52049.0),
    ]

    assert not any(w.startswith("price_outlier:") for w in server._relevance_warnings("iphone 15", priced))


async def test_relevance_warnings_reach_the_response(monkeypatch):
    """The warning is useless if it does not travel with the comparison."""

    async def wb(query, limit):
        return [offer("wildberries", 990.0, title="Чехол для iPhone 15")]

    async def ozon(query, limit):
        return [offer("ozon", 52049.0, title="Apple iPhone 15 128GB")]

    stub_sources(monkeypatch, {"wildberries": wb, "ozon": ozon})

    result = await server.compare_prices(query="iphone 15")

    assert any(w.startswith("relevance:") for w in result.warnings), result.warnings
    # Flagged, not removed: the offer is still ranked and still cheapest.
    assert result.cheapest.price_rub == 990.0


# ------------------------------------------------------------------- condition ----
#
# Checked live on wildberries.ru in July 2026 for the query "iphone 15": the
# cheap rows were "Смартфон iPhone 15, 256ГБ, Восстановленный" at 35 206 ₽ and
# "iPhone 15 256GB Витринный образец" — a refurbished phone and a display unit,
# ranked against new phones from other marketplaces. This is the case the
# accessory list misses: nothing in the title looks like a case, and 34 224 ₽
# against a 52 049 ₽ median is not low enough to trip the outlier check.


def test_a_refurbished_cheapest_is_flagged():
    priced = [
        MarketOffer(
            source="wildberries",
            product_id="1",
            title="Смартфон iPhone 15, 256ГБ, Восстановленный Apple",
            price_rub=34224.0,
        ),
        MarketOffer(source="ozon", product_id="2", title="Apple iPhone 15 128GB", price_rub=52049.0),
        MarketOffer(source="yandex_market", product_id="3", title="Apple iPhone 15", price_rub=60229.0),
    ]

    warnings = server._relevance_warnings("iphone 15", priced)

    assert any(w.startswith("condition:") for w in warnings), warnings
    assert not any(w.startswith("price_outlier:") for w in warnings), (
        "the real case does not trip the median check — that is why the condition check exists"
    )


def test_a_display_unit_is_flagged():
    priced = [
        MarketOffer(
            source="wildberries", product_id="1", title="iPhone 15 256GB Витринный образец esim", price_rub=40000.0
        ),
        MarketOffer(source="ozon", product_id="2", title="Apple iPhone 15 256GB", price_rub=52049.0),
    ]

    assert any(w.startswith("condition:") for w in server._relevance_warnings("iphone 15", priced))


def test_asking_for_a_refurbished_phone_is_not_flagged():
    """Searching for refurbished and getting refurbished is the right answer."""
    priced = [
        MarketOffer(source="wildberries", product_id="1", title="iPhone 15 Восстановленный", price_rub=34224.0),
        MarketOffer(source="ozon", product_id="2", title="iPhone 15 восстановленный", price_rub=39000.0),
    ]

    assert not any(w.startswith("condition:") for w in server._relevance_warnings("iphone 15 восстановленный", priced))


def test_a_new_product_carries_no_condition_warning():
    priced = [
        MarketOffer(source="wildberries", product_id="1", title="Apple iPhone 15 128GB черный", price_rub=49900.0),
        MarketOffer(source="ozon", product_id="2", title="Apple iPhone 15 128GB", price_rub=52049.0),
    ]

    assert server._relevance_warnings("iphone 15", priced) == []
