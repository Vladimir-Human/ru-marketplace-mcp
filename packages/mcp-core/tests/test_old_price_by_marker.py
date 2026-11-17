"""An explicit old-price marker owns its price role, even when unreadable."""

# START_MODULE_CONTRACT
# PURPOSE: Keep marked old prices separate from ranked tile candidates.
# SCOPE: Pure prices_from_tile inputs; no browser or transport.
# DEPENDS: pytest; mcp_core.dom.
# LINKS: M-DOM; R-OLD-PRICE-BY-MARKER.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_missing_marked_old_price_stays_unknown: A null marker blocks rank fallback.
# test_marked_old_price_wins: A readable marker supplies the old price.
# test_unmarked_tile_keeps_rank_rule: Without the mark, the largest higher candidate wins.
# test_marked_old_price_at_or_below_current_is_unknown: Non-discounts stay null.
# test_unreadable_marked_old_price_stays_unknown: Refused text blocks rank fallback.
# test_legacy_numeric_old_price_is_still_accepted: Cached numeric fields remain compatible.
# test_an_unmarked_tile_with_a_null_old_price_text_keeps_the_rank_rule: Citilink's card shape.
# END_MODULE_MAP

import pytest
from mcp_core.dom import prices_from_tile

# START_BLOCK_MARKER_PRICE_TESTS


@pytest.mark.req("R-OLD-PRICE-BY-MARKER")
def test_missing_marked_old_price_stays_unknown() -> None:
    tile = {
        "price_text": "6 569 ₽",
        "old_price_text": None,
        "old_price_marked": True,
        "price_texts": {"other": ["8 999", "12 999"]},
    }
    assert prices_from_tile(tile) == (6569.0, None)


@pytest.mark.req("R-OLD-PRICE-BY-MARKER")
def test_marked_old_price_wins() -> None:
    tile = {
        "price_text": "6 569 ₽",
        "old_price_text": "12 999",
        "old_price_marked": True,
        "price_texts": {"other": ["8 999", "12 999"]},
    }
    assert prices_from_tile(tile) == (6569.0, 12999.0)


@pytest.mark.req("R-OLD-PRICE-BY-MARKER")
def test_unmarked_tile_keeps_rank_rule() -> None:
    tile = {
        "price_text": "6 569 ₽",
        "price_texts": {"other": ["8 999", "12 999"]},
    }
    assert prices_from_tile(tile) == (6569.0, 12999.0)


@pytest.mark.req("R-OLD-PRICE-BY-MARKER")
@pytest.mark.parametrize("old_price_text", ["6 569", "5 999"])
def test_marked_old_price_at_or_below_current_is_unknown(old_price_text: str) -> None:
    tile = {
        "price_text": "6 569 ₽",
        "old_price_text": old_price_text,
        "old_price_marked": True,
        "price_texts": {"other": ["8 999", "12 999"]},
    }
    assert prices_from_tile(tile) == (6569.0, None)


@pytest.mark.req("R-OLD-PRICE-BY-MARKER")
@pytest.mark.parametrize("old_price_text", ["", "8 999 ₽12 999", "от 12 999 ₽/ мес."])
def test_unreadable_marked_old_price_stays_unknown(old_price_text: str) -> None:
    tile = {
        "price_text": "6 569 ₽",
        "old_price_text": old_price_text,
        "old_price_marked": True,
        "price_texts": {"other": ["8 999", "12 999"]},
    }
    assert prices_from_tile(tile) == (6569.0, None)


@pytest.mark.req("R-OLD-PRICE-BY-MARKER")
def test_legacy_numeric_old_price_is_still_accepted() -> None:
    tile = {
        "price_text": "6 569 ₽",
        "old_price_text": None,
        "old_price_marked": True,
        "old_price_rub": 12999.0,
        "price_texts": {"other": ["8 999", "12 999"]},
    }
    assert prices_from_tile(tile) == (6569.0, 12999.0)


@pytest.mark.req("R-OLD-PRICE-BY-MARKER")
def test_an_unmarked_tile_with_a_null_old_price_text_keeps_the_rank_rule() -> None:
    # Citilink's card emits old_price_text as null when its selector misses and
    # relies on the candidates for the struck price (test_card_extractor_dom).
    tile = {
        "price_text": "89 990 ₽",
        "old_price_text": None,
        "price_texts": {"other": ["105 990"]},
    }
    assert prices_from_tile(tile) == (89990.0, 105990.0)


# END_BLOCK_MARKER_PRICE_TESTS
