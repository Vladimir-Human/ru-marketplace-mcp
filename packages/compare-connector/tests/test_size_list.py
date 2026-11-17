"""A title that lists several sizes names no one variant, so it is never cheapest_comparable.

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-SIZE-LIST on lists_sizes and through the public compare_prices entry.
SCOPE: identity.lists_sizes; compare_prices with the AliExpress and Citilink search tools replaced by native responses.
DEPENDS: pytest; compare_connector.identity/server; aliexpress/citilink output models.
LINKS: M-COMPARE-DECIDE; R-COMPARE-SIZE-LIST; G-CAPTURE-MUTATION-STUFFED-TITLES (P2-1 m6b memory family).
END_MODULE_CONTRACT
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aliexpress_connector.models_output import AliSearchItemOut, AliSearchResponse
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from compare_connector.identity import lists_sizes


@pytest.mark.req("R-COMPARE-SIZE-LIST")
@pytest.mark.parametrize(
    "title",
    [
        "Смартфон Apple iPhone 16 128GB/256GB/512GB",
        "iPhone 16 128/256/512 ГБ",
        "Робот-пылесос 64 GB / 128 GB / 256 GB / 512 GB",
        "Карта памяти 128 ГБ или 256 ГБ",
        "SSD 512GB | 1TB",
        "Флешка 64GB, 128GB",
        "Ноутбук 8GB/16GB RAM",
    ],
)
def test_a_title_listing_several_sizes_names_no_one_variant(title: str) -> None:
    assert lists_sizes(title)


@pytest.mark.req("R-COMPARE-SIZE-LIST")
@pytest.mark.parametrize(
    "title",
    [
        "Смартфон Xiaomi Redmi Note 14 8/256GB",
        "Смартфон Samsung Galaxy A55 8 ГБ / 256 ГБ",
        "Ноутбук 16GB/512GB SSD",
        "Ноутбук 32 ГБ / 1 ТБ",
        "SSD Samsung 990 PRO 1TB (1000 ГБ)",
        "SSD Samsung 990 PRO 1 ТБ / 1000 ГБ",
        "Смартфон Apple iPhone 16 128 ГБ, черный",
        "Компьютер SSD 512 ГБ + HDD 1 ТБ",
        "Чайник электрический 1.7 л",
    ],
)
def test_one_size_a_ram_storage_pair_or_one_size_in_two_units_is_no_list(title: str) -> None:
    assert not lists_sizes(title)


@pytest.mark.req("R-COMPARE-SIZE-LIST")
async def test_compare_prices_skips_a_size_list_for_cheapest_comparable_but_keeps_it_cheapest(monkeypatch) -> None:
    async def aliexpress_search(**kwargs):
        item = AliSearchItemOut(
            item_id="100500123456", title="Смартфон Apple iPhone 16 128GB/256GB/512GB", price_rub=52990
        )
        return AliSearchResponse(items=[item])

    async def citilink_search(**kwargs):
        items = [
            CitilinkSearchItemOut(product_id="2001", title="Смартфон Apple iPhone 16 256 ГБ, черный", price_rub=79990)
        ]
        return CitilinkSearchResponse(items=items)

    monkeypatch.setitem(server.SOURCES, "aliexpress", SimpleNamespace(aliexpress_search=aliexpress_search))
    monkeypatch.setitem(server.SOURCES, "citilink", SimpleNamespace(citilink_search=citilink_search))

    result = await server.compare_prices("iphone 16 256gb", sources=["aliexpress", "citilink"])

    assert (result.cheapest.source, result.cheapest.price_rub) == ("aliexpress", 52990)
    assert (result.cheapest_comparable.source, result.cheapest_comparable.product_id) == ("citilink", "2001")
    assert any(w.startswith("comparable_price:") for w in result.warnings)
