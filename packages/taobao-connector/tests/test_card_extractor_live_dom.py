"""The Taobao card extractor against a LIVE captured item page.

``fixtures/item_card_live.html`` is the real rendered page of item
1048008062319 captured 2026-08-07 through the operator's logged-in Chrome
(provenance in ``item_card_live.provenance.json``), trimmed only of
script/style noise. The capture caught the extractor red-handed on three
counts — the title read the header image-search placeholder, the split
symbol/text price spans were never assembled, and the sales/shop line scan
glued whole-page blobs — all fixed the same day; this test pins the fixed
behavior against the live markup so it cannot silently regress.

Needs Node with jsdom and skips without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor
from taobao_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "item_card_live.html"
PAGE_URL = "https://item.taobao.com/item.htm?id=1048008062319"

TITLE = "大码美式设计感蓝色扎染拼接格纹吊带连衣裙女胖妹妹收腰显瘦长裙"


def _extract() -> dict:
    try:
        return run_extractor(server._CARD_EXTRACT_JS, FIXTURE, page_url=PAGE_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_live_title_is_the_product_name_not_a_widget_placeholder() -> None:
    """The modern page has no h1; before the fix the generic title fallback
    read the header image-search box and answered «按图片搜索»."""
    payload = _extract()
    assert payload["title"] == TITLE
    assert "按图片搜索" not in (payload["title"] or "")


def test_live_price_is_assembled_from_the_split_spans() -> None:
    """￥83.6 is what the buyer pays (店铺优惠后); ￥95 is the before-discount
    figure (优惠前) and reads as the strikethrough. Before the fix both were
    missed — the digits-only spans failed the glyph-attachment check — and a
    priced card answered as no price."""
    payload = _extract()
    price, old_price = prices_from_tile(payload)
    assert price == 83.6
    assert old_price == 95.0


def test_live_shop_and_sales_are_the_short_nodes_not_body_blobs() -> None:
    """The rendered body text carries no newlines; an uncapped line scan
    glued the whole page into one value. The shop name is the dedicated span,
    the sales figure the short «已售 300+» text node."""
    payload = _extract()
    assert payload["shop_name"] == "小毒家大码女装"
    assert payload["sales"] == "已售 300+"
    for value in (payload["shop_name"], payload["sales"]):
        assert len(value) <= 40, f"glued body blob leaked into a field: {value[:60]}…"


def test_live_description_images_are_counted() -> None:
    assert _extract()["description_images"] == 20
