"""Cian search on the bot check page, and the CIAN_TIMEOUT read budget.

START_MODULE_CONTRACT
PURPOSE: Prove R-CIAN-SEARCH-BOT-CHECK: a search tab Cian sent to its bot check (/xpvnsulc/) sends no API
         request and answers challenge_required (the selfcheck says challenge_required too), with no pacer
         refusal; and R-SETTINGS-WIRED.cian-read: CIAN_TIMEOUT is the only bound on the search and card reads.
SCOPE: The CDP shell only (_cdp_post_json, _cdp_page_config) with a fake open_page and page; no browser.
DEPENDS: pytest; stdlib asyncio/contextlib/json; cian_connector.server; mcp_core.transport.attempt_deadline; fastmcp ToolError.
LINKS: M-CIAN; R-CIAN-SEARCH-BOT-CHECK; R-SETTINGS-WIRED.cian-read; live run 2026-11-17 (search tab on the check page).
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from cian_connector import server
from fastmcp.exceptions import ToolError
from mcp_core.transport import attempt_deadline

CHECK_URL = "https://www.cian.ru/xpvnsulc/?back_location=https%3A%2F%2Fwww.cian.ru%2F"
HOME_URL = "https://www.cian.ru/"


class _Page:
    def __init__(self, url: str, answer: object) -> None:
        self.url = url
        self.answer = answer
        self.evaluated: list[object] = []

    async def evaluate(self, script: str, args: object) -> object:
        self.evaluated.append(args)
        return self.answer


def _patch_open_page(monkeypatch, page: _Page) -> None:
    @contextlib.asynccontextmanager
    async def fake_open_page(url, **kwargs):
        yield page

    async def no_wait() -> None:
        return None

    monkeypatch.setattr(server, "open_page", fake_open_page)
    monkeypatch.setattr(server, "_polite_wait", no_wait)
    server._cache.clear()


@pytest.mark.req("R-CIAN-SEARCH-BOT-CHECK")
async def test_a_search_tab_on_the_bot_check_sends_nothing_and_needs_a_human(monkeypatch) -> None:
    page = _Page(CHECK_URL, json.dumps({"status": 200, "text": "{}"}))
    _patch_open_page(monkeypatch, page)
    refusals: list[object] = []
    monkeypatch.setattr(server._pacer, "record_refusal", lambda *a, **k: refusals.append(a))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("sale")

    error = json.loads(str(excinfo.value))
    assert error["error"] == "challenge_required"
    assert error["provider"] == "cian"
    assert "marketplace-mcp unblock cian" in error["user_action"]
    assert page.evaluated == []
    assert refusals == []


@pytest.mark.req("R-CIAN-SEARCH-BOT-CHECK")
async def test_the_selfcheck_names_the_bot_check_as_a_human_step(monkeypatch) -> None:
    _patch_open_page(monkeypatch, _Page(CHECK_URL, json.dumps({"status": 200, "text": "{}"})))

    result = await server._cian_selfcheck_impl(None)

    assert result.checks["search"].reason == "challenge_required"


@pytest.mark.req("R-CIAN-SEARCH-BOT-CHECK")
async def test_a_search_tab_on_the_site_still_posts_the_query(monkeypatch) -> None:
    page = _Page(HOME_URL, json.dumps({"status": 200, "text": '{"data": {}}'}))
    _patch_open_page(monkeypatch, page)

    assert await server._cdp_post_json({"_type": "flatsale"}, None) == (200, '{"data": {}}')
    assert len(page.evaluated) == 1


class _BoundSpy:
    """Delegates to asyncio, recording every bound the read runs under.

    R-CDP-QUEUE-CREDIT moved the bound out of the connector module: the read
    now runs under mcp_core's ``bounded_attempt``, so the timeout is applied
    there and not in the connector's own ``asyncio.wait_for`` call. Both
    modules are patched with this one spy, so the recorded list is every bound
    the read had — the claim under test is unchanged.
    """

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def __getattr__(self, name: str) -> object:
        return getattr(asyncio, name)

    async def wait_for(self, awaitable, timeout):
        self.timeouts.append(timeout)
        return await asyncio.wait_for(awaitable, timeout)

    def timeout(self, delay):
        self.timeouts.append(delay)
        return asyncio.timeout(delay)


@pytest.mark.req("R-SETTINGS-WIRED.cian-read")
@pytest.mark.parametrize("read", ["search", "card"])
async def test_cian_timeout_is_the_only_bound_on_the_read(monkeypatch, read: str) -> None:
    spy = _BoundSpy()
    monkeypatch.setattr(server, "asyncio", spy)
    monkeypatch.setattr(attempt_deadline, "asyncio", spy)
    monkeypatch.setattr(server, "TIMEOUT", 60.0)
    if read == "search":
        _patch_open_page(monkeypatch, _Page(HOME_URL, json.dumps({"status": 200, "text": "{}"})))
        await server._cdp_post_json({}, None)
    else:
        _patch_open_page(monkeypatch, _Page(HOME_URL, '{"kind": "ok"}'))
        await server._cdp_page_config(f"{HOME_URL}sale/flat/1/", None)

    assert spy.timeouts == [60.0]
