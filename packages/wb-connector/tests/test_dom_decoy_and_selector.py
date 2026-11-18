"""Regression tests for WB category selector MCP schema, DOM decoy filtering, and DOM lock cache recheck."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastmcp import Client
from wb_connector import dom_transport, server


@pytest.mark.req("R-WB-CATEGORY-SELECTOR")
async def test_wb_category_products_mcp_call_accepts_650_char_subject_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_selector = "subject=" + ";".join(str(n) for n in range(1000, 1130))
    assert 600 <= len(long_selector) <= 1000
    seen: list[str] = []

    async def no_wait() -> None:
        return None

    async def responder(client: Any, url: str) -> tuple[int, str, None]:
        seen.append(url)
        product = {"id": 1000, "name": "x", "totalQuantity": 1, "sizes": [{"price": {"product": 150000}}]}
        return 200, json.dumps({"data": {"products": [product]}}), None

    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "_safe_get_text", responder)

    async with Client(server.mcp) as client:
        result = await client.call_tool("wb_category_products", {"shard": "c2c", "query": long_selector})

    assert not result.is_error
    assert seen and seen[0].endswith("&" + long_selector)


@pytest.mark.req("R-CAPTURE-MUTATION.split-payment")
def test_dom_search_and_card_ignore_decoy_price_lines() -> None:
    tile = {
        "nm": "1345073040",
        "lines": [
            "200 ₽ за отзыв",
            "по 1 250 ₽ × 4 частями",
            "Пошлина ~2 450 ₽",
            "Доставка 299 ₽",
            "4 961 ₽",
            "9 338 ₽",
            "с WB Кошельком",
            "Apple / Наушники беспроводные",
        ],
        "del": ["9 338 ₽"],
        "wallet": [],
        "wallet_icon": True,
    }
    parsed = dom_transport.parse_search_tile(tile)
    assert parsed is not None
    assert parsed["price_rub"] == 4961.0
    assert parsed["wallet_price_rub"] == 4961.0
    assert parsed["price_kind"] == "wallet"
    assert parsed["price_original_rub"] == 9338.0

    # Three figures below struck price without an explicit wallet_hint leave regular unproven.
    regular, wallet, crossed = dom_transport.assign_prices([4800.0, 4961.0, 5063.0, 9338.0], crossed_hint=9338.0)
    assert regular is None
    assert wallet is None
    assert crossed == 9338.0


@pytest.mark.req("R-WB-DOM-NAVIGATION-RETRY")
async def test_dom_render_rechecks_cache_inside_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_core.transport import chrome_cdp

    dom_transport._dom_cache.clear()
    opens = 0
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    class FakePage:
        async def evaluate(self, script: str) -> str:
            if script == dom_transport.PROBE_JS:
                return json.dumps({"state": "ok", "tiles": 2, "empty": False})
            return json.dumps({"title": "WB", "url": "https://www.wildberries.ru/", "tiles": [{"nm": "1"}]})

    @asynccontextmanager
    async def fake_open_page(url: str, **_kwargs: Any):
        nonlocal opens
        opens += 1
        first_entered.set()
        await release_first.wait()
        yield FakePage()

    async def no_gap() -> None:
        return None

    monkeypatch.setattr(chrome_cdp, "open_page", fake_open_page)
    monkeypatch.setattr(dom_transport, "_gap", no_gap)

    url = "https://www.wildberries.ru/catalog/0/search.aspx?search=test"
    t1 = asyncio.create_task(dom_transport.render(url, dom_transport.SEARCH_EXTRACT_JS, "search"))
    await first_entered.wait()
    t2 = asyncio.create_task(dom_transport.render(url, dom_transport.SEARCH_EXTRACT_JS, "search"))
    await asyncio.sleep(0)
    release_first.set()
    r1, r2 = await asyncio.gather(t1, t2)

    assert opens == 1
    assert r1["tiles"] == r2["tiles"]
    dom_transport._dom_cache.clear()
