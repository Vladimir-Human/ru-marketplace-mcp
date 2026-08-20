"""Offline tests for the AliExpress connector.

CDP rendering is monkeypatched out: the suite runs with no Chrome and no
network. Fixture shapes mirror what the live grid and the card modules
returned on 2026-08-20 — in particular the measured quirk this connector
exists around: on a search tile the BASE price comes first and the current
price second, so the tile pairing is (min, next-above), never attached[0].
"""

from __future__ import annotations

import pytest
from aliexpress_connector import server
from fastmcp.exceptions import ToolError

SEARCH_EXTRACTED = {
    "punish": False,
    "page_title": "realme watch с бесплатной доставкой на AliExpress",
    "items": [
        {
            "item_id": "1005010003103368",
            "title": "Realme Watch 5 смарт-часы",
            "price_texts": {"attached": ["6 967 ₽", "2 939 ₽"], "other": ["2 334 купили"]},
            "rating": "4,7",
            "orders": "2 334",
            "sku_id": "12000050856992499",
            "url": "https://aliexpress.ru/item/1005010003103368.html",
        },
        {
            "item_id": "1005012078314868",
            "title": "realme Watch S5 умные часы",
            "price_texts": {"attached": ["5 320 ₽ с купоном", "5 579 ₽"], "other": ["190 купили"]},
            "rating": None,
            "orders": "190",
            "sku_id": None,
            "url": "https://aliexpress.ru/item/1005012078314868.html",
        },
        {
            "item_id": "1005012078314999",
            "title": "Часы без цены",
            "price_texts": {"attached": [], "other": []},
            "rating": None,
            "orders": None,
            "sku_id": None,
            "url": "https://aliexpress.ru/item/1005012078314999.html",
        },
    ],
}

CARD_EXTRACTED = {
    "punish": False,
    "title": "Realme Watch 5 смарт-часы",
    "price_texts": {"attached": ["2 939 ₽", "6 967 ₽"], "other": []},
    "rating_text": "4,7",
    "meta_description": "Realme Watch 5 смарт-часы. ➜ 2334 заказов. ✓ Хорошие отзывы покупателей. ⭐ Рейтинг- 4.7 ⚡ Мы ускорили доставку!",
    "url": "https://aliexpress.ru/item/1005010003103368.html",
}

PUNISH_PAYLOAD = {"punish": True, "items": [], "page_title": "Пройдите проверку"}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()


