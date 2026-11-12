"""The comparison adapter must not turn a display text into a wrong count.

The outside-in study (R05) found the same defect on both sides of the
comparison: Ozon's tile parser and compare's count adapter glued every digit
of the display text together, so "1.2K отзывов" became the plausible-but-wrong
12 — through the native tool AND through the comparison — while the answer
stayed healthy.

These tests drive the real Ozon connector (only its transport is faked) and
read the comparison's own offer, so the whole native -> compare path is
covered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from compare_connector import server as compare
from ozon_connector import server as ozon


def _tile(sku: int, count_text: str) -> dict:
    return {
        "sku": sku,
        "action": {"link": f"/product/acme-{sku}/"},
        "mainState": [
            {"type": "textAtom", "textAtom": {"text": "ACME X100"}},
            {"type": "priceV2", "priceV2": {"price": [{"text": "3983 ₽"}]}},
            {
                "type": "labelListV2",
                "labelListV2": {
                    "testInfo": {"automatizationId": "tile-list-rating"},
                    "items": [
                        {"type": "text", "text": {"text": "4,8"}},
                        {"type": "text", "text": {"text": count_text}},
                    ],
                },
            },
        ],
    }


def _patch_ozon(monkeypatch, tiles: list[dict]) -> None:
    async def fake_fetch(path, ctx):
        return 200, json.dumps({"widgetStates": {"tileGridDesktop-test": {"items": tiles}}}), "fake"

    monkeypatch.setattr(ozon, "_fetch_composer", fake_fetch)
    monkeypatch.setitem(compare.SOURCES, "ozon", ozon)
    ozon._cache.clear()


@pytest.mark.parametrize("rating_text,priced", [("4.9", True), ("0.0", True), ("4.9", False)])
async def test_numeric_search_fields_reach_compare_unchanged(monkeypatch, rating_text, priced):
    """R-OZON-SEARCH-NUMERIC: numeric/null prices, zero ratings and counts survive comparison."""
    fixture = Path(__file__).resolve().parents[2] / "ozon-connector/tests/fixtures/search_composer_live.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    widget = json.loads(next(iter(payload["widgetStates"].values())))
    tile = widget["items"][0]
    for atom in tile["mainState"]:
        if atom["type"] == "priceV2":
            atom["priceV2"]["price"][0]["text"] = "17\u00a0324 ₽" if priced else "17 324 ₽ 27 949 ₽"
            atom["priceV2"]["price"][1]["text"] = "27\u202f949 ₽"
        elif atom["type"] == "labelListV2":
            labels = atom["labelListV2"]
            if labels["testInfo"]["automatizationId"] == "tile-list-rating":
                texts = [sub["text"] for sub in labels["items"] if sub["type"] == "text"]
                texts[0]["text"] = rating_text
                texts[1]["text"] = "2\u202f518 оценок"
    _patch_ozon(monkeypatch, [tile])

    result = await compare.compare_prices("ноутбук", sources=["ozon"])

    offer = result.offers[0]
    assert offer.price_rub == (17324.0 if priced else None)
    assert offer.rating == float(rating_text)
    assert offer.rating_count == 2518
    if priced:
        assert result.cheapest.price_rub == 17324.0
    else:
        assert result.cheapest is None  # the original price is never a substitute offer
        assert any("price_unparseable" in w for w in result.warnings)


async def test_a_magnitude_review_count_never_reaches_the_comparison_as_an_integer(monkeypatch):
    """R05: the compared offer carries no count, not a manufactured one."""
    _patch_ozon(monkeypatch, [_tile(123456, "1.2K отзывов")])

    result = await compare.compare_prices("ACME X100", sources=["ozon"])

    offer = result.offers[0]
    assert offer.rating_count is None, offer.rating_count
    assert offer.rating_count != 12


async def test_a_grouped_review_count_reaches_the_comparison_unchanged(monkeypatch):
    """The positive control: the ordinary count keeps its real value."""
    _patch_ozon(monkeypatch, [_tile(123456, "1 200 отзывов")])

    result = await compare.compare_prices("ACME X100", sources=["ozon"])

    assert result.offers[0].rating_count == 1200
