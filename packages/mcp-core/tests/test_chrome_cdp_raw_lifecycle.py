"""Raw CDP owns its target even when discovery or attachment fails."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from mcp_core.transport import chrome_cdp


@pytest.fixture
def raw_browser(monkeypatch):
    state = SimpleNamespace(fail=None, cleanup=None, sent=[], entered=[], exited=[], attaching=asyncio.Event())

    class Socket:
        def __init__(self, kind):
            self.kind = kind
            self.responses = asyncio.Queue()

        async def send(self, raw):
            message = json.loads(raw)
            method = message["method"]
            state.sent.append((self.kind, method, message["params"]))
            if method == "Target.closeTarget":
                if state.cleanup == "error":
                    raise OSError("cleanup failed")
                if state.cleanup == "hang":
                    await asyncio.Event().wait()
            if method == "Page.enable" and state.fail == "enable":
                raise OSError("enable failed")
            result = {"targetId": "owned"} if method == "Target.createTarget" else {}
            await self.responses.put(json.dumps({"id": message["id"], "result": result}))
            if method == "Page.navigate":
                await self.responses.put(
                    json.dumps(
                        {
                            "method": "Network.responseReceived",
                            "params": {
                                "type": "Document",
                                "response": {"status": 403 if state.fail == "navigation" else 200},
                            },
                        }
                    )
                )
                await self.responses.put(json.dumps({"method": "Page.loadEventFired"}))

        async def recv(self):
            return await self.responses.get()

    @asynccontextmanager
    async def connect(url, **options):
        assert options["max_size"] == chrome_cdp._RAW_MAX_FRAME_BYTES
        assert options["open_timeout"] == chrome_cdp._RAW_CONNECT_TIMEOUT_S
        kind = "browser" if url.endswith("/browser") else "page"
        if kind == "page":
            assert "browser" not in state.exited
            if state.fail == "connect":
                raise OSError("connect failed")
            if state.fail == "cancel_attach":
                state.attaching.set()
                await asyncio.Event().wait()
        state.entered.append(kind)
        try:
            yield Socket(kind)
        finally:
            state.exited.append(kind)

    def discover(url, timeout):
        assert url == f"{chrome_cdp.CDP_URL}/json"
        if state.fail == "discovery":
            raise OSError("discovery failed")
        if state.fail == "json":
            return BytesIO(b"invalid json")
        targets = [{"id": "foreign", "webSocketDebuggerUrl": "ws://localhost/foreign"}]
        if state.fail != "missing":
            targets.append({"id": "owned", "webSocketDebuggerUrl": "ws://localhost/page"})
        return BytesIO(json.dumps(targets).encode())

    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "_browser_ws_url", lambda: "ws://localhost/browser")
    monkeypatch.setattr(chrome_cdp, "_websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(urllib.request, "urlopen", discover)
    return state


def assert_owned_target_closed(state):
    closes = [entry for entry in state.sent if entry[1] == "Target.closeTarget"]
    assert closes == [("browser", "Target.closeTarget", {"targetId": "owned"})]
    assert state.exited[-1] == "browser"


@pytest.mark.parametrize("failure", ["discovery", "json", "missing", "connect", "enable", "navigation", "body"])
async def test_raw_target_closed_after_every_postcreation_failure(raw_browser, failure):
    raw_browser.fail = failure
    with pytest.raises((RuntimeError, OSError, chrome_cdp.NavBlocked)):
        async with chrome_cdp._raw_cdp_page("https://example.com", 0):
            assert failure == "body", "the failing setup must never yield a page"
            raise RuntimeError("body failed")
    assert_owned_target_closed(raw_browser)


async def test_raw_target_closed_once_on_success(raw_browser):
    async with chrome_cdp._raw_cdp_page("https://example.com", 0) as page:
        assert page._target_id == "owned"
        assert not any(entry[1] == "Target.closeTarget" for entry in raw_browser.sent)
    assert_owned_target_closed(raw_browser)
    assert raw_browser.exited == ["page", "browser"]


@pytest.mark.parametrize("cleanup", ["error", "hang"])
@pytest.mark.parametrize("failure", ["missing", "body", "cancel_attach", "cancel_body"])
async def test_raw_cleanup_is_bounded_and_preserves_original_failure(raw_browser, monkeypatch, cleanup, failure):
    raw_browser.fail = failure
    raw_browser.cleanup = cleanup
    monkeypatch.setattr(chrome_cdp, "_TAB_OP_TIMEOUT_S", 0.02)
    original = RuntimeError("body failed")

    async def run():
        async with chrome_cdp._raw_cdp_page("https://example.com", 0):
            if failure == "cancel_body":
                raw_browser.attaching.set()
                await asyncio.Event().wait()
            raise original

    task = asyncio.create_task(run())
    if failure.startswith("cancel_"):
        await asyncio.wait_for(raw_browser.attaching.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    else:
        with pytest.raises(RuntimeError) as caught:
            await asyncio.wait_for(task, timeout=1)
        if failure == "body":
            assert caught.value is original
        else:
            assert str(caught.value) == "CDP target has no websocket URL"
    assert_owned_target_closed(raw_browser)
