"""Regression tests for the DNS search/card extractors, on a real captured DOM.

Why this file exists: on 2026-07-28 ``dns_search`` returned 24 product links with
``title=None`` and ``price_rub=None`` against a grid that rendered fine. Every
existing DNS test passed, because they all monkeypatch ``_cdp_render`` and feed it
an already-extracted dict — so nothing in the suite ever executed the extractor
JS, and the defect lived precisely in the layer no test touched.

``fixtures/search_grid.html`` is the tile markup as captured from the live grid
(query «ноутбук», Moscow, 2026-07-28), trimmed only of repeated decorative nodes.
Ground truth observed on the page at capture time:

    HUAWEI MateBook D 16 2024 MCLF-X  58 999 ₽   no strikethrough   в 219 магазинах
    HONOR MagicBook X16 AMD 2025      51 999 ₽   was 54 999         в 186 магазинах

Both tiles also advertise an instalment ("от 5 751 ₽/ мес.", "от 5 069 ₽/ мес."),
which is the number the previous ``Math.min`` price heuristic would have picked.

The DOM half of this file needs Node with jsdom and skips without it; the
selection half is pure Python and always runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dns_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid.html"

# Values read off the live page when the fixture was captured.
EXPECTED = [
    {
        "product_id": "b7a1667f9b19ed20",
        "title": '16" Ноутбук HUAWEI MateBook D 16 2024 MCLF-X серый',
        "price_rub": 58999.0,
        "old_price_rub": None,
    },
    {
        "product_id": "cc2b8f1d44a0e311",
        "title": '16" Ноутбук HONOR MagicBook X16 AMD 2025 серый',
        "price_rub": 51999.0,
        "old_price_rub": 54999.0,
    },
]

# The instalment figures on those tiles. If one of these ever comes back as a
# price, the extractor has regressed to picking the cheapest number on the tile.
INSTALMENT_DECOYS = {5751.0, 5069.0}


def _extract(js_source: str) -> dict:
    """Run the connector's real extractor over the captured markup.

    Skips rather than fails when jsdom is absent: the pure-Python assertions
    below still cover the price-selection logic on a machine without Node.
    """
    try:
        return run_extractor(
            js_source, FIXTURE, page_url="https://www.dns-shop.ru/search/?q=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA"
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_search_extractor_reads_the_real_grid() -> None:
    """The extractor finds both tiles and reads the prices the page displayed."""
    payload = _extract(server._SEARCH_EXTRACT_JS)
    items = payload["items"]
    assert len(items) == 2, f"expected both tiles, got {len(items)}"

    for got, expected in zip(items, EXPECTED, strict=True):
        assert got["product_id"] == expected["product_id"]
        # The title must be the product name with the bracketed spec blob removed.
        assert got["title"] == expected["title"]
        assert "[" not in got["title"], "short-specs blob leaked into the title"
        assert got["price_text"], "no price text extracted from a tile that shows a price"


def test_search_extractor_ignores_the_instalment_line() -> None:
    """«от 5 751 ₽/ мес.» must never be mistaken for the price.

    This is the regression that matters most: a wrong-but-plausible price is worse
    than a missing one, because nothing downstream can tell it is wrong.
    """
    payload = _extract(server._SEARCH_EXTRACT_JS)
    for tile in payload["items"]:
        item = server._search_item_from_tile(tile)
        assert item.price_rub not in INSTALMENT_DECOYS, (
            f"extractor returned the monthly instalment {item.price_rub} as the price"
        )


def test_search_items_match_the_prices_on_the_page() -> None:
    """End to end over the real DOM: extractor JS -> Python mapping -> wire shape."""
    payload = _extract(server._SEARCH_EXTRACT_JS)
    items = [server._search_item_from_tile(t) for t in payload["items"]]
    for got, expected in zip(items, EXPECTED, strict=True):
        assert got.price_rub == expected["price_rub"]
        assert got.old_price_rub == expected["old_price_rub"]
        assert got.url and got.url.startswith("https://www.dns-shop.ru/product/")


def test_tile_root_is_not_the_image_anchor() -> None:
    """Guard the exact mechanism of the original bug.

    ``closest()`` tests the element itself first, and the DNS image link's own
    class (``catalog-product__image-link``) matches ``[class*="catalog-product"]``.
    Anything that resolves a tile by walking up from an anchor will land on a node
    with no text. Assert the extractor selects tiles directly instead.
    """
    js = server._SEARCH_EXTRACT_JS
    # Compare against code only: the comment above the extractor names closest()
    # precisely so the next reader knows why it is gone.
    code = "\n".join(line.split("//")[0] for line in js.splitlines())
    assert ".catalog-product'" in code or '.catalog-product"' in code, (
        "extractor no longer selects the tile root by its exact BEM block class"
    )
    assert ".closest(" not in code, "tile resolution went back to closest(), which matches the image anchor itself"


# ---------------------------------------------------------------------------
# Pure-Python half: the price/title mapping, always runs, no Node needed.
# ---------------------------------------------------------------------------


def test_price_text_shapes_seen_on_dns() -> None:
    """Real display strings from the live tiles, including the ones that broke."""
    item = server._search_item_from_tile(
        {
            "product_id": "b7a1667f9b19ed20",
            "title": '16" Ноутбук HUAWEI MateBook D 16 2024 MCLF-X серый',
            "price_text": "58 999 ₽",  # nbsp thousands separator, glyph attached
            "old_price_text": None,
            "url": "https://www.dns-shop.ru/product/b7a1667f9b19ed20/x/",
        }
    )
    assert item.price_rub == 58999.0
    assert item.old_price_rub is None


def test_old_price_without_a_currency_glyph_is_still_parsed() -> None:
    """`.product-buy__prev` renders "54 999" with no ₽ — the old filter missed it."""
    item = server._search_item_from_tile(
        {
            "product_id": "cc2b8f1d44a0e311",
            "title": "x",
            "price_text": "51 999 ₽",
            "old_price_text": "54 999",
            "url": "https://www.dns-shop.ru/product/cc2b8f1d44a0e311/x/",
        }
    )
    assert item.price_rub == 51999.0
    assert item.old_price_rub == 54999.0


def test_concatenated_price_blob_is_refused_not_guessed() -> None:
    """If the strikethrough ever glues onto the price, fail loud rather than invent.

    "58 999 ₽54 999" is ambiguous; coerce_price returns None instead of 5899954999.
    """
    item = server._search_item_from_tile({"product_id": "x", "title": "x", "price_text": "58 999 ₽54 999", "url": "u"})
    assert item.price_rub is None


def test_strikethrough_below_current_price_is_dropped() -> None:
    """An "old" price under the current one is a drifted read, not a discount."""
    item = server._search_item_from_tile(
        {"product_id": "x", "title": "x", "price_text": "58 999 ₽", "old_price_text": "1 000", "url": "u"}
    )
    assert item.price_rub == 58999.0
    assert item.old_price_rub is None


def test_legacy_numeric_payload_still_maps() -> None:
    """A cache entry written by the previous build must not start answering nulls."""
    item = server._search_item_from_tile(
        {
            "product_id": "x",
            "title": "y",
            "price_rub": 58999.0,
            "old_price_rub": 64999.0,
            "url": "u",
        }
    )
    assert item.price_rub == 58999.0
    assert item.old_price_rub == 64999.0


def test_zero_price_is_none_never_zero() -> None:
    """A dead listing must not rank as the cheapest option in compare_prices."""
    item = server._search_item_from_tile({"product_id": "x", "title": "y", "price_text": "0 ₽", "url": "u"})
    assert item.price_rub is None
