"""Owned navigation with a parsed DOM but no DOMContentLoaded/load event.

All browser I/O is replaced; these tests prove the transport shell, not markup.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from mcp_core.transport import chrome_cdp

URL = "https://www.citilink.ru/search/?text=laptop"


def _browser_doubles(state):
    """Model readiness and main-frame response/commit events, never page markup."""

    def parsed():
        state.parsed_reads += 1
        value = state.ready[0]
        if len(state.ready) > 1:
            state.ready.pop(0)
        return value in {"interactive", "complete"}

    class Page:
        url = URL

        async def goto(self, url, *, wait_until, timeout):
            if wait_until == "domcontentloaded" and not state.dcl:
                raise chrome_cdp._PlaywrightTimeoutError("DOMContentLoaded never fired")
            if not state.commits:
                await asyncio.sleep(timeout / 1000)
                raise chrome_cdp._PlaywrightTimeoutError("main document did not commit")
            self.url = state.final
            return SimpleNamespace(status=state.status)

        @asynccontextmanager
        async def expect_navigation(self, *, wait_until, timeout):
            assert timeout <= 10_000
            if state.reload is None:
                raise chrome_cdp._PlaywrightTimeoutError("no reload")
            assert wait_until == "commit"
            self.url = state.final
            future = asyncio.get_running_loop().create_future()
            future.set_result(SimpleNamespace(status=state.reload))
            yield SimpleNamespace(value=future)

        async def wait_for_function(self, expression, *, timeout):
            assert "document.readyState" in expression and "interactive" in expression and "complete" in expression
            assert 0 < timeout <= 200
            while not parsed():
                await asyncio.sleep(0.001)

        async def wait_for_timeout(self, ms):
            state.settled.append(ms)

        async def close(self):
            state.closed = True

    class Socket:
        def __init__(self, browser):
            self.browser = browser
            self.queue = asyncio.Queue()

        def put(self, message):
            self.queue.put_nowait(json.dumps(message))

        def document(self, status):
            self.put(
                {
                    "method": "Network.responseReceived",
                    "params": {
                        "type": "Document",
                        "frameId": "main",
                        "response": {"status": status, "url": state.final, "headers": {"server": "QRATOR"}},
                    },
                }
            )
            # A successful iframe must never override the main document.
            self.put(
                {
                    "method": "Network.responseReceived",
                    "params": {
                        "type": "Document",
                        "frameId": "child",
                        "response": {"status": 200},
                    },
                }
            )
            if state.commits:
                self.put(
                    {
                        "method": "Page.frameNavigated",
                        "params": {
                            "frame": {
                                "id": "main",
                                "url": state.final,
                                "loaderId": "document",
                            }
                        },
                    }
                )
            if state.dcl:
                self.put({"method": "Page.loadEventFired", "params": {}})

        async def send(self, raw):
            msg = json.loads(raw)
            method = msg["method"]
            state.sent.append(method)
            result = {}
            if method == "Target.createTarget":
                result = {"targetId": "owned"}
            elif method == "Target.closeTarget":
                state.closed = True
            elif method == "Page.navigate":
                result = {"frameId": "main"}
            elif method == "Runtime.evaluate":
                assert "document.readyState" in msg["params"]["expression"]
                result = {"result": {"value": parsed()}}
            self.put({"id": msg["id"], "result": result})
            if method == "Page.navigate":
                # The stale about:blank load must not satisfy this navigation.
                self.put({"method": "Page.loadEventFired", "params": {}})
                self.document(state.status)
                if state.reload is not None:
                    self.document(state.reload)

        async def recv(self):
            return await self.queue.get()

    return Page, Socket


@pytest.fixture(params=["playwright", "raw"])
def navigation(monkeypatch, request):
    state = SimpleNamespace(
        status=200,
        reload=None,
        final=URL,
        ready=["interactive"],
        parsed_reads=0,
        closed=False,
        settled=[],
        permit=[],
        commits=True,
        dcl=False,
        sent=[],
    )
    Page, Socket = _browser_doubles(state)

    @asynccontextmanager
    async def context():
        if request.param == "raw":
            raise chrome_cdp._CdpConnectTimeout("fake attach timeout")
        yield SimpleNamespace()

    async def new_tab(ctx):
        return Page()

    @asynccontextmanager
    async def connect(url, **kwargs):
        yield Socket(url.endswith("/browser"))

    async def acquire(host):
        assert host == "www.citilink.ru"
        return SimpleNamespace(
            ok=lambda: state.permit.append("ok"),
            refused=lambda status: state.permit.append(("refused", status)),
            neutral=lambda: state.permit.append("neutral"),
            release=lambda: state.permit.append("release"),
        )

    async def sleep(seconds):
        state.settled.append(seconds * 1000)

    # Keep raw polling real; only the post-navigation settle delay is recorded.
    original_sleep = asyncio.sleep

    async def record_sleep(seconds):
        if seconds == 0.8:
            await sleep(seconds)
        else:
            await original_sleep(seconds)

    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "get_context", context)
    monkeypatch.setattr(chrome_cdp, "_new_tab", new_tab)
    monkeypatch.setattr(chrome_cdp, "_acquire_navigation_permit", acquire)
    monkeypatch.setattr(chrome_cdp, "_browser_ws_url", lambda: "ws://localhost/browser")
    monkeypatch.setattr(chrome_cdp, "_websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(chrome_cdp.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: BytesIO(
            json.dumps(
                [
                    {"id": "owned", "webSocketDebuggerUrl": "ws://localhost/page"},
                ]
            ).encode()
        ),
    )
    return state


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
@pytest.mark.parametrize("ready", ["interactive", "complete"])
async def test_parsed_dom_without_load_events_yields_owned_page(navigation, ready):
    navigation.ready = ["loading", ready]
    async with chrome_cdp.open_page(
        URL, 800, navigation_timeout_ms=200, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}
    ) as page:
        assert page.url == URL
        assert navigation.parsed_reads >= 2
        assert navigation.settled == [800]
        assert navigation.permit == ["ok", "release"]
        assert not navigation.closed
    assert navigation.closed


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
async def test_parsed_dom_keeps_final_host_policy(navigation):
    navigation.final = "https://outside.example/search"
    with pytest.raises(chrome_cdp.NavigationPolicyError):
        async with chrome_cdp.open_page(
            URL, 0, navigation_timeout_ms=200, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}
        ):
            pytest.fail("off-host page yielded")
    assert navigation.closed
    assert navigation.permit == ["neutral", "release"]


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
@pytest.mark.parametrize("status,reload", [(401, 200), (403, 200), (401, 403)])
async def test_parsed_dom_preserves_antibot_recheck(navigation, status, reload):
    navigation.status, navigation.reload = status, reload
    if reload == 200:
        async with chrome_cdp.open_page(
            URL, 0, navigation_timeout_ms=200, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}
        ):
            assert navigation.parsed_reads
        assert navigation.permit == ["ok", "release"]
    else:
        with pytest.raises(chrome_cdp.NavBlocked) as caught:
            async with chrome_cdp.open_page(
                URL, 0, navigation_timeout_ms=200, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}
            ):
                pytest.fail("blocked reload yielded")
        assert caught.value.status == reload
        assert navigation.permit == [("refused", reload), "release"]
    assert navigation.closed


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
@pytest.mark.parametrize("commits", [False, True])
async def test_unparsed_dom_is_bounded_and_never_yielded(navigation, commits):
    navigation.ready, navigation.commits = ["loading"], commits
    # The raw path's tab close waits raw_targets.RECHECK_S (0.25 s) to confirm the tab is gone
    # (R-CDP-SIM-S4), so a 50 ms budget ends in ~0.3 s; a 0.5 s bound flaked on a slow macOS runner.
    # Half the watchdog still tells the two apart: had the watchdog fired, the call took 5 s.
    watchdog_s = 5.0
    start = asyncio.get_running_loop().time()
    with pytest.raises((TimeoutError, chrome_cdp.TransportDownError, chrome_cdp._PlaywrightTimeoutError)):
        async with asyncio.timeout(watchdog_s):
            async with chrome_cdp.open_page(
                URL, 0, navigation_timeout_ms=50, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}
            ):
                pytest.fail("loading DOM yielded")
    elapsed = asyncio.get_running_loop().time() - start
    assert elapsed < watchdog_s / 2, "the navigation budget, not the outer watchdog, must fire"
    assert navigation.closed
    assert "ok" not in navigation.permit


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
@pytest.mark.parametrize("status", [401, 403])
async def test_parsed_dom_recheck_without_reload_preserves_block(navigation, status):
    navigation.status = status
    with pytest.raises(chrome_cdp.NavBlocked) as caught:
        async with chrome_cdp.open_page(
            URL, 0, navigation_timeout_ms=200, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}
        ):
            pytest.fail("unrecovered block yielded")
    assert caught.value.status == status
    assert not navigation.parsed_reads
    assert navigation.permit == [("refused", status), "release"]
    assert navigation.closed


@pytest.mark.req("R-CDP-NAV-TIMEOUT.qrator-429")
async def test_429_without_load_events_is_refused_before_parse_or_settle(navigation):
    navigation.status, navigation.ready = 429, ["loading"]
    start = asyncio.get_running_loop().time()
    with pytest.raises(chrome_cdp.NavBlocked) as caught:
        async with asyncio.timeout(12):
            async with chrome_cdp.open_page(URL, 800, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}):
                pytest.fail("429 yielded")
    assert asyncio.get_running_loop().time() - start < 12
    assert caught.value.status == 429
    assert not navigation.parsed_reads and not navigation.settled
    assert navigation.permit == [("refused", 429), "release"]
    assert navigation.closed


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
async def test_default_wait_does_not_enable_parsed_dom(navigation):
    navigation.dcl = True
    async with chrome_cdp.open_page(URL, 0, navigation_timeout_ms=200, allowed_hosts={"www.citilink.ru"}):
        assert not navigation.parsed_reads
    assert navigation.closed


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
async def test_unparsed_dom_timeout_says_the_page_was_still_parsing(navigation):
    navigation.ready, navigation.commits = ["loading"], True
    with pytest.raises((TimeoutError, chrome_cdp.TransportDownError)) as excinfo:
        async with chrome_cdp.open_page(
            URL, 0, navigation_timeout_ms=50, wait_for_dom_parsed=True, allowed_hosts={"www.citilink.ru"}
        ):
            pytest.fail("loading DOM yielded")
    assert chrome_cdp._UNPARSED_MSG in str(excinfo.value)


@pytest.mark.req("R-CDP-NAV-TIMEOUT.dom-parsed")
@pytest.mark.parametrize(
    ("ready_state", "next_data", "parsed"),
    [("loading", False, False), ("loading", True, True), ("interactive", False, True), ("complete", False, True)],
)
def test_parsed_dom_predicate_accepts_a_next_data_block_while_loading(ready_state, next_data, parsed):
    """Runs the shipped predicate in Node against a stub document (no browser)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    stub = (
        f"const document = {{readyState: {json.dumps(ready_state)}, "
        f"getElementById: (id) => ({json.dumps(next_data)} && id === '__NEXT_DATA__') ? {{}} : null}};"
    )
    script = f"{stub} process.stdout.write(String(Boolean({chrome_cdp._DOM_PARSED_JS})));"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, check=True).stdout
    assert out == ("true" if parsed else "false")
