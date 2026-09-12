"""A real MCP client can recover a source on the same owned browser page."""

import json
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
from compare_connector import server as compare
from fastmcp import Client
from fastmcp.exceptions import ToolError
from lamoda_connector import server as lamoda
from mcp_core.cache import TTLCache
from mcp_core.transport import browser_handoff as handoff
from taobao_connector import server as taobao


@pytest.fixture
async def browser(monkeypatch):
    pages = []

    class Page:
        url = "https://www.lamoda.ru/catalogsearch/result/?q=test"
        solved = False
        closed = False

        async def evaluate(self, expression):
            return json.dumps(
                {
                    "items": [
                        {"sku": "MP002XM1RMM3", "item_id": "123456789012", "title": "Test shoes", "price_rub": 500}
                    ]
                    if self.solved
                    else [],
                    "body_snippet": "" if self.solved else "CAPTCHA: Подтвердите, что вы не робот",
                    "title": "Test shoes" if self.solved else None,
                    "page_title": "Test shoes" if self.solved else "Security check",
                    "price_cny": 500 if self.solved else None,
                    "_handoff_expires_at": "untrusted page value",
                }
            )

    @asynccontextmanager
    async def open_page(url, wait_ms, **kwargs):
        page = Page()
        page.url = url
        pages.append(page)
        try:
            yield page
        finally:
            page.closed = True

    async def current_url(page):
        return page.url

    async def reveal(page):
        pass

    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", "60")
    monkeypatch.setattr(handoff.chrome_cdp, "HEADLESS", False)
    monkeypatch.setattr(handoff.chrome_cdp, "open_page", open_page)
    monkeypatch.setattr(handoff.chrome_cdp, "current_page_url", current_url)
    monkeypatch.setattr(handoff.chrome_cdp, "reveal_owned_page", reveal)
    monkeypatch.setattr(lamoda, "_cache", TTLCache(ttl_s=120))
    monkeypatch.setattr(lamoda, "_min_gap", 0)
    lamoda._pacer.reset()
    monkeypatch.setattr(taobao, "_cache", TTLCache(ttl_s=120))
    monkeypatch.setattr(taobao, "_min_gap", 0)
    taobao._pacer.reset()
    monkeypatch.setattr(compare, "SOURCES", {"lamoda": lamoda})
    yield pages
    await handoff.close_handoffs()


async def test_compare_mcp_retains_source_session_and_resumes_without_navigation(browser):
    arguments = {"query": "test", "sources": ["lamoda"]}
    async with Client(compare.mcp) as client:
        first = (await client.call_tool("compare_prices", arguments)).structured_content
        outcome = first["source_outcomes"][0]
        assert outcome["requires_user_action"] is True
        assert outcome["handoff_expires_at"].endswith("Z")
        assert "untrusted" not in outcome["handoff_expires_at"]
        assert len(browser) == 1 and not browser[0].closed
        browser[0].solved = True
        second = (await client.call_tool("compare_prices", arguments)).structured_content
        assert second["complete"] is True
        assert second["cheapest"]["price_rub"] == 500
        assert second["source_outcomes"][0]["handoff_expires_at"] is None
        assert len(browser) == 1 and browser[0].closed


async def test_distinct_mcp_sessions_do_not_share_challenge_tabs(browser):
    arguments = {"query": "test", "sources": ["lamoda"]}
    async with Client(compare.mcp) as first, Client(compare.mcp) as second:
        await first.call_tool("compare_prices", arguments)
        await second.call_tool("compare_prices", arguments)
        assert len(browser) == 2
        assert all(not page.closed for page in browser)
        browser[0].solved = True
        recovered = (await first.call_tool("compare_prices", arguments)).structured_content
        assert recovered["complete"] is True
        assert browser[0].closed and not browser[1].closed
    assert all(page.closed for page in browser)


async def test_mcp_shutdown_closes_pending_handoff(browser):
    async with Client(compare.mcp) as client:
        await client.call_tool("compare_prices", {"query": "test", "sources": ["lamoda"]})
        assert len(browser) == 1 and not browser[0].closed
    assert browser[0].closed


@pytest.mark.parametrize("kind", ["search", "card"])
async def test_taobao_tools_resume_the_retained_page(browser, kind):
    arguments = {"query": "test"} if kind == "search" else {"item_id_or_url": "123456789012"}
    async with Client(taobao.mcp) as client:
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool(f"taobao_{kind}", arguments)
        error = json.loads(str(excinfo.value))
        assert error["handoff_expires_at"]
        assert error["challenge_type"] == "captcha"
        assert len(browser) == 1 and not browser[0].closed
        browser[0].solved = True
        recovered = (await client.call_tool(f"taobao_{kind}", arguments)).structured_content
        assert recovered["status"] == "success"
        assert len(browser) == 1 and browser[0].closed


def test_compare_import_does_not_require_browser_extra():
    script = """
import builtins
original = builtins.__import__
def without_browser(name, *args, **kwargs):
    if name == 'playwright' or name.startswith('playwright.'):
        raise ImportError('browser extra deliberately unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = without_browser
from compare_connector import server
assert server.mcp.name == 'compare-connector'
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
