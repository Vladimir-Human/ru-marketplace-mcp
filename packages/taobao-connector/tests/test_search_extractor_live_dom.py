"""The Taobao search extractor against a LIVE captured grid.

Companion to ``test_search_extractor_dom.py`` (the hand-modeled fixture): on
2026-08-07 the operator logged into Taobao in their Chrome (CDP) and captured
the rendered search page (provenance in
``fixtures/search_grid_live.provenance.json``). The capture reproduced three
defects the modeled grid could not show:

1. The tile anchor wraps the WHOLE tile, so reading the anchor text first
   glued price, sales and shop into the title.
2. The modern layout splits the price into ``unit`` (¥) + ``priceInt`` +
   ``priceFloat`` nodes; the whole price block as one text blob glues the
   sales count ("200+人付款") onto the digits, which coerce_price rejects as
   ambiguous — so every priced tile read as no price at all.
3. The legacy line scan for shop/sales/location splits on newlines, and the
   rendered tile text carries none, so all three came back null or glued.

Note: tiles that link to detail.tmall.com instead of item.taobao.com stay out
of scope by design (the connector speaks item.taobao.com); the fixture holds
three extractable tiles. The DOM half needs Node with jsdom and skips without
it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_core.domtest import JsdomUnavailable, run_extractor
from taobao_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid_live.html"

# Values read off the live page when the fixture was captured.
EXPECTED = [
    {
        "item_id": "1048008062319",
        "title": "大码美式设计感蓝色扎染拼接格纹吊带连衣裙女胖妹妹收腰显瘦长裙",
        "price_cny": 66.8,
        "shop_name": "小毒家大码女装",
        "sales": "200+人付款",
        "location": "广东 广州",
    },
    {
        "item_id": "1060336448920",
        "title": "薄荷绿挂脖连衣裙女夏2026新款度假风雪纺荷叶边气质收腰显瘦长裙",
        "price_cny": 112.0,
        "shop_name": "黑色玫瑰 rose",
        "sales": "100+人付款",
        "location": "广东 广州",
    },
    {
        "item_id": "709599481146",
        "title": "性感气质收腰褶皱包臀短裙女夏季新款纯欲辣妹V领紧身短袖连衣裙",
        "price_cny": 16.9,
        "shop_name": "迪丽热九衣橱",
        "sales": "1000+人付款",
        "location": "广东 揭阳",
    },
]


def _items() -> list[dict]:
    try:
        payload = run_extractor(
            server._SEARCH_EXTRACT_JS,
            FIXTURE,
            page_url="https://s.taobao.com/search?q=%E8%BF%9E%E8%A1%A3%E8%A3%99",
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    return payload["items"]


def test_live_titles_are_the_product_name_not_the_whole_tile() -> None:
    items = _items()
    assert len(items) == 3, f"expected the three captured tiles, got {len(items)}"
    for got, expected in zip(items, EXPECTED, strict=True):
        assert got["item_id"] == expected["item_id"]
        assert got["title"] == expected["title"]
        assert "¥" not in got["title"], "the price leaked into the title"


def test_live_prices_survive_the_split_price_layout() -> None:
    """unit + priceInt + priceFloat must reassemble into the price the page
    displayed; the sales count glued next to them must not poison the read."""
    for tile, expected in zip(_items(), EXPECTED, strict=True):
        price, old = server.prices_from_tile(tile)
        assert price == expected["price_cny"]
        assert old is None, "taobao search tiles carry no strikethrough"


def test_live_items_carry_shop_sales_location() -> None:
    """Extractor JS -> Python mapping -> TaobaoSearchItemOut over the live DOM."""
    items = [server._search_item_from_tile(t) for t in _items()]
    for got, expected in zip(items, EXPECTED, strict=True):
        assert got.item_id == expected["item_id"]
        assert got.title == expected["title"]
        assert got.price_cny == expected["price_cny"]
        assert got.shop_name == expected["shop_name"]
        assert got.sales == expected["sales"]
        assert got.location == expected["location"]
        assert got.url and "item.taobao.com" in got.url
