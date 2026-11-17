"""A title that names several models of one family names no one product, so it is never cheapest_comparable.

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-MODEL-LIST on lists_models and through the public compare_prices entry.
SCOPE: identity.lists_models; compare_prices with the AliExpress and Citilink search tools replaced by native responses.
DEPENDS: pytest; compare_connector.identity/server; aliexpress/citilink output models.
LINKS: M-COMPARE-DECIDE; R-COMPARE-MODEL-LIST; P2-1 m6b models family; issue #313.
END_MODULE_CONTRACT
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aliexpress_connector.models_output import AliSearchItemOut, AliSearchResponse
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from compare_connector.identity import lists_models


@pytest.mark.req("R-COMPARE-MODEL-LIST")
@pytest.mark.parametrize(
    "title",
    [
        # Real titles from the committed recordings.
        "Фильтр для Dyson V11/V15",
        "Фильтр для пылесоса Dyson V11, V15, SV14, SV17, SV22",
        "原装戴森DysonV8/V10/V11/V12/V15吸尘器主机95含电池不含充电器",
        # The P2-1 m6b stuffing and plain lists.
        "Пылесос Dyson V15 Detect Apple iPhone 15 / iPhone 16 / Samsung Galaxy S24 / S25",
        "Смартфон Apple iPhone 15 / iPhone 16",
        "Смартфон Samsung Galaxy S24 или S25",
        "Смартфон Xiaomi Redmi Note 13 / Note 14 256GB",
    ],
)
def test_a_title_naming_several_models_of_one_family_names_no_one_product(title: str) -> None:
    assert lists_models(title)


@pytest.mark.req("R-COMPARE-MODEL-LIST")
@pytest.mark.parametrize(
    "title",
    [
        "Смартфон Apple iPhone 16, 8/128ГБ",
        "Ноутбук i7-12700H / RTX3060 / Win11",
        "Смартфон Blackview BV9300 IP68/IP69K",
        "Кабель USB 3.0/3.1",
        "Оперативная память DDR4/DDR5",
        "Смартфон Xiaomi Redmi Note 14 / Note 14 Pro",
        "Игровая консоль PlayStation 5 Slim / PS5",
        "Пылесос Dyson V15 Detect Absolute",
        "Подгузники Pampers Premium Care 4 (9-14 кг) 82 шт",
        "Монитор 27/32 дюйма",
        "Адаптер V10 / V12 мм",
    ],
)
def test_one_model_a_spec_list_or_a_size_is_no_model_list(title: str) -> None:
    assert not lists_models(title)


def _sources(monkeypatch: pytest.MonkeyPatch, listed_title: str) -> None:
    async def aliexpress_search(**kwargs):
        return AliSearchResponse(items=[AliSearchItemOut(item_id="100500123456", title=listed_title, price_rub=39990)])

    async def citilink_search(**kwargs):
        items = [CitilinkSearchItemOut(product_id="2001", title="Пылесос Dyson V15 Detect Absolute", price_rub=69990)]
        return CitilinkSearchResponse(items=items)

    monkeypatch.setitem(server.SOURCES, "aliexpress", SimpleNamespace(aliexpress_search=aliexpress_search))
    monkeypatch.setitem(server.SOURCES, "citilink", SimpleNamespace(citilink_search=citilink_search))


@pytest.mark.req("R-COMPARE-MODEL-LIST")
async def test_compare_prices_skips_a_model_list_for_cheapest_comparable_but_keeps_it_cheapest(monkeypatch) -> None:
    _sources(monkeypatch, "Пылесос Dyson V12 / V15 Detect Slim")

    result = await server.compare_prices("dyson v15 detect", sources=["aliexpress", "citilink"])

    assert (result.cheapest.source, result.cheapest.price_rub) == ("aliexpress", 39990)
    assert (result.cheapest_comparable.source, result.cheapest_comparable.product_id) == ("citilink", "2001")


@pytest.mark.req("R-COMPARE-MODEL-LIST")
async def test_a_query_that_lists_models_itself_keeps_such_titles_comparable(monkeypatch) -> None:
    _sources(monkeypatch, "Пылесос Dyson V12 / V15 Detect Slim")

    result = await server.compare_prices("dyson v12/v15 detect", sources=["aliexpress", "citilink"])

    assert (result.cheapest_comparable.source, result.cheapest_comparable.price_rub) == ("aliexpress", 39990)
