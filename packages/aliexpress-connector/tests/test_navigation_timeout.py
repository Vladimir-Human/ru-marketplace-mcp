"""Independent issue #190 regressions; only browser I/O is doubled.

START_MODULE_CONTRACT
PURPOSE: Prove the env-loaded AliExpress budget reaches real navigation through MCP.
SCOPE: Offline native browser doubles; existing captured grid and real extractor.
DEPENDS: FastMCP Client; chrome_cdp; AliSettings; existing domtest/jsdom and captures.
LINKS: M-ALIEXPRESS; M-CHROME-CDP; R-CDP-NAV-TIMEOUT; R-OFFLINE; R-FIXTURES.
SOURCE: Owner task #190, 2026-11-03; assertions derived before reading fixed sources.
END_MODULE_CONTRACT
START_MODULE_MAP
_loaded_connector: Reload genuine env settings without leaking modules into other tests.
_BrowserContext: Supply owned landing/popup handles, never a real browser session.
_Landing: Model instant readiness at 21746ms; execute actual JS on captured markup.
_test helpers: Call the real MCP tool and check extraction, budget, and owned cleanup.
END_MODULE_MAP
"""

from __future__ import annotations

import importlib
import json
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp_core.domtest import jsdom_available, run_extractor
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

READINESS_MS = 21_746
ITEM_ID = "1005010003103368"
ITEM_URL = f"https://aliexpress.ru/item/{ITEM_ID}.html"
GRID_CAPTURE = Path(__file__).parent / "fixtures" / "search_grid.html"

# START_BLOCK_NATIVE_BROWSER_IO


class _Popup:
    """Explicit native page double, not invented HTML or a claimed live card."""

    def __init__(self, opener=None):
        self.url = ITEM_URL
        self._opener = opener
        self.close_calls = 0
        self.reads = 0

    async def opener(self):
        return self._opener

    async def evaluate(self, _expression):
        self.reads += 1
        return json.dumps(
            {
                "punish": False,
                "title": "Native card test double",
                "price_texts": {"attached": ["123 ₽"], "other": []},
                "url": ITEM_URL,
            }
        )

    async def close(self):
        self.close_calls += 1


class _Landing:
    def __init__(self, context, readiness_ms):
        self.context = context
        self.readiness_ms = readiness_ms
        self.url = "about:blank"
        self.goto_calls = []
        self.extract_calls = 0
        self.close_calls = 0
        self.listeners = []
        self.routes = []

    async def goto(self, url, *, timeout, wait_until):
        self.goto_calls.append({"url": url, "timeout": timeout, "wait_until": wait_until})
        if timeout < self.readiness_ms:
            raise PlaywrightTimeoutError(
                f"Page.goto: Timeout {timeout:g}ms exceeded; "
                f"native test page needs {self.readiness_ms}ms before domcontentloaded"
            )
        self.url = url
        return SimpleNamespace(status=200)

    async def wait_for_timeout(self, _milliseconds):
        # Browser-side settle I/O is instantaneous in this double. It cannot
        # rescue a goto that failed: the readiness check above runs first.
        return None

    async def evaluate(self, expression):
        if "window.open(" in expression:
            popup = _Popup(self)
            self.context.owned_popup = popup
            self.context.pages.append(popup)
            for callback in tuple(self.listeners):
                callback(popup)
            return "opened"
        self.extract_calls += 1
        # Actual extractor, unmodified existing captured fixture, no browser or
        # remote resource execution. Budget tests must not skip this evidence.
        return json.dumps(run_extractor(expression, GRID_CAPTURE, page_url=self.url))

    def on(self, event, callback):
        if event != "popup":
            raise AssertionError(f"unexpected native page event: {event}")
        self.listeners.append(callback)

    def remove_listener(self, event, callback):
        if event != "popup":
            raise AssertionError(f"unexpected native page event: {event}")
        self.listeners.remove(callback)

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def close(self):
        self.close_calls += 1


class _BrowserContext:
    browser = None

    def __init__(self, readiness_ms):
        self.foreign = _Popup()  # Existing same-item tab is not ours.
        self.pages = [self.foreign]
        self.owned_popup = None
        self.landing = _Landing(self, readiness_ms)
        self.routes = []

    async def new_page(self):
        self.pages.append(self.landing)
        return self.landing

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))


