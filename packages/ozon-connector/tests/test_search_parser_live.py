"""The Ozon search parse path against a LIVE captured composer payload.

``fixtures/search_composer_live.json`` is the real composer-api.bx response for
«ноутбук» captured 2026-08-07 through the operator's Chrome (provenance in
``search_composer_live.provenance.json``), trimmed to the first three product
tiles. Before this fixture existed every ozon_search test fed the parser an
invented payload, so the parse path had never touched the shape Ozon actually
serves — the same hole the DNS/Citilink/Lamoda/Taobao captures plugged.

The test runs ``_search_items_from_payload`` — the exact function the tool
calls after fetching — over the captured bytes. No jsdom needed: composer is
JSON, not DOM.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_core.resilience import coerce_price
from ozon_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "search_composer_live.json"

# Values read off the live capture when the fixture was built. Separator is the
# THIN SPACE U+2009 — exactly what Ozon renders, and exactly what both
# coerce_price and the compare _as_price delegation are proven to accept.
T = "\u2009"
EXPECTED = [
    {
        "sku": "3497076095",
        "title": (
            'Neobihier Ноутбук 15.6", AMD Ryzen 9 5900HX, RAM 16 ГБ, SSD 512 ГБ, '
            "AMD Radeon Graphics, Windows Pro, белый, Русская раскладка"
        ),
        "price": f"39{T}534{T}₽",
        "price_rub": 39534.0,
        "price_original": f"66{T}888{T}₽",
        "stock": f"186{T}шт осталось",
    },
    {
        "sku": "2796072502",
        "title": (
            'Ноутбук 15.6", Intel Celeron N5095A, RAM 16 ГБ, SSD 512 ГБ, '
            "Intel UHD Graphics 750, Windows Pro, розовый, светло-розовый, Русская раскладка"
        ),
        "price": f"32{T}296{T}₽",
        "price_rub": 32296.0,
        "price_original": None,
        "stock": None,
    },
    {
        "sku": "4695873085",
        "title": (
            "Ноутбук для учебы и работы игровой 4 ядра "
            'Ноутбук 15.6", Intel N95, RAM 32 ГБ, SSD 2048 ГБ, Windows Pro, '
            "серебристый, светло-серый, Русская раскладка"
        ),
        "price": f"26{T}838{T}₽",
        "price_rub": 26838.0,
        "price_original": f"203{T}500{T}₽",
        "stock": f"477{T}шт осталось",
    },
]


def _items() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return server._search_items_from_payload(payload)


def test_live_composer_payload_parses_to_the_three_tiles() -> None:
    items = _items()
    assert len(items) == 3, f"expected the three captured tiles, got {len(items)}"
    for got, expected in zip(items, EXPECTED, strict=True):
        assert str(got.get("sku")) == expected["sku"]
        assert got.get("title") == expected["title"]
        assert got.get("price") == expected["price"]
        assert got.get("price_original") == expected["price_original"]
        assert got.get("stock") == expected["stock"]


def test_live_price_strings_parse_to_the_displayed_numbers() -> None:
    """The display strings the live tiles carry must coerce to the numbers the
    page showed — this is the evidence the _as_price delegation stands on."""
    for got, expected in zip(_items(), EXPECTED, strict=True):
        assert coerce_price(got.get("price")) == expected["price_rub"]


def test_live_stock_labels_survive_the_parse_verbatim() -> None:
    """The tile carries the raw label («N шт осталось» or nothing); mapping it
    to in_stock is the compare connector's doctrine and is pinned there."""
    for got, expected in zip(_items(), EXPECTED, strict=True):
        assert got.get("stock") == expected["stock"]


def test_live_tiles_carry_a_canonical_card_input() -> None:
    """compare/ozon_card chain depends on card_input surviving the parse."""
    for got in _items():
        assert got.get("card_input"), "a live product tile lost its card_input"
        assert got.get("url", "").startswith("https://www.ozon.ru/product/")
