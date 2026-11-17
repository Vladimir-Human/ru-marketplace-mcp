"""Basket routing, page links and catalog selectors found by audit cycle 1 (2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove the WB basket host follows WB's route map, every card item and category carries a full
         https://www.wildberries.ru page link, and the catalog selectors WB's own menu emits are accepted.
SCOPE: Pure parse helpers and models; the category-products shell with a fake transport (URL only).
DEPENDS: wb_connector.server, wb_connector.parse, wb_connector.models_output; pytest.
LINKS: R-WB-BASKET-ROUTE, R-OUTPUT-PAGE-URLS.wb, R-WB-CATEGORY-SELECTOR.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from wb_connector import server
from wb_connector.models_output import WbCardItem


@pytest.mark.req("R-WB-BASKET-ROUTE")
@pytest.mark.parametrize(
    ("nm_id", "host"),
    [
        (1_241_789_136, "basket-44"),  # the audit's card: vol 12417, range 11910-12677
        (519_000_000, "basket-28"),  # WB's CTO: basket 28 serves vol 5190-5501
        (480_000_000, "basket-26"),  # vol 4800: the old table sent it to 27
        (2_880_599_999, "basket-65"),  # last vol of the route map
    ],
)
def test_the_basket_host_follows_wbs_route_map(nm_id: int, host: str) -> None:
    assert server._basket_for_sku(nm_id) == f"{host}.wbbasket.ru"


@pytest.mark.req("R-WB-BASKET-ROUTE")
def test_an_nm_id_above_the_route_map_uses_the_configured_fallback() -> None:
    assert server._basket_for_sku(2_880_600_000) == f"{server.get_settings().basket_fallback}.wbbasket.ru"


@pytest.mark.req("R-OUTPUT-PAGE-URLS.wb")
def test_every_card_item_carries_its_product_page() -> None:
    item = WbCardItem(**server._card_item_dict({"id": 1241789136, "name": "x"}))
    assert item.url == "https://www.wildberries.ru/catalog/1241789136/detail.aspx"
    assert WbCardItem(nm_id=None).url == ""
    assert WbCardItem(nm_id=0).url == ""


@pytest.mark.req("R-OUTPUT-PAGE-URLS.wb")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/catalog/elektronika", "https://www.wildberries.ru/catalog/elektronika"),
        ("https://www.wildberries.ru/catalog/obuv", "https://www.wildberries.ru/catalog/obuv"),
        ("https://evil.example/catalog/obuv", ""),
        ("//evil.example/catalog", ""),
        ("http://www.wildberries.ru/catalog/obuv", ""),
        ("catalog/obuv", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_a_category_page_is_a_full_link_on_wildberries(raw: Any, expected: str) -> None:
    node = server._menu_node({"id": 1, "name": "n", "url": raw}, 0, [10], 1)
    assert node is not None
    assert node.url == expected


def _catalog_answer() -> tuple[int, str, None]:
    product = {"id": 1000, "name": "x", "totalQuantity": 1, "sizes": [{"price": {"product": 150000}}]}
    return 200, json.dumps({"data": {"products": [product]}}), None


@pytest.mark.req("R-WB-CATEGORY-SELECTOR")
@pytest.mark.parametrize(
    "query",
    [
        "vflags=8388608",  # Ресейл and Товары оптом in the live menu
        "subject=" + ";".join(str(n) for n in range(1000, 1130)),  # a 650-character subject list
    ],
)
def test_a_selector_wbs_own_menu_emits_reaches_the_catalog(monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    seen: list[str] = []

    async def no_wait() -> None:
        return None

    async def responder(client: Any, url: str) -> tuple[int, str, None]:
        seen.append(url)
        return _catalog_answer()

    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "_safe_get_text", responder)
    result = asyncio.run(server.wb_category_products(shard="c2c", query=query))
    assert seen and seen[0].endswith("&" + query)
    assert result.items[0].url == "https://www.wildberries.ru/catalog/1000/detail.aspx"


@pytest.mark.req("R-WB-CATEGORY-SELECTOR")
@pytest.mark.parametrize("query", ["vflags=1&cat=2", "subject=" + "1;" * 600, "vflags=abc"])
def test_a_selector_stays_bounded_and_numeric(monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    async def forbidden(client: Any, url: str) -> None:
        raise AssertionError("an unsafe selector must not reach the network")

    monkeypatch.setattr(server, "_safe_get_text", forbidden)
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(server.wb_category_products(shard="c2c", query=query))
    assert json.loads(str(excinfo.value))["error"] == "bad_request"
