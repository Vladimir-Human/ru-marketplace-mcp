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
