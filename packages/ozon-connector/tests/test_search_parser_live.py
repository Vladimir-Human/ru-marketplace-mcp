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

import pytest
from ozon_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "search_composer_live.json"

# Values read off the live capture when the fixture was built. The upstream
# uses THIN SPACE U+2009; prices are now numeric rubles, while stock stays raw.
T = "\u2009"
EXPECTED = [
    {
        "sku": "3497076095",
        "title": (
            'Neobihier Ноутбук 15.6", AMD Ryzen 9 5900HX, RAM 16 ГБ, SSD 512 ГБ, '
            "AMD Radeon Graphics, Windows Pro, белый, Русская раскладка"
        ),
        "price": 39534.0,
        "price_original": 66888.0,
        "stock": f"186{T}шт осталось",
    },
    {
        "sku": "2796072502",
        "title": (
            'Ноутбук 15.6", Intel Celeron N5095A, RAM 16 ГБ, SSD 512 ГБ, '
            "Intel UHD Graphics 750, Windows Pro, розовый, светло-розовый, Русская раскладка"
        ),
        "price": 32296.0,
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
        "price": 26838.0,
        "price_original": 203500.0,
        "stock": f"477{T}шт осталось",
    },
]


def _issue_payload(separator: str, count_text: str) -> dict:
    """Use the captured tile structure, replacing only issue #226 display values."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    key = next(iter(payload["widgetStates"]))
    widget = json.loads(payload["widgetStates"][key])
    tile = widget["items"][0]
    for atom in tile["mainState"]:
        if atom["type"] == "priceV2":
            atom["priceV2"]["price"][0]["text"] = f"17{separator}324 ₽"
            atom["priceV2"]["price"][1]["text"] = f"27{separator}949 ₽"
        elif atom["type"] == "labelListV2":
            labels = atom["labelListV2"]
            if labels["testInfo"]["automatizationId"] == "tile-list-rating":
                texts = [sub["text"] for sub in labels["items"] if sub["type"] == "text"]
                texts[0]["text"] = "4.9"
                texts[1]["text"] = count_text
    widget["items"] = [tile]
    payload["widgetStates"][key] = json.dumps(widget, ensure_ascii=False)
    return payload


def _patch_composer(monkeypatch, payload: dict) -> None:
    async def fake_fetch(path, ctx):
        return 200, json.dumps(payload, ensure_ascii=False), "fixture"

    monkeypatch.setattr(server, "_fetch_composer", fake_fetch)


@pytest.mark.parametrize("separator", [" ", "\u00a0", "\u202f", "\u2009"])
async def test_search_returns_numeric_issue_fields_from_a_captured_tile(monkeypatch, separator):
    """R-OZON-SEARCH-NUMERIC: localized display values become RUB/score/count numbers."""
    _patch_composer(monkeypatch, _issue_payload(separator, f"2{separator}518 отзывов"))

    result = await server.ozon_search("realme P4 8/256")

    assert result.count == 1
    item = result.items[0].model_dump()
    assert {key: item[key] for key in ("price", "price_original", "rating", "rating_count")} == {
        "price": 17324.0,
        "price_original": 27949.0,
        "rating": 4.9,
        "rating_count": 2518,
    }
    assert type(item["price"]) is float
    assert type(item["price_original"]) is float
    assert type(item["rating"]) is float
    assert type(item["rating_count"]) is int
    assert result.meta.healthy is True, result.meta.warnings


@pytest.mark.parametrize("count_text", ["2\u00a0518 оценок", "2\u202f518", "(2\u2009518)"])
async def test_search_keeps_supported_review_count_texts(monkeypatch, count_text):
    """R-OZON-SEARCH-NUMERIC: do not discard count forms the shared helper understands."""
    _patch_composer(monkeypatch, _issue_payload("\u00a0", count_text))

    result = await server.ozon_search("realme P4 8/256")

    assert result.items[0].rating_count == 2518


async def test_search_rejects_unparseable_values_without_losing_warnings(monkeypatch):
    """R-OZON-SEARCH-NUMERIC: refused values are null, with the existing drift warnings."""
    payload = _issue_payload("\u00a0", "1.2K отзывов")
    key = next(iter(payload["widgetStates"]))
    widget = json.loads(payload["widgetStates"][key])
    for atom in widget["items"][0]["mainState"]:
        if atom["type"] == "priceV2":
            atom["priceV2"]["price"][0]["text"] = "17 324 ₽ 27 949 ₽"
            atom["priceV2"]["price"][1]["text"] = "27 949 ₽ 29 999 ₽"
        elif atom["type"] == "labelListV2":
            for sub in atom["labelListV2"]["items"]:
                if sub["type"] == "text" and sub["text"]["text"] == "4.9":
                    sub["text"]["text"] = "6.5"
    payload["widgetStates"][key] = json.dumps(widget, ensure_ascii=False)
    _patch_composer(monkeypatch, payload)

    result = await server.ozon_search("realme P4 8/256")

    item = result.items[0]
    assert item.price is None
    assert item.price_original is None
    assert item.rating is None
    assert item.rating_count is None
    assert result.meta.healthy is False
    assert any(w.startswith("price_unparseable:") for w in result.meta.warnings)
    assert any(w.startswith("rating_out_of_range:") for w in result.meta.warnings)
    assert "_validation_warnings" not in item.model_dump()


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


def test_live_prices_are_numeric_rubles() -> None:
    """R-OZON-SEARCH-NUMERIC: the captured prices leave the parser as RUB floats."""
    for got, expected in zip(_items(), EXPECTED, strict=True):
        assert got["price"] == expected["price"]
        assert type(got["price"]) is float
        if got["price_original"] is not None:
            assert type(got["price_original"]) is float


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
