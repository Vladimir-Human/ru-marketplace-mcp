"""Loading-grid timing/error regressions, using fake owned pages and real product values.

The fake payload transitions model the owner's reported 加载中 state; product
values come from the supplied capture. The login-wall case mirrors the captured
wall's structural markers (empty title, 32 links, 2 member-login routes); the
captured wall itself is exercised by the jsdom test
(test_search_tmall_live_dom.py). No Chrome, no jsdom, no marketplace traffic.
"""
# START_MODULE_CONTRACT
# PURPOSE: Prove bounded same-page loading polls and challenge/timeout/drift precedence.
# SCOPE: Public search/selfcheck with fake pages and short poll budgets; offline payloads only.
# DEPENDS: pytest; asyncio; mcp_core cache; taobao_connector.server; captured provenance.
# LINKS: M-TAOBAO; R-TAOBAO-GRID-LOADING; R-ERRORS; R-TRANSPORT; R-OFFLINE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# FakePage: Supply deterministic render transitions or a cancellable stalled evaluation.
# test_loading_then_items_returns_items_in_the_same_page: Poll to products without another navigation.
# test_loading_at_deadline_is_retryable_timeout: Never classify unfinished rendering as parser drift.
# test_stalled_loading_evaluation_respects_deadline: Bound evaluation and close the owned page.
# test_login_wall_with_loading_never_waits: Prioritize the wall over loading.
# test_challenge_with_loading_never_waits: Prioritize titled login/CAPTCHA evidence.
# test_non_loading_empty_grid_is_parser_drift: Preserve a genuine empty extraction's verdict.
# test_selfcheck_loading_at_deadline_is_inconclusive_timeout: Do not diagnose unfinished rendering as drift.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from mcp_core.cache import TTLCache
from taobao_connector import server

FIXTURES = Path(__file__).parent / "fixtures"
PROVENANCE = json.loads((FIXTURES / "search_grid_iphone15_live.provenance.json").read_text(encoding="utf-8"))
CATALOG = {
    "title": "iphone 15_淘宝搜索",
    "anchors_total": 16,
    "login_anchors": 0,
    "body_snippet": "iphone 15_淘宝搜索",
    "grid_loading": False,
    "items": [it for it in PROVENANCE["ground_truth"]["items"] if it["kind"] != "simba_ad"],
}
LOADING = {**CATALOG, "items": [], "body_snippet": "加载中...", "grid_loading": True}
# The captured 2026-09-10 login wall: empty document title, 32 links, two
# member/login routes — and the same «加载中...» grid marker.
WALL = {
    "title": "",
    "anchors_total": 32,
    "login_anchors": 2,
    "body_snippet": "加载中...",
    "grid_loading": True,
    "items": [],
}


# START_BLOCK_FAKE_PAGE
class FakePage:
    def __init__(self, payloads, *, stall_after=None):
        self.payloads = payloads
        self.stall_after = stall_after
        self.reads = 0
        self.opens = 0
        self.closes = 0
        self.cancelled = False

    async def evaluate(self, expression):
        assert expression == server._SEARCH_EXTRACT_JS
        self.reads += 1
        if self.stall_after is not None and self.reads > self.stall_after:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return json.dumps(self.payloads[min(self.reads - 1, len(self.payloads) - 1)], ensure_ascii=False)


def _patch_page(monkeypatch, page):
    @asynccontextmanager
    async def open_mock(url, wait_ms):
        assert url.startswith("https://s.taobao.com/search?")
        page.opens += 1
        try:
            yield page
        finally:
            page.closes += 1

    monkeypatch.setattr(server, "open_page", open_mock)


@pytest.fixture(autouse=True)
def _fast_isolated_state(monkeypatch):
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=300))
    # raising=False lets these requirement-derived tests reach the old public
    # zero-item path too, rather than fail merely because the new seams are absent.
    monkeypatch.setattr(server, "_GRID_POLL_INTERVAL_S", 0.002, raising=False)
    monkeypatch.setattr(server, "_GRID_LOADING_TIMEOUT_S", 0.06, raising=False)

    async def no_wait():
        pass

    monkeypatch.setattr(server, "_polite_wait", no_wait)


# END_BLOCK_FAKE_PAGE