def _patch_search(monkeypatch, payload):
    async def fake_render(url, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render_search", fake_render)


async def test_search_parses_tiles_current_not_base(monkeypatch):
    """The live grid lists the base price first; the current price is the smaller."""
    _patch_search(monkeypatch, SEARCH_EXTRACTED)

    result = await server.aliexpress_search("realme watch")

    assert result.count == 3
    assert result.items[0].price_rub == 2939.0
    assert result.items[0].old_price_rub == 6967.0
    assert result.items[0].orders_count == 2334
    assert result.items[0].rating == 4.7


async def test_search_coupon_price_never_becomes_the_price(monkeypatch):
    """'с купоном' is not the price: the regular price wins, coupon goes to warnings."""
    _patch_search(monkeypatch, SEARCH_EXTRACTED)

    result = await server.aliexpress_search("realme watch")

    assert result.items[1].price_rub == 5579.0
    assert result.items[1].old_price_rub is None
    warnings = "; ".join(result.meta.warnings or [])
    assert "coupon" in warnings


async def test_card_punish_maps_to_transport_error(monkeypatch):
    payload = dict(CARD_EXTRACTED)
    payload["punish"] = True
    _patch_card(monkeypatch, payload)

    with pytest.raises(ToolError):
        await server.aliexpress_card("1005010003103368")


async def test_card_reordered_sticky_still_pairs_correctly(monkeypatch):
    """The pairing must not trust DOM order: a base-first sticky ships current=min."""
    payload = dict(CARD_EXTRACTED)
    payload["price_texts"] = {"attached": ["6 967 ₽", "2 939 ₽"], "other": []}
    _patch_card(monkeypatch, payload)

    result = await server.aliexpress_card("1005010003103368")

    assert result.price_rub == 2939.0
    assert result.old_price_rub == 6967.0


async def test_card_priceless_tile_is_none_never_zero(monkeypatch):
    _patch_search(monkeypatch, SEARCH_EXTRACTED)

    result = await server.aliexpress_search("realme watch")

    assert result.items[2].price_rub is None
    assert result.items[2].old_price_rub is None


async def test_search_maps_zero_tiles_to_parser_drift(monkeypatch):
    _patch_search(monkeypatch, {"punish": False, "items": []})

    with pytest.raises(ToolError):
        await server.aliexpress_search("realme watch")


async def test_search_maps_punish_to_transport_error(monkeypatch):
    _patch_search(monkeypatch, PUNISH_PAYLOAD)

    with pytest.raises(ToolError) as exc_info:
        await server.aliexpress_search("realme watch")
    assert "challenge" in str(exc_info.value) or "Пройдите" in str(exc_info.value)


def _patch_card(monkeypatch, payload):
    async def fake_card(url, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_card", fake_card)


async def test_card_current_price_first_in_sticky(monkeypatch):
    """The sticky module lists the current price FIRST — opposite order to tiles."""
    _patch_card(monkeypatch, CARD_EXTRACTED)

    result = await server.aliexpress_card("https://aliexpress.ru/item/1005010003103368.html")

    assert result.item_id == "1005010003103368"
    assert result.price_rub == 2939.0
    assert result.old_price_rub == 6967.0
    assert result.rating == 4.7
    assert result.orders_count == 2334


async def test_card_accepts_a_bare_numeric_id(monkeypatch):
    _patch_card(monkeypatch, CARD_EXTRACTED)

    result = await server.aliexpress_card("1005010003103368")

    assert result.item_id == "1005010003103368"


async def test_card_rejects_input_without_an_item_id():
    with pytest.raises(ToolError):
        await server.aliexpress_card("https://aliexpress.ru/category/watches.html")


async def test_card_rejects_off_host_when_id_extractable(monkeypatch):
    """A host-checked id: an attacker-provided URL must never steer the operator's Chrome."""
    _patch_card(monkeypatch, CARD_EXTRACTED)

    with pytest.raises(ToolError):
        await server.aliexpress_card("https://evil.example/item/1005010003103368.html")


async def test_card_flags_drift_when_neither_title_nor_price(monkeypatch):
    payload = dict(CARD_EXTRACTED)
    payload["title"] = None
    payload["price_texts"] = {"attached": [], "other": []}
    _patch_card(monkeypatch, payload)

    with pytest.raises(ToolError):
        await server.aliexpress_card("1005010003103368")


async def test_card_uses_meta_fallbacks_when_modules_absent(monkeypatch):
    payload = dict(CARD_EXTRACTED)
    payload["rating_text"] = None
    _patch_card(monkeypatch, payload)

    result = await server.aliexpress_card("1005010003103368")

    assert result.rating == 4.7
    assert result.orders_count == 2334


async def test_card_warns_when_price_missing(monkeypatch):
    payload = dict(CARD_EXTRACTED)
    payload["price_texts"] = {"attached": [], "other": []}
    _patch_card(monkeypatch, payload)

    result = await server.aliexpress_card("1005010003103368")

    assert result.price_rub is None
    warnings = "; ".join(result.meta.warnings or [])
    assert "price_missing" in warnings


# ------------------------------------------------------------- selfcheck ---------


async def test_selfcheck_healthy_when_both_gates_pass(monkeypatch):
    async def fake_search(url, ctx):
        return SEARCH_EXTRACTED

    async def fake_card(url, ctx):
        return CARD_EXTRACTED

    monkeypatch.setattr(server, "_cdp_render_search", fake_search)
    monkeypatch.setattr(server, "_cdp_card", fake_card)

    result = await server.aliexpress_selfcheck()

    assert result.status == "success"
    assert result.checks["search"].state == "healthy"
    assert result.checks["card"].state == "healthy"


async def test_selfcheck_inconclusive_on_punish(monkeypatch):
    async def fake_search(url, ctx):
        return PUNISH_PAYLOAD

    monkeypatch.setattr(server, "_cdp_render_search", fake_search)

    result = await server.aliexpress_selfcheck()

    assert result.status == "inconclusive"
    assert result.checks["search"].state == "inconclusive"
    assert result.checks["card"].reason == "skipped_no_search_health"


async def test_selfcheck_drift_on_zero_tiles(monkeypatch):
    async def fake_search(url, ctx):
        return {"punish": False, "items": []}

    monkeypatch.setattr(server, "_cdp_render_search", fake_search)

    result = await server.aliexpress_selfcheck()

    assert result.status == "drift_detected"
    assert result.checks["search"].state == "drift"
