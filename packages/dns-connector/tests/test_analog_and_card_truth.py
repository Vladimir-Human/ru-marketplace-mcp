"""Regression tests for DNS /product/analog/ routing, card top-scoping, 404s, and healthy caching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dns_connector import server
from fastmcp.exceptions import ToolError
from mcp_core.cache import TTLCache
from mcp_core.domtest import JsdomUnavailable, run_extractor


@pytest.fixture(autouse=True)
def _clear_dns_cache() -> None:
    server._cache.clear()


@pytest.mark.req("R-OUTPUT-PAGE-URLS")
def test_extract_product_id_handles_analog_and_rejects_bare_analog() -> None:
    assert (
        server._extract_product_id("https://www.dns-shop.ru/product/analog/05ebcc7973baed20/stiralnaya-masina/")
        == "05ebcc7973baed20"
    )
    assert server._extract_product_id("https://www.dns-shop.ru/product/card/05ebcc7973baed20/") == "05ebcc7973baed20"
    assert server._extract_product_id("https://www.dns-shop.ru/product/analog/") is None
    assert server._extract_product_id("analog") is None
    assert server._extract_product_id("https://evil.example/product/analog/05ebcc7973baed20/stiralnaya-masina/") is None


@pytest.mark.req("R-OUTPUT-PAGE-URLS")
def test_search_extractor_keeps_distinct_analog_tiles(tmp_path: Path) -> None:
    html = """<!doctype html><html><head><title>DNS</title></head><body>
      <div class="catalog-product">
        <a class="catalog-product__name" href="/product/analog/05ebcc7973baed20/item-one/">Стиральная машина A</a>
        <div class="product-buy__price">29 999 ₽</div>
      </div>
      <div class="catalog-product">
        <a class="catalog-product__name" href="/product/analog/16fcdd8084cbf131/item-two/">Стиральная машина B</a>
        <div class="product-buy__price">31 999 ₽</div>
      </div>
    </body></html>"""
    grid = tmp_path / "analog_grid.html"
    grid.write_text(html, encoding="utf-8")
    try:
        payload = run_extractor(server._SEARCH_EXTRACT_JS, grid, page_url="https://www.dns-shop.ru/search/?q=washer")
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))

    ids = [item["product_id"] for item in payload["items"]]
    assert ids == ["05ebcc7973baed20", "16fcdd8084cbf131"]


@pytest.mark.req("R-OUTPUT-PAGE-URLS")
def test_search_item_normalizes_relative_and_drops_off_host_url() -> None:
    rel = server._search_item_from_tile(
        {
            "product_id": "05ebcc7973baed20",
            "title": "Washer",
            "price_text": "29 999 ₽",
            "url": "/product/05ebcc7973baed20/",
        }
    )
    foreign = server._search_item_from_tile(
        {
            "product_id": "05ebcc7973baed20",
            "title": "Washer",
            "price_text": "29 999 ₽",
            "url": "https://evil.example/product/05ebcc7973baed20/",
        }
    )
    assert rel.url == "https://www.dns-shop.ru/product/05ebcc7973baed20/"
    assert foreign.url == ""


@pytest.mark.req("R-DNS-FIRST-VISIT-PRICES")
def test_card_extractor_scopes_to_product_card_top_and_marks_sold_out(tmp_path: Path) -> None:
    html = """<!doctype html><html><head><title>Ноутбук — DNS</title></head><body>
      <div class="product-card-top">
        <h1 class="product-card-top__title">Ноутбук ASUS VivoBook 15</h1>
        <div class="order-avail-wrap"><span class="available">Товар распродан</span></div>
      </div>
      <div class="accessories-rail">
        <div class="product-buy__price">499 ₽</div>
        <div class="product-buy__prev">799</div>
        <span class="available">В наличии</span>
      </div>
    </body></html>"""
    card_file = tmp_path / "oos_card.html"
    card_file.write_text(html, encoding="utf-8")
    try:
        payload = run_extractor(
            server._CARD_EXTRACT_JS,
            card_file,
            page_url="https://www.dns-shop.ru/product/05ebcc7973baed20/noutbuk/",
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))

    assert payload["price_text"] is None
    assert payload["old_price_text"] is None
    assert payload["is_available"] is False


@pytest.mark.req("R-ERROR-NEXT-STEP")
async def test_card_not_found_page_raises_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_render(url: str, extract_js: str, wait_ms: int, ctx: object) -> dict:
        return {
            "title": "Страница не найдена",
            "price_text": None,
            "old_price_text": None,
            "is_available": None,
            "page_title": "Страница не найдена — DNS",
            "qrator_wall": False,
        }

    monkeypatch.setattr(server, "_cdp_render", fake_render)
    with pytest.raises(ToolError) as excinfo:
        await server.dns_card("05ebcc7973baed20")
    assert json.loads(str(excinfo.value))["error"] == "not_found"


@pytest.mark.req("R-CACHE-VALIDATED")
async def test_unhealthy_search_and_card_do_not_enter_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=300))

    async def fake_search_render(url: str, ctx: object) -> dict:
        return {
            "title": "ноутбук — DNS",
            "items": [
                {
                    "product_id": "05ebcc7973baed20",
                    "title": "Ноутбук без цены",
                    "price_text": None,
                    "old_price_text": None,
                    "availability_text": "В наличии",
                    "url": "https://www.dns-shop.ru/product/05ebcc7973baed20/",
                }
            ],
        }

    monkeypatch.setattr(server, "_render_search", fake_search_render)
    res_search = await server.dns_search("ноутбук")
    assert res_search.meta.healthy is False
    assert len(server._cache) == 0

    async def fake_card_render(url: str, extract_js: str, wait_ms: int, ctx: object) -> dict:
        return {
            "title": "Ноутбук в наличии без цены",
            "price_text": None,
            "old_price_text": None,
            "is_available": True,
            "page_title": "Ноутбук — DNS",
        }

    monkeypatch.setattr(server, "_cdp_render", fake_card_render)
    res_card = await server.dns_card("05ebcc7973baed20")
    assert res_card.meta.healthy is False
    assert len(server._cache) == 0
