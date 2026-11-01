"""R-ITEM-HONESTY wiring tests for the Taobao connector (audit 2026-11-01).

Commit fe7efcc routed taobao_search through ``mcp_core.resilience.validate_items``
(``price_key="price_cny"``, ``absent_price="no_prices_on_page"``) and taobao_card
through ``resilience.validate_offer`` (``price_key="price_cny"``), so an offer that
lost its title or its price is named in ``meta.warnings`` and flips ``healthy``.
These tests prove the wiring through the public tool entries, CDP rendering
monkeypatched out exactly like ``test_server.py`` — no Chrome, no network.
"""

from __future__ import annotations

import pytest
from taobao_connector import server

# ---------------------------------------------------------------- fixtures ----


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()


def _patch_render(monkeypatch, payload):
    async def fake_render(url, extract_js, wait_ms, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render", fake_render)


# A search page whose every tile lost BOTH its title and its price, while every
# key family the shape canary reads stays PRESENT (a null title keeps the
# ``items[].title`` family alive; an empty attached-price list and a null
# ``price_cny`` keep the price family alive) — so the pre-existing shape_drift
# check stays quiet and only the offer invariants speak. The structural
# wall-marker fields mirror a healthy logged-out results page (133 anchors),
# so the anchor-count gate keeps the 登录 wording out of the wall branch.
_TITLELESS_PRICELESS_PAGE = {
    "title": "手机-淘宝搜索",
    "anchors_total": 133,
    "login_anchors": 2,
    "body_snippet": "亲，请登录 免费注册 手机-淘宝搜索",
    "grid_loading": False,
    "items": [
        {
            "item_id": "123456789012",
            "title": None,
            "price_texts": {"attached": [], "other": []},
            "price_cny": None,
            "shop_name": "苹果官方旗舰店",
            "location": None,
            "sales": None,
            "url": "https://item.taobao.com/item.htm?id=123456789012",
        },
        {
            "item_id": "123456789013",
            "title": None,
            "price_texts": {"attached": [], "other": []},
            "price_cny": None,
            "shop_name": None,
            "location": None,
            "sales": None,
            "url": "https://item.taobao.com/item.htm?id=123456789013",
        },
    ],
}

# A healthy page in the current extractor's shape: every tile titled, every
# price glyph-attached, structural wall fields of a logged-out results page.
_PRICED_TITLED_PAGE = {
    "title": "手机-淘宝搜索",
    "anchors_total": 133,
    "login_anchors": 2,
    "body_snippet": "亲，请登录 免费注册 手机-淘宝搜索 Apple iPhone 16 Pro Max 全网通",
    "grid_loading": False,
    "items": [
        {
            "item_id": "123456789012",
            "title": "Apple iPhone 16 Pro Max 全网通",
            "price_texts": {"attached": ["¥9999"], "other": []},
            "shop_name": "苹果官方旗舰店",
            "location": "上海",
            "sales": "2000+人付款",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
        },
        {
            "item_id": "123456789013",
            "title": "二手手机 便宜出",
            "price_texts": {"attached": ["¥12.5"], "other": []},
            "shop_name": "二手数码小店",
            "location": None,
            "sales": "1人付款",
            "url": "https://item.taobao.com/item.htm?id=123456789013",
        },
    ],
}


# -------------------------------------------------------------- taobao_search ----


async def test_search_flags_tiles_that_lost_their_prices_and_titles(monkeypatch):
    """A page of titleless, priceless tiles is named drift the agent can read.

    validate_items aggregates both invariants over the mapped items: not one
    item carries a parseable CNY price, so ``no_prices_on_page`` fires, and
    2 of 2 tiles lost their titles, so ``title_missing: 2/2`` fires next to it.
    On the pre-HEAD code this second alarm did not exist — taobao_search had
    only the hand-rolled guard ``priceless = sum(1 for it in items if
    it.price_cny is None); if priceless == len(items):
    warnings.append("no_prices_on_page")`` — which is IDENTICAL for an
    all-priceless page. So of the assertions below, only the ``title_missing``
    one fails on HEAD~1: the old warnings list was ``["no_prices_on_page"]``
    and healthy was already False. The tiles keep the call from raising
    parser_drift, which is zero-items-only.
    """
    _patch_render(monkeypatch, _TITLELESS_PRICELESS_PAGE)

    result = await server.taobao_search("ноутбук")

    assert result.count == 2
    assert result.meta.healthy is False
    assert "no_prices_on_page" in result.meta.warnings
    assert any(w.startswith("title_missing: 2/2") for w in result.meta.warnings), result.meta.warnings
    assert result.items[0].price_cny is None


async def test_search_a_priced_page_with_titles_stays_healthy(monkeypatch):
    """The guard: a page where every tile kept its title and glyph-attached
    price answers healthy with no invariant warning — the wiring flags drift,
    it does not invent it."""
    _patch_render(monkeypatch, _PRICED_TITLED_PAGE)

    result = await server.taobao_search("ноутбук")

    assert result.meta.healthy is True
    assert "no_prices_on_page" not in result.meta.warnings
    assert not any(w.startswith("title_missing") for w in result.meta.warnings)


# --------------------------------------------------------------- taobao_card ----


async def test_card_warns_when_the_item_lost_its_title(monkeypatch):
    """A card that parsed a price but no title is drift, not a healthy offer.

    The card keeps its CNY price, so the neither-title-nor-price parser_drift
    raise cannot fire (the raise needs BOTH None); validate_offer then reports
    the missing title in meta.warnings and healthy flips to False. On the
    pre-HEAD code taobao_card attached meta with only its hand-built
    ``card_warnings`` — ``attach_meta(result.model_dump(by_alias=True,
    exclude={"meta"}), card_warnings, source="taobao_card")`` — and for a card
    whose only drift is the title that list is empty, so BOTH assertions below
    fail on HEAD~1: warnings was ``[]`` and healthy was True.
    """
    payload = {
        "title": None,
        "price_cny": 199.0,
        "shop_name": "苹果官方旗舰店",
        "sales": "月销2000+",
        "description_images": 14,
        "page_title": "Apple iPhone 16 Pro Max-淘宝网",
    }
    _patch_render(monkeypatch, payload)

    result = await server.taobao_card("123456789012")

    assert result.price_cny == 199.0
    assert result.meta.healthy is False
    assert any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings
