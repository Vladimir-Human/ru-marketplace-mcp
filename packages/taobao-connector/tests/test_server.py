"""Offline tests for the Taobao connector.

CDP rendering is monkeypatched out: the suite runs with no Chrome and no
network. Fixtures mirror what the in-page extractor returns from a rendered
search/card — including the login-wall and gone-item shapes that must never be
read as data.
"""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError
from taobao_connector import server

# ---------------------------------------------------------------- fixtures ----

SEARCH_EXTRACTED = {
    "title": "手机-淘宝搜索",
    # Current-extractor wall fields, in the shape a HEALTHY logged-out page
    # carries them (live 2026-09-10: 133 anchors including the header's
    # /member/login.jhtml + /member/new_register.jhtml, «亲，请登录» in the
    # chrome text). The anchor-count gate must keep this out of the wall
    # branch — a results page is never link-poor.
    "anchors_total": 133,
    "login_anchors": 2,
    "body_snippet": "亲，请登录 免费注册 手机-淘宝搜索 Apple iPhone 16 Pro Max 全网通",
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
# The 2026-09-10 wall variant, synthetic sibling of the live capture
# (fixtures/search_login_wall_live.html): EMPTY title, few links, login and
# register routes on them, login wording in the visible text. The title-only
# check could not see this; the structural markers must.
TITLE_LESS_WALL = {
    "title": "",
    "items": [],
    "anchors_total": 32,
    "login_anchors": 2,
    "body_snippet": "搜索 中国大陆 亲，请登录 免费注册 淘宝网首页 购物车",
}
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


# --------------------------------------------------------- login-wall markers ----
#
# Wall detection is multi-marker since the 2026-09-10 EMPTY-title variant: the
# extractor JS surfaces raw structure (anchors_total, login_anchors,
# body_snippet) and every verdict is made in Python, gated on the anchor count
# so a healthy page — which can carry a header login link and the word 登录 in
# its chrome — is never convicted.


def _error_payload(excinfo) -> dict:
    """raise_tool_error serializes a ConnectorError as JSON inside ToolError."""
    return json.loads(str(excinfo.value))


def test_login_wall_markers_title_branch():
    """The classic titled wall keeps firing on both title fields."""
    assert server._login_wall_markers({"title": "登录-淘宝网"}) == ("title",)
    assert server._login_wall_markers({"page_title": "Sign in — Taobao Login"}) == ("title",)
    assert server._login_wall({"title": "登录-淘宝网"}) is True


def test_login_wall_markers_read_the_document_title_per_payload_kind():
    """The title marker selects its field by payload kind: a ``page_title`` key
    identifies a CARD payload, whose ``title`` is the PRODUCT name. The
    live-reproduced false positive (review 2026-09-10): a fully rendered card of
    a product called «微信QQ账号电脑端登录…» was convicted because the marker
    read the product name — the DOCUMENT title (page_title) was clean."""
    # Card payload: login-worded PRODUCT name, clean document title — no marker.
    card = {"title": "微信QQ账号电脑端登录 全自动发货", "page_title": "淘宝网"}
    assert server._login_wall_markers(card) == ()
    assert server._login_wall(card) is False
    # Card payload whose DOCUMENT title is the login page — the classic wall,
    # fired through page_title.
    assert server._login_wall_markers({"title": None, "page_title": "登录-淘宝网"}) == ("title",)
    # Search payloads carry document.title in `title` and never emit
    # page_title — the titled-wall behavior is unchanged.
    assert server._login_wall_markers({"title": "登录-淘宝网", "items": []}) == ("title",)


def test_login_wall_markers_login_routes_branch():
    """Login/register anchors on a link-poor page — even with an empty title
    and no login wording anywhere (the iframe-rendered wall shape)."""
    payload = {"title": "", "anchors_total": 33, "login_anchors": 1, "body_snippet": ""}
    assert server._login_wall_markers(payload) == ("login_routes",)
    assert server._login_wall(payload) is True


def test_login_wall_markers_body_text_branch():
    """Login wording on a link-poor page with no login route in the DOM."""
    for snippet in ("亲，请登录后继续浏览", "Please LOG IN to continue", "Sign in to your account"):
        markers = server._login_wall_markers(
            {"title": "", "anchors_total": 12, "login_anchors": 0, "body_snippet": snippet}
        )
        assert markers == ("body_text",), snippet
    # Catalog wording on a link-poor page is not a wall: the text marker only
    # fires on the login wording the live wall actually carries.
    assert (
        server._login_wall_markers(
            {"title": "", "anchors_total": 12, "login_anchors": 0, "body_snippet": "商品目录 排行榜"}
        )
        == ()
    )


def test_login_wall_markers_are_gated_on_anchor_count():
    """A healthy LOGGED-OUT page carries header login links and 登录 wording —
    but also 100+ anchors (live 2026-09-10: 133 links including
    /member/login.jhtml while results rendered fine). The gate is what keeps
    that page out of the wall branch."""
    healthy = {
        "title": "手机_淘宝搜索",
        "items": [{"item_id": "123456789012"}],
        "anchors_total": 133,
        "login_anchors": 2,
        "body_snippet": "亲，请登录 免费注册 手机-淘宝搜索",
    }
    assert server._login_wall_markers(healthy) == ()
    assert server._login_wall(healthy) is False


def test_login_wall_markers_ignore_missing_and_garbage_fields():
    """A payload cached by an older build carries no structural fields: the
    title marker is the only one that can fire. Garbage values never invent a
    wall — coerce_int answers None and the gated markers stay silent."""
    assert server._login_wall_markers({"title": "手机-淘宝搜索", "items": []}) == ()
    assert (
        server._login_wall_markers({"title": "", "anchors_total": "many", "login_anchors": "2", "body_snippet": 42})
        == ()
    )
    assert server._login_wall_markers({}) == ()


async def test_search_maps_the_title_less_wall_to_transport_down(monkeypatch):
    """The 2026-09-10 regression: the empty-title wall payload must answer
    transport_down with the log-in fix inline — NOT the ParserDriftError that
    its zero-item list alone would have produced."""
    _patch_render(monkeypatch, TITLE_LESS_WALL)

    with pytest.raises(ToolError) as excinfo:
        await server.taobao_search("手机")

    error = _error_payload(excinfo)
    assert error["error"] == "transport_down"
    assert "login wall" in error["message"].lower()
    assert "log into taobao.com" in error["message"].lower()


async def test_card_maps_the_title_less_wall_to_transport_down(monkeypatch):
    """Item pages redirect to the same wall; the card path must classify it
    identically instead of drifting on 'neither title nor price'."""
    _patch_render(
        monkeypatch,
        {
            "title": None,
            "price_cny": None,
            "shop_name": None,
            "sales": None,
            "description_images": 0,
            "page_title": "",
            "anchors_total": 32,
            "login_anchors": 2,
            "body_snippet": "亲，请登录 免费注册",
        },
    )

    with pytest.raises(ToolError) as excinfo:
        await server.taobao_card("123456789012")

    error = _error_payload(excinfo)
    assert error["error"] == "transport_down"
    assert "login wall" in error["message"].lower()


# ------------------------------------------------------ anti-bot challenge ----
#
# The captcha verdict used to be baked in the extractor JS: title='__BLOCKED__'
# whenever 验证/人机/captcha matched document.body.textContent — INCLUDING the
# hidden script/widget text every page carries. Live probe 2026-09-10: healthy
# logged-out pages rendering 29-38 items answered __BLOCKED__ because the
# hidden baxia widget's text says «人机», so taobao_selfcheck reported
# inconclusive(blocked) and the shape-drift canary never ran. The verdict now
# lives in Python (_anti_bot_challenge) over the visibility-filtered
# body_snippet, gated on ZERO extracted items: a real challenge replaces the
# catalog, and a page with items is not a wall.


def test_anti_bot_challenge_is_gated_on_zero_items():
    """Items + hidden challenge wording -> catalog. Zero items + challenge
    wording -> challenge. The gate, not the wording, is the verdict."""
    healthy = {
        "title": "手机-淘宝搜索",
        "items": [{"item_id": "123456789012"}],
        "body_snippet": "亲，请登录 免费注册 Apple iPhone 16 Pro Max 人机验证 baxia-dialog",
    }
    assert server._anti_bot_challenge(healthy) is False
    challenge = {"title": "淘宝网", "items": [], "body_snippet": "亲，请拖动下方滑块完成验证后继续浏览"}
    assert server._anti_bot_challenge(challenge) is True


def test_anti_bot_challenge_honors_the_legacy_marker_and_ignores_garbage():
    """An older build's payload carries the JS-baked '__BLOCKED__' title — it
    still reads as a challenge. Garbage or missing fields never invent one,
    and zero items WITHOUT challenge wording is the drift question, not a
    challenge."""
    assert server._anti_bot_challenge({"title": "__BLOCKED__", "items": []}) is True
    assert (
        server._anti_bot_challenge({"title": "手机-淘宝搜索", "items": [], "body_snippet": "没有找到相关商品"}) is False
    )
    assert server._anti_bot_challenge({}) is False
    assert server._anti_bot_challenge({"items": None, "body_snippet": 42}) is False


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


async def test_card_with_a_login_worded_product_name_parses_and_is_not_a_wall(monkeypatch):
    """The live-reproduced false positive (review 2026-09-10): a fully rendered
    card whose PRODUCT name contains 登录 used to convict the title marker —
    `title` on a card payload is the product name, not document.title — and
    taobao_card raised TransportDownError("login wall") for a page that parsed
    perfectly. A rich page (300 anchors) with a clean DOCUMENT title must
    classify as no wall AND answer with the product data."""
    payload = {
        "title": "微信QQ账号电脑端登录 全自动发货 秒到账",
        "price_texts": {"attached": ["¥12.5"], "other": []},
        "shop_name": "游戏服务小店",
        "sales": "已售 100+",
        "description_images": 8,
        "page_title": "淘宝网",
        "anchors_total": 300,
        "login_anchors": 2,
        "body_snippet": "亲，请登录 免费注册 微信QQ账号电脑端登录 全自动发货 秒到账",
    }
    _patch_render(monkeypatch, payload)

    assert server._login_wall_markers(payload) == (), "the product name is data, not a wall marker"

    result = await server.taobao_card("123456789012")

    assert result.status == "success"
    assert result.title == payload["title"]
    assert result.price_cny == 12.5
    assert result.meta.healthy is True


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
    assert result.checks["search"].reason == "login_wall"


async def test_selfcheck_title_less_wall_is_inconclusive_never_drift(monkeypatch):
    """The 2026-09-10 regression: the EMPTY-title wall used to fall through to
    the zero-items branch and report drift/parse_smoke_failed. A session wall
    is inconclusive(login_wall) — the canary never saw the catalog, so it can
    say nothing about its shape."""
    _patch_render(monkeypatch, TITLE_LESS_WALL)

    result = await server.taobao_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None
    search = result.checks["search"]
    assert search.state == "inconclusive"
    assert search.reason == "login_wall"
    assert search.reason != "parse_smoke_failed"


async def test_titled_and_title_less_walls_classify_identically(monkeypatch):
    """Both wall variants — the classic titled one and the 2026-09-10
    empty-title one — must produce the same error code from the tools and the
    same verdict from the canary."""
    for payload in (LOGIN_WALL, TITLE_LESS_WALL):
        server._cache._data.clear()
        _patch_render(monkeypatch, payload)

        with pytest.raises(ToolError) as excinfo:
            await server.taobao_search("手机")
        error = _error_payload(excinfo)
        assert error["error"] == "transport_down", payload.get("title")

        result = await server.taobao_selfcheck()
        assert result.status == "inconclusive"
        assert result.checks["search"].reason == "login_wall"


async def test_selfcheck_zero_items_is_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "手机-淘宝搜索", "items": []})

    result = await server.taobao_selfcheck()

    assert result.status == "drift_detected"
    assert result.healthy is False


async def test_selfcheck_anti_bot_page_is_inconclusive(monkeypatch):
    """The legacy payload shape: an older build's extractor JS baked
    '__BLOCKED__' into the title. Still honored — a challenge is a session
    event, never drift."""
    _patch_render(monkeypatch, {"title": "__BLOCKED__", "items": []})

    result = await server.taobao_selfcheck()

    assert result.status == "inconclusive"
    assert result.checks["search"].state == "inconclusive"
    assert result.checks["search"].reason == "blocked"


async def test_selfcheck_genuine_challenge_is_inconclusive_blocked(monkeypatch):
    """The current payload shape: a genuine challenge — zero items, the catalog
    replaced by the widget — must read inconclusive(blocked) exactly like the
    legacy marker did. The verdict now comes from Python over the
    visibility-filtered text; the taxonomy is unchanged."""
    _patch_render(
        monkeypatch,
        {
            "title": "淘宝网",
            "items": [],
            "anchors_total": 8,
            "login_anchors": 0,
            "body_snippet": "亲，请拖动下方滑块完成验证后继续浏览",
        },
    )

    result = await server.taobao_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None
    search = result.checks["search"]
    assert search.state == "inconclusive"
    assert search.reason == "blocked"


async def test_selfcheck_runs_the_shape_canary_past_hidden_challenge_text(monkeypatch):
    """The consequence half of the 2026-09-10 probe: while the ungated JS
    verdict convicted healthy pages (hidden baxia widget text «人机»), the
    canary answered inconclusive(blocked) and the shape-drift check NEVER RAN.
    A payload WITH items must reach the shape check and come out healthy even
    with challenge wording in its visibility-filtered text."""
    _patch_render(
        monkeypatch,
        {
            "title": "手机-淘宝搜索",
            "anchors_total": 133,
            "login_anchors": 2,
            "body_snippet": "亲，请登录 免费注册 人机验证 captcha baxia Apple iPhone 16 Pro Max 全网通",
            "items": [
                {
                    "item_id": "123456789012",
                    "title": "Apple iPhone 16 Pro Max 全网通",
                    "price_texts": {"attached": ["¥9999"], "other": []},
                    "shop_name": "苹果官方旗舰店",
                    "location": "上海",
                    "sales": "2000+人付款",
                    "url": "https://item.taobao.com/item.htm?id=123456789012",
                }
            ],
        },
    )

    result = await server.taobao_selfcheck()

    assert result.status == "success"
    assert result.healthy is True
    search = result.checks["search"]
    assert search.state == "healthy"
    assert search.reason != "blocked"
    assert any("items extracted" in note for note in search.notes), "the shape canary never ran"


async def test_selfcheck_cries_shape_drift_when_the_price_family_vanishes(monkeypatch):
    """Items still extract, but every key the parser binds a price through is
    gone — that is structural drift, and it must be said out loud with the
    missing family named."""
    _patch_render(
        monkeypatch,
        {
            "title": "手机-淘宝搜索",
            "anchors_total": 133,
            "login_anchors": 0,
            "body_snippet": "手机-淘宝搜索 Apple iPhone 16 Pro Max 全网通",
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
