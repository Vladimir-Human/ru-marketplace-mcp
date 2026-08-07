"""Regression tests for the Lamoda search extractor on a modeled fixture.

Lamoda is the last CDP search source without a jsdom fixture. This closes the
same hole DNS and Citilink already plug: the extractor JS runs against the
modelled grid markup, not against a mocked render call, so a broken selector
reads as a failing assertion instead of a silent ``price_rub: null``. The
fixture is hand-modelled, not a capture — the audit of 2026-08-06 found no
provenance for it, and lamoda.ru answers a datacenter fetch with a captcha, so
a live capture is an operator task.

``fixtures/search_grid.html`` models a rendered search grid: two cards, each
with an empty image/overlay link first and a text-bearing product link next, a
current price in its own node, and one card carrying a strikethrough old price,
a ``-30%`` promo badge, a rating and a review-count line — all numbers that are
not prices.

Ground truth on the fixture: Nike 5 990 ₽ (was 7 990), Adidas 8 490 ₽.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lamoda_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid.html"

# (sku, title, current price, strikethrough) exactly as the page displays them.
# SKUs come back lowercase because they are read from the URL, where Lamoda
# renders them lowercased; the GraphQL path normalises to uppercase separately.
EXPECTED = [
    ("mp002xm1rmm3", "Кроссовки Nike Air Max 270", 5990.0, 7990.0),
    ("mp002xm7abc2", "Кроссовки Adidas Ultraboost", 8490.0, None),
]


# Numbers that live in these cards but are not prices: the promo badge, the
# rating and the review count. A regression that promotes any of them is the
# dangerous kind — it validates and looks plausible.
NON_PRICE_NUMBERS = {30.0, 4.9, 128.0}


def _items() -> list[dict]:
    payload = _extract(server._SEARCH_EXTRACT_JS)
    return payload["items"]


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(
            js_source,
            FIXTURE,
            page_url="https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8",
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_search_extractor_reads_the_real_grid() -> None:
    """Both cards are found, deduplicated by SKU, with their names intact."""
    items = _items()
    assert len(items) == 2, f"expected both cards, got {len(items)}"
    assert len({it["sku"] for it in items}) == 2, "a SKU appeared twice"
    for got, (sku, title, _price, _old) in zip(items, EXPECTED, strict=True):
        assert got["sku"] == sku
        assert got["title"] == title


def test_prices_are_read_from_the_price_node_not_the_promo_badge() -> None:
    """-30%, 4.9 and «отзывов: 128» must never become the price."""
    for tile, (_sku, _title, expected_price, _old) in zip(_items(), EXPECTED, strict=True):
        price, _old_price = server.prices_from_tile(tile)
        assert price == expected_price, f"expected {expected_price}, got {price}"
        assert price not in NON_PRICE_NUMBERS


def test_strikethrough_is_reported_as_the_old_price() -> None:
    for tile, (_sku, _title, expected_price, expected_old) in zip(_items(), EXPECTED, strict=True):
        price, old = server.prices_from_tile(tile)
        assert price == expected_price
        assert old == expected_old


def test_items_carry_the_wire_shape() -> None:
    """Extractor JS -> Python mapping -> LamodaSearchItemOut wire shape."""
    items = [server._search_item_from_tile(t) for t in _items()]
    for got, (sku, title, expected_price, expected_old) in zip(items, EXPECTED, strict=True):
        assert got.sku == sku
        assert got.title == title
        assert got.price_rub == expected_price
        assert got.old_price_rub == expected_old
        assert got.url and "/p/" in got.url


def test_the_extractor_uses_shared_helpers_not_legacy_heuristics() -> None:
    """Guard against a regression back to closest()/innerText/parseFloat/Math.min."""
    code = "\n".join(line.split("//")[0] for line in server._SEARCH_EXTRACT_JS.splitlines())
    assert ".closest(" not in code, "tile resolution went back to closest()"
    assert "innerText" not in code, "extractor went back to innerText"
    assert "parseFloat" not in code, "price parsing went back to parseFloat"
    assert "Math.min" not in code, "price picking went back to Math.min"
    assert "tileRootFor" in code, "shared tileRootFor vanished from the extractor"
    assert "priceTextsIn" in code, "shared priceTextsIn vanished from the extractor"
