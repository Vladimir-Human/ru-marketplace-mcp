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
