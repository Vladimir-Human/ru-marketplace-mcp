"""Regression tests for AliExpress coupon/delivery/rating/orders truth and healthy caching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aliexpress_connector import server
from mcp_core.cache import TTLCache
from mcp_core.domtest import JsdomUnavailable, run_extractor


@pytest.fixture(autouse=True)
def _clear_ali_cache() -> None:
    server._cache.clear()


@pytest.mark.req("R-ALIEXPRESS-LONG-TITLE")
def test_search_extractor_skips_coupon_leaf_title_and_bare_integer_rating(tmp_path: Path) -> None:
    html = """<!doctype html><html><head><title>AliExpress</title></head><body>
      <div class="tile">
        <a href="https://aliexpress.ru/item/1005010003103368.html">
          <div>9 189 ₽ с купоном</div>
          <div>2</div>
          <div>4,9</div>
          <div>25 купили</div>
        </a>
      </div>
      <div class="tile">
        <a href="https://aliexpress.ru/item/1005010003103369.html">
          <div>Кроссовки городские замшевые унисекс</div>
          <div>3</div>
          <div>3 990 ₽</div>
        </a>
      </div>
    </body></html>"""
    grid = tmp_path / "ali_grid.html"
    grid.write_text(html, encoding="utf-8")
    try:
        raw = run_extractor(server._SEARCH_EXTRACT_JS, grid, page_url="https://aliexpress.ru/wholesale")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    payload = raw if isinstance(raw, dict) else json.loads(raw)

    by_id = {item["item_id"]: item for item in payload["items"]}
    assert by_id["1005010003103368"]["title"] is None
    assert by_id["1005010003103368"]["rating"] == "4.9"
    assert by_id["1005010003103369"]["rating"] is None


@pytest.mark.req("R-ALIEXPRESS-LONG-TITLE")
def test_tile_and_card_prices_ignore_delivery_and_plus_delta_leaves() -> None:
    tile = {
        "price_texts": {
            "attached": ["завтра, 349 ₽", "послезавтра, 299 ₽", "курьером, 499 ₽", "+ 349 ₽", "4 500 ₽"],
            "other": [],
        }
    }
    price, old_price, coupon = server._tile_prices(tile)
    assert price == 4500.0
    assert old_price is None
    assert coupon is None

    card_price, card_old = server._card_prices(tile)
    assert card_price == 4500.0
    assert card_old is None


@pytest.mark.req("R-CACHE-VALIDATED")
async def test_search_truncated_and_coupon_notes_stay_healthy_and_unhealthy_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=300))
    healthy_tiles = [
        {
            "item_id": f"100501000310{i:04d}",
            "title": f"Умные часы модель {i}",
            "price_texts": {"attached": ["5 579 ₽", "4 999 ₽ с купоном"], "other": []},
            "rating": "4.8",
            "orders": "120",
            "sku_id": None,
            "image_url": "https://ae04.alicdn.com/kf/S1.jpg",
            "url": f"https://aliexpress.ru/item/100501000310{i:04d}.html",
        }
        for i in range(48)
    ]

    async def fake_healthy_search(url: str, ctx: object) -> dict:
        return {"punish": False, "page_title": "AliExpress", "items": healthy_tiles}

    monkeypatch.setattr(server, "_cdp_render_search", fake_healthy_search)
    res = await server.aliexpress_search("умные часы")
    assert res.meta.healthy is True
    assert any(w.startswith("info:truncated_at_48:") for w in res.meta.warnings)
    assert any(w.startswith("info:coupon_seen:") for w in res.meta.warnings)
    assert len(server._cache) == 1

    server._cache.clear()
    unpriced_tiles = [{**healthy_tiles[0], "price_texts": {"attached": [], "other": []}}]

    async def fake_unpriced_search(url: str, ctx: object) -> dict:
        return {"punish": False, "page_title": "AliExpress", "items": unpriced_tiles}

    monkeypatch.setattr(server, "_cdp_render_search", fake_unpriced_search)
    unhealthy = await server.aliexpress_search("без цен")
    assert unhealthy.meta.healthy is False
    assert len(server._cache) == 0


@pytest.mark.req("R-CACHE-VALIDATED")
@pytest.mark.parametrize(("meta_desc", "expected"), [("➜ 1 заказ", 1), ("➜ 24 заказа", 24), ("➜ 1 205 заказов", 1205)])
async def test_card_orders_regex_parses_singular_and_paucal_forms_and_gates_cache(
    monkeypatch: pytest.MonkeyPatch, meta_desc: str, expected: int
) -> None:
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=300))

    async def fake_card(url: str, ctx: object) -> dict:
        return {
            "punish": False,
            "title": "Realme Watch 5",
            "price_texts": {"attached": ["2 939 ₽"], "other": []},
            "rating_text": "4,8",
            "meta_description": meta_desc,
        }

    monkeypatch.setattr(server, "_cdp_card", fake_card)
    res = await server.aliexpress_card("1005010003103368")
    assert res.orders_count == expected
    assert res.meta.healthy is True
    assert len(server._cache) == 1
