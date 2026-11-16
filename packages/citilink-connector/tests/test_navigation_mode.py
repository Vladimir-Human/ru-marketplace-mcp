"""Citilink public tool navigation wiring and HTTP 429 error taxonomy only."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from citilink_connector import server
from mcp_core.errors import ToolError
from mcp_core.transport import chrome_cdp


@pytest.fixture
def tool_navigation(monkeypatch):
    state = SimpleNamespace(calls=[], status=None, waits=0)
    server._cache._data.clear()

    async def wait():
        state.waits += 1

    async def evaluate(expression):
        # Stop before parsing: this is a navigation shell test, not invented data.
        raise ToolError("extraction reached")

    @asynccontextmanager
    async def open_page(url, *, wait_ms, **options):
        state.calls.append((url, wait_ms, options))
        if state.status is not None:
            raise chrome_cdp.NavBlocked(state.status, url)
        yield SimpleNamespace(evaluate=evaluate)

    monkeypatch.setattr(server, "_polite_wait", wait)
    monkeypatch.setattr(chrome_cdp, "open_page", open_page)
    return state


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
@pytest.mark.parametrize("tool", ["search", "card"])
async def test_citilink_tools_opt_in_without_changing_settle_delay(tool_navigation, tool):
    with pytest.raises(ToolError, match="extraction reached"):
        if tool == "search":
            await server.citilink_search("laptop")
        else:
            await server.citilink_card("laptop-2169270")
    assert tool_navigation.waits == 1
    assert len(tool_navigation.calls) == 1
    url, wait_ms, options = tool_navigation.calls[0]
    assert url == (
        f"{server.SEARCH_URL}?text=laptop" if tool == "search" else f"{server.SITE_BASE}/product/laptop-2169270/"
    )
    assert wait_ms == 8000
    assert options.get("wait_for_dom_parsed") is True
    assert not server._cache._data


@pytest.mark.req("R-CDP-NAV-TIMEOUT.qrator-429")
@pytest.mark.parametrize("tool", ["search", "card"])
async def test_citilink_429_returns_rate_limited_with_retry_delay(tool_navigation, tool):
    tool_navigation.status = 429
    start = asyncio.get_running_loop().time()
    with pytest.raises(ToolError) as caught:
        async with asyncio.timeout(12):
            if tool == "search":
                await server.citilink_search("laptop")
            else:
                await server.citilink_card("laptop-2169270")
    assert asyncio.get_running_loop().time() - start < 12
    error = json.loads(str(caught.value))
    assert error["error"] == "rate_limited"
    assert error["retry_after_s"] > 0
    assert error["provider"] == "citilink"
    assert error["status_code"] == 429
    assert error["retryable"] is True
    assert len(tool_navigation.calls) == 1
    assert not server._cache._data


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
@pytest.mark.req("R-CDP-NAV-TIMEOUT.qrator-429")
@pytest.mark.parametrize("tool", ["search", "card"])
@pytest.mark.parametrize("status", [200, 429])
async def test_public_citilink_never_waits_for_missing_domcontentloaded(monkeypatch, tool, status):
    """Real connector/kit/open_page; only browser I/O is replaced."""
    calls = []
    server._cache._data.clear()

    class Page:
        url = server.SITE_BASE

        async def goto(self, url, *, wait_until, timeout):
            calls.append(("goto", wait_until))
            self.url = url
            if wait_until == "domcontentloaded":
                raise chrome_cdp._PlaywrightTimeoutError("DOMContentLoaded never fired despite interactive DOM")
            return SimpleNamespace(status=status)

        async def wait_for_function(self, expression, *, timeout):
            calls.append(("parsed",))
            assert "interactive" in expression and "complete" in expression

        async def wait_for_timeout(self, ms):
            calls.append(("settled", ms))

        async def evaluate(self, expression):
            raise ToolError("extraction reached")

        async def close(self):
            calls.append(("closed",))

    @asynccontextmanager
    async def context():
        yield SimpleNamespace()

    async def new_tab(ctx):
        return Page()

    async def wait():
        pass

    monkeypatch.setattr(server, "_polite_wait", wait)
    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "get_context", context)
    monkeypatch.setattr(chrome_cdp, "_new_tab", new_tab)
    with pytest.raises(ToolError) as caught:
        async with asyncio.timeout(12):
            if tool == "search":
                await server.citilink_search("laptop")
            else:
                await server.citilink_card("laptop-2169270")
    if status == 200:
        assert str(caught.value) == "extraction reached"
        assert calls == [("goto", "commit"), ("parsed",), ("settled", 8000), ("closed",)]
    else:
        assert json.loads(str(caught.value))["error"] == "rate_limited"
        assert calls == [("goto", "commit"), ("closed",)]
    assert not server._cache._data
