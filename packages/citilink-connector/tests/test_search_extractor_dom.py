"""Regression tests for the Citilink extractors on captured tile markup.

Citilink showed the opposite symptom to DNS: the title came back right and the
price came back ``None``. Root cause, verified live on 2026-07-28 through a
rendering browser: **Citilink renders the ₽ glyph in its own element, sibling to
the digits** — ``<span>59 990</span><span>₽</span>``. The old extractor filtered
``innerText`` lines through ``/руб|₽/``, so no line ever carried both and every
price parsed as null.

``fixtures/search_grid.html`` is the tile markup as served, captured from
https://www.citilink.ru/catalog/noutbuki/. Only icon ``<svg>`` path data and the
repeated carousel ``<img>`` tags were elided; every attribute the extractor reads
is verbatim. Three real traps live in it:

  1. the FIRST product anchor in a tile is an **empty overlay link**, so anchor
     text can never be the title source;
  2. the currency glyph is a separate element from the digits;
  3. the strikethrough price precedes the current price in DOM order, with a
     "- 10%" badge and a "в 1356 пунктов" delivery count nearby — three numbers
     that are not the price.

The capture also revealed the fix that matters most. Citilink's class names are
build-hashed (``app-catalog-51bw0j-…``) and therefore unusable across deploys, but
the site publishes a stable ``data-meta-*`` contract for its own analytics:
``data-meta-name="Snippet__title" / "Snippet__price" / "Snippet__old-price"``, and
``data-meta-price="59990"`` — an exact machine-readable amount that needs no
parsing and cannot be confused with an instalment or a badge. The extractor keys
on those and keeps the display strings as a fallback.

Ground truth displayed on the page at capture time:

    Ноутбук Acer Gadget ERBook Air …   59 990 ₽   was 66 990
    Ноутбук iRU Strato 16ALMR …        55 170 ₽   no strikethrough
"""

from __future__ import annotations

from pathlib import Path

import pytest
from citilink_connector import server
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid.html"

# (product id, current price, strikethrough) exactly as the page displayed them.
EXPECTED = [
    ("noutbuk-acer-gadget-erbook-air-ryzen-7-8745hs-16gb-ssd512gb-780m-14-ip-2147976", 59990.0, 66990.0),
    ("noutbuk-iru-strato-16almr-16-ips-intel-core-i5-12450h-8-yadern-16gb-2142315", 55170.0, None),
]

# Numbers that live in these tiles but are not prices. A regression that promotes
# any of them is the dangerous kind: it validates and looks plausible.
# Numbers that live in these tiles but are not prices: the discount badge (-10%),
# the delivery point count, the delivery estimate in days, the rating and the
# opinion count. Promoting any of them is the dangerous kind of regression — it
# validates and looks plausible.
NON_PRICE_NUMBERS = {10.0, 1356.0, 8.0, 4.9}


def _items() -> list[dict]:
    payload = _extract(server._SEARCH_EXTRACT_JS)
    return payload["items"]


def _extract(js_source: str) -> dict:
    """Run the connector's real extractor over the captured markup.

    Skips rather than fails when jsdom is absent: the pure-Python assertions
    below still cover the price-selection logic on a machine without Node.
    """
    try:
        return run_extractor(js_source, FIXTURE, page_url="https://www.citilink.ru/catalog/noutbuki/")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_price_is_found_when_the_currency_glyph_is_a_separate_element() -> None:
    """The exact reason Citilink prices were null. This is the core regression."""
    for tile, (product_id, expected_price, _) in zip(_items(), EXPECTED, strict=True):
        assert tile["product_id"] == product_id
        price, _old = prices_from_tile(tile)
        assert price == expected_price, f"expected {expected_price} for {product_id}, got {price}"


def test_strikethrough_before_the_current_price_is_read_as_the_old_price() -> None:
    """DOM order here is old price, then discount badge, then current price.

    Anything that simply takes the first price-shaped number in the tile reports
    66 990 as the price. The current price is identified by being attached to the
    currency glyph, not by position.
    """
    for tile, (_pid, expected_price, expected_old) in zip(_items(), EXPECTED, strict=True):
        price, old = prices_from_tile(tile)
        assert price == expected_price
        assert old == expected_old


def test_badges_bonuses_and_delivery_counts_are_never_prices() -> None:
    """ "- 10%", "+ 1 655 бонусов", "в 1356 пунктов", "(от 8 дней)" are not prices."""
    for tile in _items():
        price, old = prices_from_tile(tile)
        assert price not in NON_PRICE_NUMBERS, f"a non-price number leaked in as the price: {price}"
        assert old not in NON_PRICE_NUMBERS, f"a non-price number leaked in as the old price: {old}"


def test_title_comes_from_a_text_bearing_anchor_not_the_empty_overlay() -> None:
    """The first product anchor per tile is an empty overlay link."""
    for tile in _items():
        assert tile["title"], "title empty — extraction fell back to the overlay anchor"
        assert "Ноутбук" in tile["title"]


def test_search_items_carry_the_wire_shape() -> None:
    items = [server._search_item_from_tile(t) for t in _items()]
    for got, (product_id, expected_price, expected_old) in zip(items, EXPECTED, strict=True):
        assert got.product_id == product_id
        assert got.price_rub == expected_price
        assert got.old_price_rub == expected_old


# ---------------------------------------------------------------------------
# Pure-Python half: always runs, no Node needed.
# ---------------------------------------------------------------------------


def test_glyph_attached_candidate_wins_over_a_bare_number() -> None:
    """Mirrors Citilink's shape: bare old price, glyph-attached current price."""
    price, old = prices_from_tile({"price_texts": {"attached": ["59 990₽", "59 990"], "other": ["66 990"]}})
    assert price == 59990.0
    assert old == 66990.0


def test_a_tile_with_no_glyph_attached_candidate_reports_no_price() -> None:
    """Fail honest: a bare number alone is not evidence of a price.

    Promoting it would be indistinguishable from a correct read downstream, which
    is exactly the failure this audit was called on to remove.
    """
    price, old = prices_from_tile({"price_texts": {"attached": [], "other": ["66 990"]}})
    assert price is None
    assert old is None


def test_legacy_flat_candidate_list_is_treated_as_weak() -> None:
    """A payload cached before the split carries no glyph information."""
    price, _old = prices_from_tile({"price_texts": ["59 990"]})
    assert price is None


def test_legacy_numeric_payload_still_maps() -> None:
    price, old = prices_from_tile({"price_rub": 59990.0, "old_price_rub": 66990.0})
    assert price == 59990.0
    assert old == 66990.0


def test_exact_meta_price_attribute_is_preferred() -> None:
    """``data-meta-price`` is the site's own numeric amount — no parsing, no ambiguity."""
    tiles = _items()
    assert [t["price_meta"] for t in tiles] == ["59990", "55170"]


def test_stable_data_meta_hooks_are_what_the_extractor_keys_on() -> None:
    """Guard against a regression back onto build-hashed class names.

    ``app-catalog-51bw0j-…`` changes whenever Citilink ships a build; selecting on
    it would look fine in review and break silently in production.
    """
    code = "\n".join(line.split("//")[0] for line in server._SEARCH_EXTRACT_JS.splitlines())
    assert "Snippet__title" in code
    assert "Snippet__price" in code
    assert "data-meta-price" in code
    assert "app-catalog-" not in code, "extractor is selecting on a build-hashed class name"
