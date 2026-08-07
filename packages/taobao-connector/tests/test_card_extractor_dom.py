"""Regression tests for the Taobao card extractor on a modeled fixture.

The search extractor got fixtures when it moved onto the shared helpers; the
card extractor ships the same body-scan approach (``priceTextsIn`` over
``document.body``, price picked in Python by ``mcp_core.dom.prices_from_tile``)
and had no DOM-level test at all — markup drift on the item page would have
surfaced only as a live failure on the operator's machine. The fixture is a
hand model of the item page shape, not a capture; capturing the client-rendered
real page is an operator task.

``fixtures/item_card.html`` models the rendered item page shape: title in
``h1``, the current price as a glyph+digits span pair (``¥7999``), a
strikethrough original (``¥8999``), and a coupon line (``券后价¥7899``) that is
glyph-attached and *below* the real price — the trap: it must become neither
the price nor the strikethrough. Shop name ends in ``店``, the sales line
matches ``人付款``, and three description images sit under a ``desc`` block.

Ground truth on the fixture: ¥7999 now, ¥8999 crossed out, coupon ¥7899
ignored, shop 苹果官方旗舰店, 2000+人付款, 3 description images.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor
from taobao_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "item_card.html"


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(js_source, FIXTURE, page_url="https://item.taobao.com/item.htm?id=123456789012")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_card_extractor_reads_the_item_page() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["title"] == "Apple iPhone 16 Pro Max 全网通5G手机 国行正品全新未拆封 官方标配"
    assert payload["shop_name"] == "苹果官方旗舰店"
    assert payload["sales"] == "2000+人付款"
    assert payload["description_images"] == 3


def test_yuan_price_and_strikethrough_are_read() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    price, old_price = prices_from_tile(payload)
    assert price == 7999.0
    assert old_price == 8999.0


def test_the_coupon_price_is_never_the_price_or_the_strikethrough() -> None:
    """«券后价¥7899» is glyph-attached but sits below the real price."""
    payload = _extract(server._CARD_EXTRACT_JS)
    price, old_price = prices_from_tile(payload)
    assert price != 7899.0
    assert old_price != 7899.0


def test_a_login_wall_title_is_detected_without_a_render() -> None:
    """The wall check is pure Python and must fire on both title fields."""
    assert server._login_wall({"page_title": "淘宝网 - 登录页面"}) is True
    assert server._login_wall({"title": "Sign in — Taobao Login"}) is True
    assert server._login_wall({"page_title": "Apple iPhone 16 Pro Max-淘宝网"}) is False


def test_the_card_extractor_uses_shared_helpers_not_legacy_heuristics() -> None:
    """Guard against a regression back to closest()/innerText/parseFloat/Math.min."""
    code = "\n".join(line.split("//")[0] for line in server._CARD_EXTRACT_JS.splitlines())
    assert ".closest(" not in code, "tile resolution went back to closest()"
    assert "innerText" not in code, "extractor went back to innerText"
    assert "parseFloat" not in code, "price parsing went back to parseFloat"
    assert "Math.min" not in code, "price picking went back to Math.min"
    assert "priceTextsIn" in code, "shared priceTextsIn vanished from the extractor"
