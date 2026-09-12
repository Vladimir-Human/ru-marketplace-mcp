"""A real MCP client can recover a source on the same owned browser page."""

import base64
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
                    "_handoff_id": "untrusted-page-identifier",
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
        assert outcome["handoff_id"] and "untrusted" not in outcome["handoff_id"]
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
        blocked = (await second.call_tool("compare_prices", arguments)).structured_content
        assert blocked["complete"] is False  # A's success cache must not bypass B's owned page
        assert blocked["source_outcomes"][0]["handoff_expires_at"]
        assert not browser[1].closed
        browser[1].solved = True
        recovered_b = (await second.call_tool("compare_prices", arguments)).structured_content
        assert recovered_b["complete"] is True
        assert len(browser) == 2 and browser[1].closed
    assert all(page.closed for page in browser)


async def test_mcp_shutdown_closes_pending_handoff(browser):
    async with Client(compare.mcp) as client:
        await client.call_tool("compare_prices", {"query": "test", "sources": ["lamoda"]})
        assert len(browser) == 1 and not browser[0].closed
    assert browser[0].closed


@pytest.mark.parametrize("kind", ["search", "card"])
async def test_taobao_tools_resume_the_retained_page(browser, kind):
    arguments = {"query": "test"} if kind == "search" else {"item_id_or_url": "123456789012"}
    async with Client(taobao.mcp) as client, Client(taobao.mcp) as second:
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool(f"taobao_{kind}", arguments)
        error = json.loads(str(excinfo.value))
        assert error["handoff_expires_at"]
        assert error["challenge_type"] == "captcha"
        assert len(browser) == 1 and not browser[0].closed
        with pytest.raises(ToolError):
            await second.call_tool(f"taobao_{kind}", arguments)
        assert len(browser) == 2
        browser[0].solved = True
        recovered = (await client.call_tool(f"taobao_{kind}", arguments)).structured_content
        assert recovered["status"] == "success"
        assert len(browser) == 2 and browser[0].closed
        with pytest.raises(ToolError) as second_error:
            await second.call_tool(f"taobao_{kind}", arguments)
        assert json.loads(str(second_error.value))["handoff_expires_at"]
        assert not browser[1].closed
        browser[1].solved = True
        recovered_b = (await second.call_tool(f"taobao_{kind}", arguments)).structured_content
        assert recovered_b["status"] == "success"
        assert len(browser) == 2 and browser[1].closed


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


async def test_snapshot_mcp_transmits_image_without_ending_or_extending_handoff(browser, monkeypatch):
    captured = []
    image_data = base64.b64encode(b"\xff\xd8fixture\xff\xd9").decode()

    async def capture(page):
        captured.append(page)
        return {"image_data": image_data, "mime_type": "image/jpeg", "width": 640, "height": 360}

    monkeypatch.setattr(handoff.chrome_cdp, "capture_owned_viewport", capture)
    async with Client(compare.mcp) as client:
        first = (await client.call_tool("compare_prices", {"query": "test", "sources": ["lamoda"]})).structured_content
        outcome = first["source_outcomes"][0]
        before_expiry = outcome["handoff_expires_at"]
        result = await client.call_tool("compare_browser_snapshot", {"handoff_id": outcome["handoff_id"]})
        images = [content for content in result.content if content.type == "image"]
        assert len(images) == 1
        assert images[0].data == image_data and images[0].mimeType == "image/jpeg"
        metadata = result.structured_content
        assert "image_data" not in metadata
        assert metadata["page_origin"] == "https://www.lamoda.ru"
        assert metadata["captured_at"]
        from datetime import datetime

        assert datetime.fromisoformat(metadata["handoff_expires_at"]) == datetime.fromisoformat(before_expiry)
        assert captured == [browser[0]]
        assert len(browser) == 1 and not browser[0].closed
        browser[0].solved = True
        second = (await client.call_tool("compare_prices", {"query": "test", "sources": ["lamoda"]})).structured_content
        assert second["complete"] is True and browser[0].closed


async def test_snapshot_rejects_other_session_and_unknown_handle_without_capture(browser, monkeypatch):
    async def capture(page):
        pytest.fail("foreign or missing handle must not capture any page")

    monkeypatch.setattr(handoff.chrome_cdp, "capture_owned_viewport", capture)
    async with Client(compare.mcp) as owner, Client(compare.mcp) as other:
        result = (await owner.call_tool("compare_prices", {"query": "test", "sources": ["lamoda"]})).structured_content
        handle = result["source_outcomes"][0]["handoff_id"]
        for client, candidate in [(other, handle), (owner, "unknown-handoff-123456")]:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("compare_browser_snapshot", {"handoff_id": candidate})
            assert json.loads(str(excinfo.value))["error"] == "not_found"
        assert len(browser) == 1 and not browser[0].closed
