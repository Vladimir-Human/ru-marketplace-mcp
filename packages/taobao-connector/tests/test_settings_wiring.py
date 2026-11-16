"""TAOBAO_TIMEOUT is the budget of the page evaluation (R-SETTINGS-WIRED).

The setting existed and nothing read it: the in-page extractor ran under a
hardcoded 30-second budget. It is now this connector's own budget, so an
operator can retune it without editing the source — and the default stays the
30 seconds the evaluation had before.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError
from taobao_connector import server
from taobao_connector.settings import TaobaoSettings

SEARCH_EXTRACTED = {
    "title": "手机-淘宝搜索",
    "anchors_total": 133,
    "login_anchors": 2,
    "body_snippet": "亲，请登录 免费注册 手机-淘宝搜索 Apple iPhone 16 Pro Max 全网通",
    "grid_loading": False,
    "items": [
        {
            "item_id": "123456789012",
            "title": "Apple iPhone 16 Pro Max 全网通",
            "price_cny": 9999.0,
            "shop_name": "苹果官方旗舰店",
            "location": None,
            "sales": "2000+人付款",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
        }
    ],
}


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache.clear()


class _Page:
    """A page whose extractor answers immediately with a captured payload."""

    async def evaluate(self, expression):
        return json.dumps(SEARCH_EXTRACTED, ensure_ascii=False)


@pytest.mark.req("R-SETTINGS-WIRED")
async def test_the_configured_timeout_reaches_the_page_evaluation(monkeypatch):
    """The env var must land in the evaluation budget, not in a dead constant."""
    timeouts: list[float | None] = []
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(awaitable, timeout=None):
        timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout=timeout)

    @asynccontextmanager
    async def fake_open_page(url, wait_ms=0, **kwargs):
        yield _Page()

    monkeypatch.setenv("TAOBAO_TIMEOUT", "12")
    monkeypatch.setattr(server, "_settings", TaobaoSettings())
    monkeypatch.setattr(server, "open_page", fake_open_page)
    monkeypatch.setattr(asyncio, "wait_for", spy_wait_for)

    await server.taobao_search("手机")

    assert 12.0 in timeouts


@pytest.mark.req("R-SETTINGS-WIRED")
def test_the_default_budget_is_the_one_the_evaluation_had_before(monkeypatch):
    monkeypatch.delenv("TAOBAO_TIMEOUT", raising=False)

    assert TaobaoSettings().timeout == 30.0


@pytest.mark.req("R-SETTINGS-WIRED")
@pytest.mark.parametrize("value", [4.9, 45.1])
def test_a_budget_outside_five_to_forty_five_seconds_is_refused(value):
    with pytest.raises(ValidationError):
        TaobaoSettings(timeout=value)
