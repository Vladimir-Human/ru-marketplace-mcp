"""Ozon's search answer: what a review count is, and how many items came back.

Two defects are covered here, both seen from outside the connector through its
public ``ozon_search`` tool with a synthetic composer payload (the shape the
real one has — see ``tests/fixtures/search_composer_live.json``):

* R05: the tile parser glued every digit of the display text together, so
  "1.2K отзывов" became the plausible-but-wrong 12 and the answer stayed
  healthy. A count must be the number the text names, or nothing.
* R06: ``count`` was the number of parsed tiles while ``items`` carried the
  first thirty of them, so a page of forty tiles answered count=40 with
  thirty items and no word that anything had been cut.

The positive controls are in the same file: the ordinary grouped count still
parses, and a complete short answer is still healthy.
"""

from __future__ import annotations

import json

from ozon_connector import server


def _tile(sku: int, count_text: str) -> dict:
    """One search tile in the shape the composer sends (Nov 2026)."""
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


def _patch_composer(monkeypatch, tiles: list[dict]) -> None:
    async def fake_fetch(path, ctx):
        return 200, json.dumps({"widgetStates": {"tileGridDesktop-test": {"items": tiles}}}), "fake"

    monkeypatch.setattr(server, "_fetch_composer", fake_fetch)
    server._cache.clear()


async def test_a_magnitude_review_count_is_not_read_as_a_plausible_integer(monkeypatch):
    """R05: "1.2K отзывов" must not become 12.

    The old parser concatenated every digit of the display text, so a
    magnitude form read as the plausible-but-wrong twelve and the answer
    stayed healthy. A count that the text does not name exactly is no count.
    """
    _patch_composer(monkeypatch, [_tile(123456, "1.2K отзывов")])

    result = await server.ozon_search("ACME X100")

    assert result.items[0].rating_count is None, result.items[0].rating_count
    assert result.items[0].rating_count != 12


async def test_a_grouped_review_count_still_parses(monkeypatch):
    """The positive control: the ordinary forms keep their real value."""
    _patch_composer(monkeypatch, [_tile(123456, "1 200 отзывов")])

    result = await server.ozon_search("ACME X100")

    assert result.items[0].rating_count == 1200


async def test_the_search_count_is_the_number_of_returned_items(monkeypatch):
    """R06: count is len(items), and a cut page says so.

    Forty valid tiles were answered as count=40 with thirty items and no
    truncation word. The answer now names the parsed total separately and
    says that the page was cut.
    """
    _patch_composer(monkeypatch, [_tile(123456 + index, "339 отзывов") for index in range(40)])

    result = await server.ozon_search("ACME X100")

    assert result.count == len(result.items) == 30
    assert result.parsed_count == 40
    assert result.truncated is True
    assert any("truncat" in warning for warning in result.meta.warnings), result.meta.warnings
    # The cap is the tool's own delivery limit, not damage: 30 valid items of
    # 40 valid tiles is a healthy answer that says it was cut.
    assert result.meta.healthy is True, result.meta.warnings


async def test_a_complete_short_answer_is_not_marked_damaged(monkeypatch):
    """The positive control: a page that fits is complete and healthy."""
    _patch_composer(monkeypatch, [_tile(123456, "339 отзывов")])

    result = await server.ozon_search("ACME X100")

    assert result.count == len(result.items) == 1
    assert result.parsed_count == 1
    assert result.truncated is False
    assert not [warning for warning in result.meta.warnings if "truncat" in warning]
    assert result.meta.healthy is True


async def test_the_health_check_runs_on_the_items_the_agent_receives(monkeypatch):
    """The drift check must see the delivered answer, not the cut-off part."""
    _patch_composer(monkeypatch, [_tile(123456 + index, "339 отзывов") for index in range(40)])

    result = await server.ozon_search("ACME X100")

    # Every delivered item still carries its own fields, so the family check
    # sees a complete answer and reports nothing but the truncation.
    assert all(item.url for item in result.items)
    assert not [warning for warning in result.meta.warnings if warning.startswith("shape_drift:")]