# END_BLOCK_NATIVE_BROWSER_IO
# START_BLOCK_ENV_AND_MCP_BOUNDARY


@contextmanager
def _loaded_connector(monkeypatch, context, *, timeout_seconds, global_ms="1700"):
    from aliexpress_connector import settings
    from mcp_core.transport import chrome_cdp as cdp

    old_core = vars(cdp).copy()
    previous_server = sys.modules.get("aliexpress_connector.server")
    old_server = vars(previous_server).copy() if previous_server is not None else None
    server = None
    try:
        with monkeypatch.context() as patches:
            if timeout_seconds is None:
                patches.delenv("ALI_TIMEOUT", raising=False)
            else:
                patches.setenv("ALI_TIMEOUT", timeout_seconds)
            patches.setenv("ALI_MIN_GAP", "0")
            patches.setenv("ALI_CACHE_TTL", "0")
            patches.setenv("CHROME_NAV_TIMEOUT_MS", global_ms)
            patches.setenv("CHROME_STEALTH", "0")
            settings.get_settings.cache_clear()
            importlib.reload(cdp)
            # ALI_TIMEOUT is already in os.environ before server import/reload.
            server = importlib.import_module("aliexpress_connector.server")
            if previous_server is not None:
                importlib.reload(server)

            @asynccontextmanager
            async def native_context():
                yield context

            patches.setattr(cdp, "get_context", native_context)
            yield server
    finally:
        settings.get_settings.cache_clear()
        # Reload isolation preserves exception/function identity for old tests
        # that imported the canonical modules during collection.
        vars(cdp).clear()
        vars(cdp).update(old_core)
        if previous_server is not None:
            vars(previous_server).clear()
            vars(previous_server).update(old_server)
        elif server is not None:
            sys.modules.pop("aliexpress_connector.server", None)
            package = sys.modules["aliexpress_connector"]
            if getattr(package, "server", None) is server:
                delattr(package, "server")


async def _call(server, name):
    arguments = {"query": "realme watch"} if name == "aliexpress_search" else {"item_id_or_url": ITEM_ID}
    async with Client(server.mcp) as client:
        return await client.call_tool(name, arguments, raise_on_error=False)


def _assert_extracted_grid(result):
    """Assert the captured prices — this part needs the optional DOM harness.

    jsdom is an optional developer tool in this repository: without it the real
    extractor cannot run at all, and every other DOM-level check skips. The
    navigation contract is asserted before this helper, so it holds on any
    machine; only the price comparison skips.
    """
    if not jsdom_available():
        assert result.is_error is True, "the search must not fail before the extraction"
        assert "jsdom not installed" in str(result.content), (
            "without the DOM harness the only acceptable failure is the missing jsdom, "
            f"not a navigation timeout: {result.content}"
        )
        pytest.skip("jsdom is not installed; the captured-price assertion needs it")
    assert result.is_error is False, str(result.content)
    data = result.structured_content
    assert data is not None
    assert data["tier_used"] == "cdp"
    assert data["count"] > 0
    realme = next(item for item in data["items"] if item["item_id"] == ITEM_ID)
    # Ground truth: existing search_grid.provenance.json (capture 2026-08-20).
    assert realme["price_rub"] == 2939.0
    assert realme["old_price_rub"] == 6967.0


def _assert_owned_cleanup(context):
    assert context.landing.close_calls == 1
    assert context.foreign.close_calls == 0
    assert context.landing.routes == []
    assert context.routes == []


# END_BLOCK_ENV_AND_MCP_BOUNDARY
# START_BLOCK_REQUIREMENT_REGRESSIONS


