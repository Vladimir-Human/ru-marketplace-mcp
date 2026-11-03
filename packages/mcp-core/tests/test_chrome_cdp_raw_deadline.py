"""R15: a raw CDP command has an absolute deadline, not an inactivity limit.

CDP broadcasts every event to every attached client, so the socket a raw page
reads is never silent: unrelated events keep arriving while a command waits
for its answer. A timeout that restarts on every receive is an *inactivity*
limit, and a stream of foreign events turns a 80 ms budget into minutes.

The command deadline is therefore absolute: it is set once, and every wait
uses only the time that is left. Matching responses and frame URL events still
correlate exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from mcp_core.transport.chrome_cdp import _RawCdpPage

# A deadline short enough to keep the test quick, and a per-event delay well
# inside it: the old per-receive timeout could not fire on a single event.
DEADLINE_S = 0.3
EVENT_DELAY_S = 0.05
FOREIGN_EVENTS = 12


class _NoisySocket:
    """Answers with foreign events first, then the matching response."""

    def __init__(self, *, events: int = FOREIGN_EVENTS, delay: float = EVENT_DELAY_S) -> None:
        self.events = events
        self.delay = delay
        self.count = 0
        self.last: dict = {}

    async def send(self, raw: str) -> None:
        self.last = json.loads(raw)

    async def recv(self) -> str:
        self.count += 1
        await asyncio.sleep(self.delay)
        if self.count <= self.events:
            return json.dumps({"method": "Network.dataReceived", "params": {}})
        return json.dumps({"id": self.last["id"], "result": {"ok": True}})


class _AnsweringSocket:
    """Answers the command straight away, after one frame event."""

    def __init__(self) -> None:
        self.count = 0
        self.last: dict = {}

    async def send(self, raw: str) -> None:
        self.last = json.loads(raw)

    async def recv(self) -> str:
        self.count += 1
        if self.count == 1:
            return json.dumps(
                {
                    "method": "Page.frameNavigated",
                    "params": {"frame": {"id": "main", "url": "https://example.com/item"}},
                }
            )
        return json.dumps({"id": self.last["id"], "result": {"frameId": "main"}})


async def test_foreign_events_do_not_extend_the_command_deadline():
    """A stream of unrelated events must not buy the command more time."""
    socket = _NoisySocket()
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        await _RawCdpPage(socket, "fake")._send("Runtime.evaluate", timeout=DEADLINE_S)

    elapsed = time.monotonic() - started
    # The answer only arrives after FOREIGN_EVENTS delays, so the command must
    # have given up before then instead of waiting it out.
    assert elapsed < FOREIGN_EVENTS * EVENT_DELAY_S


async def test_the_attach_command_has_the_same_absolute_deadline():
    """Target.createTarget is an attach command, not an evaluate: same rule."""
    socket = _NoisySocket()

    with pytest.raises(TimeoutError):
        await _RawCdpPage(socket, "")._send("Target.createTarget", {"url": "about:blank"}, timeout=DEADLINE_S)


async def test_a_matching_response_still_answers_the_command():
    """The positive control: the answer is correlated and returned."""
    socket = _AnsweringSocket()

    result = await _RawCdpPage(socket, "fake")._send("Page.getFrameTree", timeout=DEADLINE_S)

    assert result == {"frameId": "main"}


async def test_a_frame_url_event_still_updates_the_page_url():
    """Events are still read while waiting: the frame URL is not lost."""
    page = _RawCdpPage(_AnsweringSocket(), "fake")

    await page._send("Page.getFrameTree", timeout=DEADLINE_S)

    assert page.url == "https://example.com/item"


async def test_a_command_error_is_still_reported_as_an_error():
    """An error answer is a failure, not a timeout."""

    class _ErrorSocket:
        async def send(self, raw: str) -> None:
            self.last = json.loads(raw)

        async def recv(self) -> str:
            return json.dumps({"id": self.last["id"], "error": {"code": -32000, "message": "nope"}})

    with pytest.raises(RuntimeError, match=r"CDP Page\.getFrameTree"):
        await _RawCdpPage(_ErrorSocket(), "fake")._send("Page.getFrameTree", timeout=DEADLINE_S)


# START_BLOCK_PUBLIC_RAW_OUTBOUND_DEADLINE

OUTBOUND_DEADLINE_S = 0.1
OUTBOUND_DELAY_S = 0.6
OUTBOUND_WATCHDOG_S = 1.5
OWNED_TARGET = "owned-prepub-target"


class _LifecycleSocket:
    """Native CDP transport double; the real raw page drives every command."""

    def __init__(self, channel, commands, slow_method, send_mode, noisy):
        self.channel = channel
        self.commands = commands
        self.slow_method = slow_method
        self.send_mode = send_mode
        self.noisy = noisy
        self.messages = []
        self.slow_send_cancelled = False
        self.recv_calls = 0

    async def send(self, raw):
        command = json.loads(raw)
        self.commands.append((self.channel, command))
        method = command["method"]
        if method == self.slow_method:
            try:
                if self.send_mode == "blocked":
                    await asyncio.Event().wait()
                else:
                    await asyncio.sleep(OUTBOUND_DELAY_S)
            except asyncio.CancelledError:
                self.slow_send_cancelled = True
                raise
        if self.noisy:
            self.messages.append({"method": "Network.dataReceived", "params": {}})
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
                    {
                        "method": "Network.responseReceived",
                        "params": {
                            "type": "Document",
                            "frameId": "main",
                            "response": {"status": 200, "url": command["params"]["url"]},
                        },
                    },
                    {"method": "Page.loadEventFired", "params": {}},
                ]
            )

    async def recv(self):
        self.recv_calls += 1
        assert self.messages, "recv must not precede a completed send"
        return json.dumps(self.messages.pop(0))


def _install_raw_lifecycle_transport(monkeypatch, *, slow_method=None, send_mode="immediate", noisy=False):
    import urllib.request
    from contextlib import asynccontextmanager
    from io import BytesIO
    from types import SimpleNamespace

    from mcp_core.transport import chrome_cdp as cdp
    from mcp_core.transport.cdp_budget import NavigationBudget

    commands = []
    browser = _LifecycleSocket("browser", commands, slow_method, send_mode, noisy)
    page = _LifecycleSocket("page", commands, slow_method, send_mode, noisy)

    @asynccontextmanager
    async def websocket(url, **kwargs):
        if "/devtools/browser/" in url:
            yield browser
        else:
            assert f"/devtools/page/{OWNED_TARGET}" in url
            yield page

    @asynccontextmanager
    async def unavailable_playwright(*args, **kwargs):
        raise cdp._CdpConnectTimeout("offline forced raw fallback")
        yield  # pragma: no cover -- declares the transport context manager

    targets = [
        {"id": "unrelated-user-target", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/unrelated"},
        {"id": OWNED_TARGET, "webSocketDebuggerUrl": f"ws://127.0.0.1:9222/devtools/page/{OWNED_TARGET}"},
    ]
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: BytesIO(json.dumps(targets).encode("utf-8")))
    monkeypatch.setattr(cdp, "_playwright_page", unavailable_playwright)
    monkeypatch.setattr(cdp, "_browser_ws_url", lambda: "ws://127.0.0.1:9222/devtools/browser/prepub")
    monkeypatch.setattr(cdp, "_websockets", SimpleNamespace(connect=websocket))
    monkeypatch.setattr(cdp, "STEALTH", False)
    monkeypatch.setattr(cdp, "_RAW_CONNECT_TIMEOUT_S", OUTBOUND_DEADLINE_S)
    budget = NavigationBudget(max_concurrent=1, per_host=1)
    monkeypatch.setattr(cdp, "navigation_budget", lambda: budget)
    return cdp, commands, browser, page


@pytest.mark.parametrize("slow_method", ["Target.createTarget", "Page.enable"])
@pytest.mark.parametrize("send_mode", ["delayed", "blocked"])
async def test_public_raw_fallback_bounds_outbound_send(monkeypatch, slow_method, send_mode):
    """R-CDP-COMMAND-DEADLINE: public open_page bounds send, not just recv.

    Prepublication requirement, 2026-11-03: a delayed/blocked websocket.send
    consumes the command's deadline. The watchdog only prevents a broken
    implementation from hanging the test; its TimeoutError is NOT success.
    """
    cdp, commands, browser, page = _install_raw_lifecycle_transport(
        monkeypatch, slow_method=slow_method, send_mode=send_mode
    )

    async def enter():
        async with cdp.open_page("https://example.com/prepub", wait_ms=0):
            pytest.fail("a timed-out attach command must not yield a page")

    started = time.monotonic()
    with pytest.raises(cdp.TransportDownError) as excinfo:
        await asyncio.wait_for(enter(), timeout=OUTBOUND_WATCHDOG_S)
    elapsed = time.monotonic() - started

    assert isinstance(excinfo.value.__cause__, TimeoutError)
    assert elapsed < OUTBOUND_DELAY_S, "send must consume the command deadline, not its own longer delay"
    stalled = browser if slow_method == "Target.createTarget" else page
    assert stalled.slow_send_cancelled, "the expired send must be cancelled before it completes"
    assert stalled.recv_calls == 0
    methods = [command["method"] for _, command in commands]
    if slow_method == "Target.createTarget":
        assert methods == ["Target.createTarget"]
    else:
        assert methods == ["Target.createTarget", "Page.enable", "Target.closeTarget"]
        assert commands[-1][0] == "browser"
        assert commands[-1][1]["params"] == {"targetId": OWNED_TARGET}


@pytest.mark.parametrize("noisy", [False, True], ids=["immediate-response", "foreign-events"])
async def test_public_raw_fallback_still_navigates_and_closes_only_its_target(monkeypatch, noisy):
    """R-CDP-COMMAND-DEADLINE: immediate sends/noisy recv keep the owned lifecycle."""
    cdp, commands, _, _ = _install_raw_lifecycle_transport(monkeypatch, noisy=noisy)

    async with cdp.open_page("https://example.com/prepub", wait_ms=0) as page:
        assert page.url == "https://example.com/prepub"
        assert not any(command["method"] == "Target.closeTarget" for _, command in commands)

    assert [command["method"] for _, command in commands] == [
        "Target.createTarget",
        "Page.enable",
        "Network.enable",
        "Runtime.enable",
        "Page.navigate",
        "Target.closeTarget",
    ]
    assert commands[-1][0] == "browser"
    assert commands[-1][1]["params"] == {"targetId": OWNED_TARGET}


# END_BLOCK_PUBLIC_RAW_OUTBOUND_DEADLINE
