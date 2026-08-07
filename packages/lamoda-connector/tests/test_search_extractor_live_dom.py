"""The Lamoda search extractor against a LIVE captured grid.

Companion to ``test_search_extractor_dom.py`` (the hand-modeled fixture): on
2026-08-07 the operator's Chrome captured the real search page (provenance in
``fixtures/search_grid_live.provenance.json``) and the capture immediately
reproduced two defects the modeled grid could not show:

1. Lamoda renders the discount badge ("−49%") INSIDE the image anchor. The
   extractor read the anchor's own text first, so every badge-bearing tile
   reported its discount as the product title.
2. The tile's size grid contributes concatenated digit blobs to the weak price
   candidates; ``max(above)`` then promotes one to the strikethrough, and
   "3535,53636,53737,538" parses to a 3.5e16-rouble old price.

The DOM half needs Node with jsdom and skips without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lamoda_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid_live.html"

# (sku, title, price, old price) exactly as displayed on the captured page.
EXPECTED = [
    ("rtlaey634001", "Кеды HANDBALL SPEZIAL J", 6569.0, 12999.0),
    ("mp002xb085kj", "Кроссовки SP EVAN SL B", 2671.0, 4110.0),
    ("rtlaeq981601", "Кроссовки RBK PREMIER ROAD CONTROL", 6159.0, 13999.0),
]


def _items() -> list[dict]:
    try:
        payload = run_extractor(
            server._SEARCH_EXTRACT_JS,
            FIXTURE,
            page_url="https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8",
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    return payload["items"]


def test_live_titles_are_product_names_not_discount_badges() -> None:
    """The badge lives inside the image anchor; the title must not."""
    items = _items()
    assert len(items) == 3, f"expected the three captured tiles, got {len(items)}"
    for got, (_sku, title, _price, _old) in zip(items, EXPECTED, strict=True):
        assert got["sku"] == _sku
        assert got["title"] == title
        assert "%" not in got["title"], "the discount badge leaked into the title"


def test_live_prices_survive_the_tile_noise() -> None:
    """Sizes, rating and the promo timer all live inside the tile. The old
    price must be the real strikethrough, never the concatenated size blob
    (coerce_price('3535,53636,53737,538') parses to 3.5e16)."""
    for tile, (_sku, _title, price, old) in zip(_items(), EXPECTED, strict=True):
        got_price, got_old = server.prices_from_tile(tile)
        assert got_price == price
        assert got_old == old, f"old price {got_old} is not the strikethrough {old}"


def test_live_items_carry_the_wire_shape() -> None:
    """Extractor JS -> Python mapping -> LamodaSearchItemOut over the live DOM."""
    items = [server._search_item_from_tile(t) for t in _items()]
    for got, (sku, title, price, old) in zip(items, EXPECTED, strict=True):
        assert got.sku == sku
        assert got.title == title
        assert got.price_rub == price
        assert got.old_price_rub == old
        assert got.url and "/p/" in got.url