async def test_mcp_search_uses_env_timeout_past_reported_readiness(monkeypatch):
    """R-CDP-NAV-TIMEOUT: genuine ALI_TIMEOUT=90 passes 90000ms to goto.

    Native goto fails instantly below the reporter's measured 21746ms threshold.
    The old 20000ms failure occurs before JS extraction; no 21-second sleep and
    no replacement of open_page, the render function, the tool, or TIMEOUT.
    """
    context = _BrowserContext(READINESS_MS)
    with _loaded_connector(monkeypatch, context, timeout_seconds="90") as server:
        result = await _call(server, "aliexpress_search")
        print(
            "[R-CDP-NAV-TIMEOUT] "
            + json.dumps(
                {
                    "tool": "aliexpress_search",
                    "env_ali_timeout_s": 90,
                    "readiness_ms": READINESS_MS,
                    "goto": context.landing.goto_calls,
                    "extract_calls": context.landing.extract_calls,
                    "owned_close_calls": context.landing.close_calls,
                    "foreign_close_calls": context.foreign.close_calls,
                    "mcp_is_error": result.is_error,
                }
            )
        )
        # The navigation contract comes first: it must hold on every machine,
        # with or without the optional DOM harness.
        assert [call["timeout"] for call in context.landing.goto_calls] == [90_000]
        assert [call["wait_until"] for call in context.landing.goto_calls] == ["domcontentloaded"]
        assert context.landing.extract_calls == 1
        _assert_owned_cleanup(context)
        _assert_extracted_grid(result)


@pytest.mark.parametrize(
    ("tool", "timeout_seconds", "expected_ms"),
    [
        ("aliexpress_search", None, 45_000),
        ("aliexpress_card", None, 45_000),
        ("aliexpress_card", "90", 90_000),
    ],
    ids=["search-default45s", "landing-default45s", "landing-env90s"],
)
async def test_search_and_card_landing_reuse_existing_ali_timeout(monkeypatch, tool, timeout_seconds, expected_ms):
    """R-CDP-NAV-TIMEOUT: source seconds beat the smaller global ms budget."""
    context = _BrowserContext(0)
    with _loaded_connector(monkeypatch, context, timeout_seconds=timeout_seconds) as server:
        result = await _call(server, tool)
        assert [call["timeout"] for call in context.landing.goto_calls] == [expected_ms]
        assert [call["wait_until"] for call in context.landing.goto_calls] == ["domcontentloaded"]
        # A card still navigates only its search landing, never directly to /item/.
        assert all("/wholesale" in call["url"] for call in context.landing.goto_calls)
        _assert_owned_cleanup(context)
        if tool == "aliexpress_search":
            _assert_extracted_grid(result)
        else:
            assert result.is_error is False, str(result.content)
            assert result.structured_content["item_id"] == ITEM_ID
            assert result.structured_content["title"] == "Native card test double"
            assert context.owned_popup.reads == 1
            assert context.owned_popup.close_calls == 1
            assert context.landing.listeners == []


async def test_short_source_budget_still_times_out_and_closes_owned_landing(monkeypatch):
    """R-CDP-NAV-TIMEOUT: global 90000ms cannot rescue explicit ALI_TIMEOUT=1."""
    context = _BrowserContext(READINESS_MS)
    with _loaded_connector(monkeypatch, context, timeout_seconds="1", global_ms="90000") as server:
        result = await _call(server, "aliexpress_search")
        assert result.is_error is True
        assert "Page.goto: Timeout 1000ms exceeded" in str(result.content)
        assert [call["timeout"] for call in context.landing.goto_calls] == [1000]
        assert context.landing.extract_calls == 0
        _assert_owned_cleanup(context)


@pytest.mark.parametrize("timeout_seconds", ["0", "NaN", "inf"])
async def test_invalid_ali_timeout_never_reaches_browser_navigation(monkeypatch, timeout_seconds):
    """R-CDP-NAV-TIMEOUT: invalid source settings reject at load or at the tool."""
    context = _BrowserContext(0)
    result = None
    try:
        with _loaded_connector(monkeypatch, context, timeout_seconds=timeout_seconds) as server:
            result = await _call(server, "aliexpress_search")
    except ValueError:
        # Pydantic configuration rejection at import is also a valid rejection.
        pass
    assert result is None or result.is_error is True
    assert context.landing.goto_calls == []
    assert context.landing.extract_calls == 0
    assert context.foreign.close_calls == 0


# END_BLOCK_REQUIREMENT_REGRESSIONS