# START_BLOCK_LOADING_VERDICTS
@pytest.mark.parametrize("remaining_loading_marker", [False, True])
async def test_loading_then_items_returns_items_in_the_same_page(monkeypatch, remaining_loading_marker):
    """R-TAOBAO-GRID-LOADING: items win even if an old loading label still remains."""
    page = FakePage([LOADING, {**CATALOG, "grid_loading": remaining_loading_marker}])
    _patch_page(monkeypatch, page)
    result = await server.taobao_search("iphone 15")
    assert result.status == "success"
    assert result.count == 4
    assert result.items[0].price_cny == CATALOG["items"][0]["price_cny"]
    assert page.reads == 2
    assert page.opens == page.closes == 1
    assert len(server._cache) == 1


async def test_loading_at_deadline_is_retryable_timeout(monkeypatch):
    """R-TAOBAO-GRID-LOADING, R-ERRORS: a loading grid expires as UpstreamTimeoutError on the wire."""
    page = FakePage([LOADING])
    _patch_page(monkeypatch, page)
    with pytest.raises(ToolError) as excinfo:
        await asyncio.wait_for(server.taobao_search("iphone 15"), timeout=1.0)
    error = json.loads(str(excinfo.value))
    assert error["error"] == "timeout"
    assert error["retryable"] is True
    assert error["provider"] == "taobao"
    assert "loading" in error["message"].lower()
    assert "retry" in error["message"].lower()
    assert page.reads > 1
    assert page.opens == page.closes == 1
    assert len(server._cache) == 0


async def test_stalled_loading_evaluation_respects_deadline(monkeypatch):
    """R-TAOBAO-GRID-LOADING, R-TRANSPORT: the extra deadline also bounds a stuck poll evaluation."""
    page = FakePage([LOADING], stall_after=1)
    _patch_page(monkeypatch, page)
    with pytest.raises(ToolError) as excinfo:
        await asyncio.wait_for(server.taobao_search("iphone 15"), timeout=1.0)
    assert json.loads(str(excinfo.value))["error"] == "timeout"
    assert page.cancelled is True
    assert page.reads == 2
    assert page.opens == page.closes == 1
    assert len(server._cache) == 0


async def test_login_wall_with_loading_never_waits(monkeypatch):
    """R-TAOBAO-GRID-LOADING: the wall's 加载中 must never initiate polling."""
    page = FakePage([WALL, CATALOG])
    _patch_page(monkeypatch, page)
    with pytest.raises(ToolError) as excinfo:
        await server.taobao_search("iphone 15")
    error = json.loads(str(excinfo.value))
    assert error["error"] == "challenge_required"
    assert error["requires_user_action"] is True
    assert page.reads == 1
    assert page.opens == page.closes == 1
    assert len(server._cache) == 0


@pytest.mark.parametrize(
    "challenge",
    [
        {"title": "登录-淘宝网", "body_snippet": "加载中..."},
        {"title": "淘宝网", "body_snippet": "CAPTCHA 加载中..."},
    ],
    ids=["login", "captcha"],
)
async def test_challenge_with_loading_never_waits(monkeypatch, challenge):
    """R-TAOBAO-GRID-LOADING: positive login/CAPTCHA evidence takes precedence on the first read."""
    page = FakePage([{**LOADING, **challenge}, CATALOG])
    _patch_page(monkeypatch, page)
    with pytest.raises(ToolError) as excinfo:
        await server.taobao_search("iphone 15")
    assert json.loads(str(excinfo.value))["error"] == "challenge_required"
    assert page.reads == 1
    assert page.opens == page.closes == 1
    assert len(server._cache) == 0


async def test_non_loading_empty_grid_is_parser_drift(monkeypatch):
    """R-TAOBAO-GRID-LOADING: no loading/wall/challenge evidence leaves genuine zero-item drift."""
    page = FakePage([{**CATALOG, "items": []}])
    _patch_page(monkeypatch, page)
    with pytest.raises(ToolError) as excinfo:
        await server.taobao_search("iphone 15")
    error = json.loads(str(excinfo.value))
    assert error["error"] == "parser_drift"
    assert error["retryable"] is False
    assert page.reads == 1
    assert page.opens == page.closes == 1
    assert len(server._cache) == 0


async def test_selfcheck_loading_at_deadline_is_inconclusive_timeout(monkeypatch):
    """R-TAOBAO-GRID-LOADING: an unfinished selfcheck grid is inconclusive, not drift_detected."""
    page = FakePage([LOADING])
    _patch_page(monkeypatch, page)
    result = await asyncio.wait_for(server.taobao_selfcheck(), timeout=1.0)
    assert result.status == "inconclusive"
    assert result.healthy is None
    assert result.checks["search"].reason == "timeout"
    assert page.reads > 1
    assert page.opens == page.closes == 1


# END_BLOCK_LOADING_VERDICTS
