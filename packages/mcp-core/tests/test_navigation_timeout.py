"""Requirement-derived public navigation-budget tests for owner issue #190.

START_MODULE_CONTRACT
PURPOSE: Check default/global/source ms budgets and bounded raw navigation.
SCOPE: Real open_page and transport lifecycle, native browser/socket I/O doubles.
DEPENDS: chrome_cdp; real NavigationBudget; asyncio; pytest; native CDP JSON.
LINKS: M-CHROME-CDP; R-CDP-NAV-TIMEOUT; R-CDP-COMMAND-DEADLINE; R-OFFLINE.
SOURCE: Owner task #190, 2026-11-03; assertions authored before fixed-source inspection.
END_MODULE_CONTRACT
START_MODULE_MAP
_configured_core: Load genuine env configuration and isolate module identities.
_install_playwright: Supply native owned/foreign pages below public open_page.
_install_raw: Force CdpConnectTimeout and fake only raw websocket/discovery I/O.
_requirement tests: Exercise budgets, invalid knobs, host/status guards and cleanup.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from mcp_core.transport.cdp_budget import NavigationBudget
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

URL = "https://shop.example/navigation"
OWNED_TARGET = "issue190-owned"
OMITTED = object()
NAVIGATION_MS = 250
SHARED_LEG_DELAY_S = 0.16
WATCHDOG_S = 2.0

# START_BLOCK_ENV_AND_NATIVE_PLAYWRIGHT_IO


@contextmanager
def _configured_core(monkeypatch, global_ms=None):
    from mcp_core.transport import chrome_cdp as cdp

    original = vars(cdp).copy()
    try:
        with monkeypatch.context() as patches:
            if global_ms is None:
                patches.delenv("CHROME_NAV_TIMEOUT_MS", raising=False)
            else:
                patches.setenv("CHROME_NAV_TIMEOUT_MS", global_ms)
            patches.setenv("CHROME_STEALTH", "0")
            importlib.reload(cdp)
            budget = NavigationBudget(max_concurrent=1, per_host=1)
            patches.setattr(cdp, "navigation_budget", lambda: budget)
            yield cdp, patches, budget
    finally:
        # Avoid leaving reloaded exception identities behind for existing tests.
        vars(cdp).clear()
        vars(cdp).update(original)


class _Page:
    def __init__(self, *, status=200, final_url=None, fault=None):
        self.status = status
        self.final_url = final_url
        self.fault = fault
        self.url = "about:blank"
        self.goto_calls = []
        self.close_calls = 0
        self.routes = []

    async def goto(self, url, *, timeout, wait_until):
        self.goto_calls.append({"url": url, "timeout": timeout, "wait_until": wait_until})
        if self.fault is not None:
            raise self.fault
        self.url = self.final_url or url
        return SimpleNamespace(status=self.status)

    def expect_navigation(self, **_kwargs):
        raise PlaywrightTimeoutError("native 403 page never reloads")

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def close(self):
        self.close_calls += 1


def _install_playwright(patches, cdp, **page_options):
    owned = _Page(**page_options)
    foreign = _Page()
    context = SimpleNamespace(browser=None, pages=[foreign], routes=[])

    async def new_page():
        context.pages.append(owned)
        return owned

    async def route(pattern, handler):
        context.routes.append((pattern, handler))

    context.new_page = new_page
    context.route = route

    @asynccontextmanager
    async def native_context():
        yield context

    patches.setattr(cdp, "get_context", native_context)
    return SimpleNamespace(owned=owned, foreign=foreign, context=context)


def _open(cdp, source_ms=OMITTED):
    if source_ms is OMITTED:
        return cdp.open_page(URL, wait_ms=0)
    return cdp.open_page(URL, wait_ms=0, navigation_timeout_ms=source_ms)


def _assert_page_cleanup(native):
    assert native.owned.close_calls == 1
    assert native.foreign.close_calls == 0
    assert native.owned.routes == []
    assert native.context.routes == []


# END_BLOCK_ENV_AND_NATIVE_PLAYWRIGHT_IO
# START_BLOCK_NATIVE_RAW_IO


class _Socket:
    """Native CDP double: real raw code sends/correlates every JSON command."""

    def __init__(self, channel, commands, *, mode="immediate", status=200, final_url=None):
        self.channel = channel
        self.commands = commands
        self.mode = mode
        self.status = status
        self.final_url = final_url
        self.messages = []
        self.navigation = False
        self.navigation_recv_calls = 0
        self.send_cancelled = False
        self.recv_cancelled = False
        self.closed = False

    async def send(self, raw):
        command = json.loads(raw)
        self.commands.append((self.channel, command))
        method = command["method"]
        if method == "Page.navigate":
            self.navigation = True
            try:
                if self.mode == "blocked-send":
                    await asyncio.Event().wait()
                elif self.mode == "shared-send-recv":
                    await asyncio.sleep(SHARED_LEG_DELAY_S)
            except asyncio.CancelledError:
                self.send_cancelled = True
                raise
        result = {}
        if method == "Target.createTarget":
            result = {"targetId": OWNED_TARGET}
        elif method == "Target.closeTarget":
            result = {"success": True}
        elif method == "Page.navigate":
            result = {"frameId": "main"}
        self.messages.append({"id": command["id"], "result": result})
        if method == "Page.navigate":
            self.messages.extend(
                [
                    {"method": "Network.dataReceived", "params": {}},
                    {
                        "method": "Network.responseReceived",
                        "params": {
                            "type": "Document",
                            "frameId": "main",
                            "response": {"status": self.status, "url": self.final_url or command["params"]["url"]},
                        },
                    },
                    {
                        "method": "Network.responseReceived",
                        "params": {
                            "type": "Document",
                            "frameId": "foreign-iframe",
                            "response": {"status": 200, "url": "https://foreign.example/iframe"},
                        },
                    },
                    {"method": "Page.domContentEventFired", "params": {}},
                    {"method": "Page.loadEventFired", "params": {}},
                ]
            )

    async def recv(self):
        if self.navigation:
            self.navigation_recv_calls += 1
            if self.mode == "shared-send-recv" and self.navigation_recv_calls == 1:
                try:
                    await asyncio.sleep(SHARED_LEG_DELAY_S)
                except asyncio.CancelledError:
                    self.recv_cancelled = True
                    raise
        if not self.messages:
            raise AssertionError("native socket received before any completed send")
        return json.dumps(self.messages.pop(0))


def _install_raw(patches, cdp, *, mode="immediate", status=200, final_url=None):
    commands = []
    browser = _Socket("browser", commands)
    page = _Socket("page", commands, mode=mode, status=status, final_url=final_url)
    attempts = []

    @asynccontextmanager
    async def failed_attach():
        attempts.append("CdpConnectTimeout")
        raise cdp._CdpConnectTimeout("native attach timeout forces the real raw fallback")
        yield  # pragma: no cover -- declares an async context manager

    @asynccontextmanager
    async def socket(url, **_kwargs):
        connection = browser if "/devtools/browser/" in url else page
        if connection is page and not url.endswith(f"/devtools/page/{OWNED_TARGET}"):
            raise AssertionError(f"native socket attached to an unowned target: {url}")
        try:
            yield connection
        finally:
            connection.closed = True

    targets = [
        {"id": "foreign-user-tab", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/foreign"},
        {"id": OWNED_TARGET, "webSocketDebuggerUrl": f"ws://127.0.0.1:9222/devtools/page/{OWNED_TARGET}"},
    ]
    patches.setattr(cdp, "get_context", failed_attach)
    patches.setattr(cdp, "_browser_ws_url", lambda: "ws://127.0.0.1:9222/devtools/browser/native")
    patches.setattr(cdp, "_websockets", SimpleNamespace(connect=socket))
    patches.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: BytesIO(json.dumps(targets).encode()))
    return SimpleNamespace(commands=commands, browser=browser, page=page, attempts=attempts)


def _assert_raw_cleanup(native):
    closes = [
        (channel, command["params"])
        for channel, command in native.commands
        if command["method"] == "Target.closeTarget"
    ]
    assert closes == [("browser", {"targetId": OWNED_TARGET})]
    assert native.browser.closed is True
    assert native.page.closed is True
    assert native.attempts == ["CdpConnectTimeout"]
    assert not any(command["method"] == "Network.setBlockedURLs" for _, command in native.commands)


# END_BLOCK_NATIVE_RAW_IO
# START_BLOCK_REQUIREMENT_TESTS


@pytest.mark.parametrize(
    ("global_ms", "source_ms", "expected_ms"),
    [
        (None, OMITTED, 20_000),
        ("65432", OMITTED, 65_432),
        ("65432", None, 65_432),
        ("65432", 90_000, 90_000),
        ("65432", 1250.5, 1250.5),
        ("NaN", 3210, 3210),
    ],
    ids=["default20k", "global-ms", "optional-none", "source-wins", "fractional-ms", "unused-invalid-global"],
)
async def test_public_open_page_passes_default_global_or_source_ms(monkeypatch, global_ms, source_ms, expected_ms):
    """R-CDP-NAV-TIMEOUT: optional keyword budget reaches actual page.goto."""
    with _configured_core(monkeypatch, global_ms) as (cdp, patches, _budget):
        native = _install_playwright(patches, cdp)
        async with _open(cdp, source_ms) as page:
            assert page is native.owned
            assert native.owned.close_calls == 0
        assert [call["timeout"] for call in native.owned.goto_calls] == [expected_ms]
        assert [call["wait_until"] for call in native.owned.goto_calls] == ["domcontentloaded"]
        _assert_page_cleanup(native)


@pytest.mark.parametrize("source_ms", [0, -1, float("nan"), float("inf"), 5e-324])
async def test_invalid_source_budget_is_rejected_before_browser_io(monkeypatch, source_ms):
    """R-CDP-NAV-TIMEOUT: zero/negative/NaN/infinity are not unlimited waits.

    5e-324 ms is a positive float that underflows to zero raw-CDP seconds, so
    it must be refused too: a budget that cannot be represented as seconds is
    not a usable limit.
    """
    with _configured_core(monkeypatch) as (cdp, patches, _budget):
        native = _install_playwright(patches, cdp)
        with pytest.raises(ValueError):
            async with _open(cdp, source_ms):
                pytest.fail("invalid navigation budget yielded a page")
        assert native.owned.goto_calls == []
        assert native.context.pages == [native.foreign]


@pytest.mark.parametrize("global_ms", ["0", "NaN", "inf", "5e-324"])
async def test_invalid_global_budget_is_rejected_before_navigation(monkeypatch, global_ms):
    """R-CDP-NAV-TIMEOUT: genuine invalid global env configuration rejects."""
    native = None
    with pytest.raises(ValueError):
        with _configured_core(monkeypatch, global_ms) as (cdp, patches, _budget):
            native = _install_playwright(patches, cdp)
            async with _open(cdp):
                pytest.fail("invalid global navigation budget yielded a page")
    if native is not None:
        assert native.owned.goto_calls == []


@pytest.mark.parametrize("status", [403, 429])
async def test_status_guard_and_owned_cleanup_survive_timeout_change(monkeypatch, status):
    """R-CDP-NAV-TIMEOUT: block statuses are not returned as successful pages."""
    with _configured_core(monkeypatch) as (cdp, patches, _budget):
        native = _install_playwright(patches, cdp, status=status)
        with pytest.raises(cdp.NavBlocked) as error:
            async with _open(cdp):
                pytest.fail("blocked document yielded a page")
        assert error.value.status == status
        _assert_page_cleanup(native)


async def test_host_guard_still_closes_only_the_owned_page(monkeypatch):
    """R-CDP-NAV-TIMEOUT: redirect policy is independent of navigation budget."""
    with _configured_core(monkeypatch) as (cdp, patches, _budget):
        native = _install_playwright(patches, cdp, final_url="https://foreign.example/redirect")
        with pytest.raises(cdp.NavigationPolicyError):
            async with _open(cdp):
                pytest.fail("off-host redirect yielded a page")
        _assert_page_cleanup(native)


async def test_navigation_timeout_closes_owned_page_without_extraction(monkeypatch):
    """R-CDP-NAV-TIMEOUT: ordinary goto timeout propagates after owned cleanup."""
    with _configured_core(monkeypatch) as (cdp, patches, _budget):
        native = _install_playwright(patches, cdp, fault=PlaywrightTimeoutError("native goto timed out"))
        with pytest.raises(PlaywrightTimeoutError, match="native goto timed out"):
            async with _open(cdp):
                pytest.fail("timed-out navigation yielded a page")
        _assert_page_cleanup(native)


@pytest.mark.parametrize("mode", ["blocked-send", "shared-send-recv"])
async def test_forced_raw_fallback_uses_source_budget_for_send_and_recv(monkeypatch, mode):
    """R-CDP-NAV-TIMEOUT/R-CDP-COMMAND-DEADLINE: one shared navigate deadline.

    A blocked send must expire. Two 160ms legs individually fit in 250ms, but
    together cannot; resetting the recv budget after send must fail this test.
    The 2s watchdog is only a deadlock guard and its own TimeoutError is not a pass.

    Scope correction (2026-11-03, baseline 908453 semantics): the raw path
    returns the last main-document status when its deadline expires and has no
    status to report. A blocked send raises before any event, so the fallback
    fails as transport_down. An expired receive leaves the page blank, and the
    unchanged host guard refuses it as a navigation-policy error. Both are the
    published behaviour; this test pins the shared bound, the cancellation and
    the owned cleanup, and it does not invent a new receive-timeout taxonomy.
    """
    with _configured_core(monkeypatch, "10000") as (cdp, patches, _budget):
        native = _install_raw(patches, cdp, mode=mode)

        async def enter():
            async with _open(cdp, NAVIGATION_MS):
                pytest.fail("expired raw navigation yielded a page")

        started = time.monotonic()
        if mode == "blocked-send":
            with pytest.raises(cdp.TransportDownError) as error:
                await asyncio.wait_for(enter(), timeout=WATCHDOG_S)
            assert isinstance(error.value.__cause__, TimeoutError)
        else:
            with pytest.raises(cdp.NavigationPolicyError) as error:
                await asyncio.wait_for(enter(), timeout=WATCHDOG_S)
            assert error.value.__cause__ is None
            assert error.value.final_url == "about:blank"
        assert time.monotonic() - started < WATCHDOG_S * 0.75
        if mode == "blocked-send":
            assert native.page.send_cancelled is True
            assert native.page.navigation_recv_calls == 0
        else:
            assert native.page.send_cancelled is False
            assert native.page.recv_cancelled is True
        _assert_raw_cleanup(native)


async def test_raw_fallback_uses_global_budget_when_source_is_omitted(monkeypatch):
    """R-CDP-NAV-TIMEOUT: global milliseconds also bound raw outbound navigate."""
    with _configured_core(monkeypatch, str(NAVIGATION_MS)) as (cdp, patches, _budget):
        native = _install_raw(patches, cdp, mode="blocked-send")

        async def enter():
            async with _open(cdp):
                pytest.fail("expired global raw navigation yielded a page")

        with pytest.raises(cdp.TransportDownError) as error:
            await asyncio.wait_for(enter(), timeout=WATCHDOG_S)
        assert isinstance(error.value.__cause__, TimeoutError)
        assert native.page.send_cancelled is True
        _assert_raw_cleanup(native)


@pytest.mark.parametrize("outcome", ["success", "blocked", "off-host"])
async def test_raw_fallback_controls_keep_status_host_policy_and_owned_cleanup(monkeypatch, outcome):
    """R-CDP-NAV-TIMEOUT: immediate/noisy raw control retains existing guards."""
    with _configured_core(monkeypatch) as (cdp, patches, _budget):
        native = _install_raw(
            patches,
            cdp,
            status=429 if outcome == "blocked" else 200,
            final_url="https://foreign.example/redirect" if outcome == "off-host" else None,
        )
        if outcome == "success":
            async with _open(cdp) as page:
                assert page.url == URL
                assert not any(command["method"] == "Target.closeTarget" for _, command in native.commands)
        else:
            expected_error = cdp.NavBlocked if outcome == "blocked" else cdp.NavigationPolicyError
            with pytest.raises(expected_error):
                async with _open(cdp):
                    pytest.fail("raw guard refused to reject the document")
        navigations = [
            command["params"]["url"] for _, command in native.commands if command["method"] == "Page.navigate"
        ]
        assert navigations == [URL]
        _assert_raw_cleanup(native)


# END_BLOCK_REQUIREMENT_TESTS
