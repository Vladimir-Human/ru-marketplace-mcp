"""Offline tests for the Taobao connector.

CDP rendering is monkeypatched out: the suite runs with no Chrome and no
network. Fixtures mirror what the in-page extractor returns from a rendered
search/card — including the login-wall and gone-item shapes that must never be
read as data.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from taobao_connector import server

# ---------------------------------------------------------------- fixtures ----

SEARCH_EXTRACTED = {
    "title": "手机-淘宝搜索",
    "items": [
        {
            "item_id": "123456789012",
            "title": "Apple iPhone 16 Pro Max 全网通",
            "price_cny": 9999.0,
            "shop_name": "苹果官方旗舰店",
            "location": None,
            "sales": "2000+人付款",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
        },
        {
            "item_id": "123456789013",
            "title": "二手手机 便宜出",
            "price_cny": None,
            "shop_name": None,
            "location": None,
            "sales": None,
            "url": "https://item.taobao.com/item.htm?id=123456789013",
        },
    ],
}

CARD_EXTRACTED = {
    "title": "Apple iPhone 16 Pro Max 全网通5G手机",
    "price_cny": 9999.0,
    "shop_name": "苹果官方旗舰店",
    "sales": "月销2000+",
    "description_images": 14,
    "page_title": "Apple iPhone 16 Pro Max-淘宝网",
}

LOGIN_WALL = {"title": "登录-淘宝网", "items": []}
GONE_ITEM = {
    "title": None,
    "price_cny": None,
    "shop_name": None,
    "sales": None,
    "description_images": 0,
    "page_title": "很抱歉，您查看的商品不存在",
}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()


def _patch_render(monkeypatch, payload):
    async def fake_render(url, extract_js, wait_ms, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render", fake_render)


# -------------------------------------------------------------- taobao_search ----


async def test_search_parses_items(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.taobao_search("手机")

    assert result.status == "success"
    assert result.count == 2
    first = result.items[0]
    assert first.item_id == "123456789012"
    assert first.price_cny == 9999.0
    assert first.sales == "2000+人付款"
    assert result.tier_used == "cdp"


async def test_search_a_hidden_price_is_none_never_zero(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.taobao_search("手机")

    priceless = result.items[1]
    assert priceless.price_cny is None
    assert priceless.price_cny != 0


async def test_search_warns_when_no_item_has_a_price(monkeypatch):
    payload = {"title": "x", "items": [dict(SEARCH_EXTRACTED["items"][1])]}
    _patch_render(monkeypatch, payload)

    result = await server.taobao_search("手机")

    assert "no_prices_on_page" in result.meta.warnings


async def test_search_maps_a_login_wall_to_transport_down(monkeypatch):
    _patch_render(monkeypatch, LOGIN_WALL)

    with pytest.raises(ToolError) as excinfo:
        await server.taobao_search("手机")

    assert "login" in str(excinfo.value).lower() or "登录" in str(excinfo.value)


async def test_search_maps_zero_items_to_parser_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "手机-淘宝搜索", "items": []})

    with pytest.raises(ToolError):
        await server.taobao_search("手机")


# ---------------------------------------------------------------- taobao_card ----


async def test_card_parses_the_item(monkeypatch):
    _patch_render(monkeypatch, CARD_EXTRACTED)

    result = await server.taobao_card("123456789012")

    assert result.item_id == "123456789012"
    assert result.price_cny == 9999.0
    assert result.description_images == 14
    assert result.url == "https://item.taobao.com/item.htm?id=123456789012"


async def test_card_accepts_a_full_url(monkeypatch):
    _patch_render(monkeypatch, CARD_EXTRACTED)

    result = await server.taobao_card("https://item.taobao.com/item.htm?spm=a21n57&id=123456789012&ns=1")

    assert result.item_id == "123456789012"


@pytest.mark.parametrize("bad", ["", "not-an-id", "12345", "https://example.com/?id=123456789012"])
async def test_card_rejects_input_without_an_item_id(bad):
    with pytest.raises(ToolError):
        await server.taobao_card(bad)


async def test_card_maps_a_gone_item_to_not_found(monkeypatch):
    _patch_render(monkeypatch, GONE_ITEM)

    with pytest.raises(ToolError):
        await server.taobao_card("123456789012")


async def test_card_flags_drift_when_neither_title_nor_price(monkeypatch):
    payload = {
        "title": None,
        "price_cny": None,
        "shop_name": None,
        "sales": None,
        "description_images": 0,
        "page_title": "iPhone-淘宝网",
    }
    _patch_render(monkeypatch, payload)

    with pytest.raises(ToolError):
        await server.taobao_card("123456789012")


async def test_card_survives_a_drifted_description_images_with_a_warning(monkeypatch):
    """A decorative count drifting to a non-number must not kill an otherwise
    readable card — and it is not a transport event, so it must not surface as
    TransportDownError either. The count degrades to 0 and the drift is named
    in meta.warnings, exactly like the other soft-drift canaries."""
    payload = dict(CARD_EXTRACTED, description_images="about five")
    _patch_render(monkeypatch, payload)

    result = await server.taobao_card("123456789012")

    assert result.description_images == 0
    assert result.title == CARD_EXTRACTED["title"]
    assert result.price_cny == CARD_EXTRACTED["price_cny"]
    assert any("description_images" in w for w in result.meta.warnings)
    assert result.meta.healthy is False


# ----------------------------------------------------------- taobao_selfcheck ----


async def test_selfcheck_healthy_when_items_extract(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.taobao_selfcheck()

    assert result.status == "success"
    assert result.healthy is True


async def test_selfcheck_login_wall_is_inconclusive_never_drift(monkeypatch):
    _patch_render(monkeypatch, LOGIN_WALL)

    result = await server.taobao_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None


async def test_selfcheck_zero_items_is_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "手机-淘宝搜索", "items": []})

    result = await server.taobao_selfcheck()

    assert result.status == "drift_detected"
    assert result.healthy is False


async def test_selfcheck_cries_shape_drift_when_the_price_family_vanishes(monkeypatch):
    """Items still extract, but every key the parser binds a price through is
    gone — that is structural drift, and it must be said out loud with the
    missing family named."""
    _patch_render(
        monkeypatch,
        {
            "title": "手机-淘宝搜索",
            "items": [
                {
                    "item_id": "123456789012",
                    "title": "Apple iPhone 16 Pro Max 全网通",
                    "url": "https://item.taobao.com/item.htm?id=123456789012",
                }
            ],
        },
    )

    result = await server.taobao_selfcheck()

    assert result.status == "drift_detected"
    search = result.checks["search"]
    assert search.state == "drift"
    assert search.reason == "shape_drift"
    assert any("price" in note for note in search.notes)


# ------------------------------------------------------------------- helpers ----


def test_extract_item_id_handles_every_accepted_shape():
    assert server._extract_item_id("123456789012") == "123456789012"
    assert server._extract_item_id("https://item.taobao.com/item.htm?id=123456789012&spm=x") == "123456789012"
    assert server._extract_item_id("手机") is None
    assert server._extract_item_id("12345") is None


# ------------------------------------------------------------------- SSRF guard ----
#
# taobao_card renders in the operator's own logged-in Chrome. The navigated URL
# is always rebuilt from ITEM_BASE, so an off-host argument cannot steer the
# browser — but it must still be refused rather than quietly mined for an id.

_OFF_HOST_INPUTS = [
    "https://evil.example/item.htm?id=123456789012",
    "https://taobao.com.evil.example/item.htm?id=123456789012",
    "//evil.example/item.htm?id=123456789012",
    "file:///etc/passwd?id=123456789012",
    "javascript:fetch('/item.htm?id=123456789012')",
]


@pytest.mark.parametrize("hostile", _OFF_HOST_INPUTS)
def test_extract_item_id_refuses_off_host_input(hostile):
    assert server._extract_item_id(hostile) is None


def test_a_real_taobao_url_still_yields_its_id():
    assert server._extract_item_id("https://item.taobao.com/item.htm?id=123456789012") == "123456789012"
    assert server._extract_item_id("https://detail.tmall.taobao.com/item.htm?spm=x&id=123456789012") == "123456789012"


def test_a_bare_numeric_id_is_accepted():
    assert server._extract_item_id("123456789012") == "123456789012"
    assert server._extract_item_id("12345") is None, "too short to be an item id"


def test_the_card_navigates_a_rebuilt_item_base_url():
    """Whatever came in, the URL we open is ours."""
    item_id = server._extract_item_id("https://item.taobao.com/item.htm?redirect=evil&id=123456789012")

    assert item_id == "123456789012"
    assert f"{server.ITEM_BASE}?id={item_id}" == "https://item.taobao.com/item.htm?id=123456789012"
