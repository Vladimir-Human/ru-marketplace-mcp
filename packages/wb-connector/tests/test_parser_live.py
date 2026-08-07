"""The WB card-item parser against LIVE captured API bodies.

``fixtures/search_v9_live.json`` and ``fixtures/card_v4_live.json`` are the
real search.wb.ru v9 and card.wb.ru v4 responses for «ноутбук» captured
2026-08-07 (provenance in the matching ``*.provenance.json``), trimmed to the
first three products. Before these fixtures existed, ``_card_item_dict`` and
``_extract_price_rub`` were only exercised against invented payloads — this
runs the exact flattening both ``wb_search`` and ``wb_card`` use on the bytes
Wildberries actually serves, and pins the documented search-vs-card price gap.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from wb_connector import server
from wb_connector.models_output import WbCardItem

FIXTURES = Path(__file__).parent / "fixtures"

# Values read off the live capture when the fixtures were built.
SEARCH_EXPECTED = [
    {
        "nm_id": 1280469586,
        "name": "Ноутбук 16 дюймов Ryzen 5-7535HS 16Гб 1ТБ WUXGA Win11 K16SFA",
        "brand": "TECNO",
        "price_rub": 55621.0,
        "price_original_rub": 74460.0,
        "total_quantity": 23,
        "in_stock": True,
    },
    {
        "nm_id": 824935779,
        "name": "Ноутбук MateBook D16 (53014MUA)",
        "brand": "Huawei",
        "price_rub": 60571.0,
        "price_original_rub": 81087.0,
        "total_quantity": 10,
        "in_stock": True,
    },
    {
        "nm_id": 497398643,
        "name": 'Ноутбук Crosshair A16 D8WGKG 16" Ryzen 7 32Gb SSD1Tb RTX5070',
        "brand": "MSI",
        "price_rub": 149851.0,
        "price_original_rub": 200604.0,
        "total_quantity": 67,
        "in_stock": True,
    },
]

CARD_EXPECTED = {
    "nm_id": 1280469586,
    "name": "Ноутбук 16 дюймов Ryzen 5-7535HS 16Гб 1ТБ WUXGA Win11 K16SFA",
    "brand": "TECNO",
    "price_rub": 54676.0,
    "price_original_rub": 70824.0,
    "total_quantity": 26,
    "in_stock": True,
}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_live_search_products_flatten_to_the_displayed_values() -> None:
    body = _load("search_v9_live.json")
    products = body["products"]
    assert len(products) == 3
    for raw, expected in zip(products, SEARCH_EXPECTED, strict=True):
        item = server._card_item_dict(raw)
        for key, value in expected.items():
            assert item[key] == value, f"{key}: {item[key]!r} != {value!r}"


def test_live_card_flattens_to_the_displayed_values() -> None:
    body = _load("card_v4_live.json")
    (raw,) = body["products"]
    item = server._card_item_dict(raw)
    for key, value in CARD_EXPECTED.items():
        assert item[key] == value, f"{key}: {item[key]!r} != {value!r}"


def test_live_prices_are_finite_positive_rubles() -> None:
    """The doctrine pinned by the audit waves, checked against live bytes: a
    WB price is a finite positive float or None, never 0/inf/nan."""
    for name in ("search_v9_live.json", "card_v4_live.json"):
        for raw in _load(name)["products"]:
            current, original = server._extract_price_rub(raw)
            for price in (current, original):
                if price is not None:
                    assert price > 0 and math.isfinite(price)


def test_live_items_build_the_wire_model() -> None:
    """The same dict flows into WbCardItem in wb_search/wb_card — build it."""
    for name in ("search_v9_live.json", "card_v4_live.json"):
        for raw in _load(name)["products"]:
            WbCardItem(**server._card_item_dict(raw))


def test_the_fixture_pair_freezes_the_search_vs_card_gap() -> None:
    """nm 1280469586 priced differently on the two endpoints the same minute.
    If a future capture makes this vanish the fixture pair must be rebuilt, and
    the release notes' claim about the gap with it."""
    search_price = server._card_item_dict(_load("search_v9_live.json")["products"][0])["price_rub"]
    card_price = server._card_item_dict(_load("card_v4_live.json")["products"][0])["price_rub"]
    assert search_price == 55621.0
    assert card_price == 54676.0
    assert search_price != card_price
