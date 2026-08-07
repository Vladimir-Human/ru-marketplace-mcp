"""Regression tests for the Taobao search extractor on a modeled fixture.

Taobao's grids are client-side React rendered over the signed mtop API; the
extractor was the only layer of the connector no offline test touched, and it
still carried the July-2026 legacy heuristics that DNS and Citilink abandoned:
``closest()`` tile resolution, ``innerText`` line scans, ``parseFloat`` on
digit-concatenated text and a DIY yuan regex. ``fixtures/search_grid.html`` is
a hand model of the tile markup shape Taobao serves (not a capture — a real
grid is client-rendered, so capturing one needs a browser and is an operator
task), trimmed to two cards: one with a real
yuan-glued price (``999¥``), one with a hidden price (``面议``, "price on
request") that must read as None — never 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_core.domtest import JsdomUnavailable, run_extractor
from taobao_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid.html"

# Ground truth on the fixture: first card priced 999 yuan, second card hides
# its price.
EXPECTED = [
    {
        "item_id": "123456789012",
        "title": "Apple iPhone 16 Pro Max 全网通5G手机 官方标配",
        "price_cny": 999.0,
        "shop_name": "苹果官方旗舰店",
        "sales": "2000+人付款",
    },
    {
        "item_id": "123456789013",
        "title": "二手手机 便宜出",
        "price_cny": None,
        "shop_name": "二手优品店",
        "sales": "约售 12 件",
    },
]


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(js_source, FIXTURE, page_url="https://s.taobao.com/search?q=%E6%89%8B%E6%9C%BA")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_search_extractor_reads_the_real_grid() -> None:
    """Both cards are found, deduplicated by item id, with their data intact."""
    payload = _extract(server._SEARCH_EXTRACT_JS)
    items = payload["items"]
    assert len(items) == 2, f"expected both cards, got {len(items)}"
    assert len({it["item_id"] for it in items}) == 2, "an item id appeared twice"

    for got, expected in zip(items, EXPECTED, strict=True):
        assert got["item_id"] == expected["item_id"]
        assert got["title"] == expected["title"]


def test_yuan_glued_price_is_read_from_the_card() -> None:
    """999¥ is glyph-attached, so priceTextsIn keeps it as the price candidate."""
    payload = _extract(server._SEARCH_EXTRACT_JS)
    first = payload["items"][0]
    assert first["price_texts"]["attached"] == ["999¥"]
    price, _old = server.prices_from_tile(first)
    assert price == 999.0


def test_a_hidden_price_is_none_never_zero() -> None:
    """«面议» carries no digits and must not read as a price or as 0."""
    payload = _extract(server._SEARCH_EXTRACT_JS)
    second = payload["items"][1]
    assert second["price_texts"]["attached"] == []
    assert second["price_texts"]["other"] == []
    price, _old = server.prices_from_tile(second)
    assert price is None


def test_items_carry_the_wire_shape() -> None:
    """Extractor JS -> Python mapping -> TaobaoSearchItemOut wire shape."""
    payload = _extract(server._SEARCH_EXTRACT_JS)
    items = [server._search_item_from_tile(t) for t in payload["items"]]
    for got, expected in zip(items, EXPECTED, strict=True):
        assert got.item_id == expected["item_id"]
        assert got.title == expected["title"]
        assert got.price_cny == expected["price_cny"]
        assert got.shop_name == expected["shop_name"]
        assert got.sales == expected["sales"]
        assert got.url and got.url.startswith("https://item.taobao.com/item.htm?id=")


def test_the_extractor_uses_shared_helpers_not_legacy_heuristics() -> None:
    """Guard against a regression back to closest()/innerText/parseFloat/Math.min."""
    code = "\n".join(line.split("//")[0] for line in server._SEARCH_EXTRACT_JS.splitlines())
    assert ".closest(" not in code, "tile resolution went back to closest()"
    assert "innerText" not in code, "extractor went back to innerText"
    assert "parseFloat" not in code, "price parsing went back to parseFloat"
    assert "Math.min" not in code, "price picking went back to Math.min"
    assert "tileRootFor" in code, "shared tileRootFor vanished from the extractor"
    assert "priceTextsIn" in code, "shared priceTextsIn vanished from the extractor"
