"""Offline tests for the browser-level CDP helpers behind ``browser status`` / ``browser show``.

The two CDP HTTP endpoints and the browser websocket are faked, so nothing here
binds a socket or needs a real Chrome. What the tests pin down is the contract
an operator sees: what the status report says, and the exact CDP commands
``show`` sends — including that it never navigates anywhere.
"""

from __future__ import annotations

import http.client
import json
import urllib.request
from contextlib import asynccontextmanager
from types import SimpleNamespace

from mcp_core.transport import chrome_cdp

VERSION_JSON = {
    "Browser": "Chrome/151.0.0.0",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/fake",
}

TARGETS_JSON = [
    {"id": "page-1", "type": "page", "title": "first"},
    {"id": "page-2", "type": "page", "title": "second"},
    {"id": "worker-1", "type": "service_worker"},
]


class FakeResponse:
    """The minimal context-manager response of ``urllib.request.urlopen``."""

    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


class FakeSocket:
    """A browser websocket that records the commands it is sent."""

    def __init__(self, results: dict[str, dict]) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.url = ""
        self._results = results
        self._pending: list[tuple[int, str]] = []

    async def send(self, message: str) -> None:
        payload = json.loads(message)
        self.sent.append((payload["method"], payload.get("params", {})))
        self._pending.append((payload["id"], payload["method"]))

    async def recv(self) -> str:
        msg_id, method = self._pending.pop(0)
        return json.dumps({"id": msg_id, "result": self._results.get(method, {})})


def fake_http(monkeypatch, *, version: object = VERSION_JSON, targets: object = TARGETS_JSON, dead: str = "") -> None:
    """Answer the two CDP HTTP endpoints the helpers read."""

    def urlopen(url: str, *, timeout: float) -> FakeResponse:
        if url.endswith("/json/version"):
            if dead == "version":
                raise OSError("connection refused")
            return FakeResponse(version)
        if dead == "list":
            raise OSError("connection refused")
        return FakeResponse(targets)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def fake_websocket(monkeypatch, results: dict[str, dict]) -> FakeSocket:
    """Serve a fake browser websocket and hand the socket back for assertions."""
    socket = FakeSocket(results)

    @asynccontextmanager
    async def connect(url: str, **_kwargs: object):
        socket.url = url
        yield socket

    monkeypatch.setattr(chrome_cdp, "_websockets", SimpleNamespace(connect=connect))
    return socket


# ------------------------------------------------------------------- status ----


async def test_browser_status_reports_an_unreachable_endpoint(monkeypatch):
    fake_http(monkeypatch, dead="version")

    report = await chrome_cdp.browser_status()

    assert report["reachable"] is False
    assert report["window"] == "unknown"
    assert "9222" in str(report["reason"])


async def test_browser_status_reports_version_page_count_and_off_screen_window(monkeypatch):
    fake_http(monkeypatch)
    fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": -32000, "top": -32000, "width": 1280, "height": 720},
            }
        },
    )

    report = await chrome_cdp.browser_status()

    assert report["reachable"] is True
    assert report["version"] == "Chrome/151.0.0.0"
    # Two page targets; the service worker is not a page.
    assert report["page_targets"] == 2
    assert report["window"] == "off-screen"


async def test_browser_status_calls_a_minimized_window_minimized(monkeypatch):
    fake_http(monkeypatch)
    fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": 80, "top": 40, "width": 1280, "height": 720, "windowState": "minimized"},
            }
        },
    )

    report = await chrome_cdp.browser_status()

    assert report["window"] == "minimized"


async def test_browser_status_calls_a_visible_window_visible(monkeypatch):
    fake_http(monkeypatch)
    fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": 80, "top": 40, "width": 1280, "height": 720, "windowState": "normal"},
            }
        },
    )

    report = await chrome_cdp.browser_status()

    assert report["window"] == "visible"


async def test_browser_status_keeps_the_version_when_the_target_list_fails(monkeypatch):
    fake_http(monkeypatch, dead="list")

    report = await chrome_cdp.browser_status()

    assert report["reachable"] is True
    assert report["version"] == "Chrome/151.0.0.0"
    assert report["page_targets"] is None


# --------------------------------------------------------------------- show ----


async def test_show_moves_the_window_on_screen_and_activates_the_first_tab(monkeypatch):
    fake_http(monkeypatch)
    socket = fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": -32000, "top": -32000, "width": 1280, "height": 720},
            }
        },
    )

    result = await chrome_cdp.show_browser_window()

    methods = [method for method, _ in socket.sent]
    assert methods == [
        "Browser.getWindowForTarget",
        "Browser.setWindowBounds",
        "Browser.setWindowBounds",
        "Browser.setWindowBounds",
        "Target.activateTarget",
    ]
    bounds_calls = [params for method, params in socket.sent if method == "Browser.setWindowBounds"]
    # Minimize, then restore: on Windows only a restore raises a window that
    # sits behind others.
    assert bounds_calls[0]["bounds"] == {"windowState": "minimized"}
    assert bounds_calls[1]["bounds"] == {"windowState": "normal"}
    assert bounds_calls[2]["bounds"] == {"left": 80, "top": 40, "width": 1280, "height": 720}
    activate = [params for method, params in socket.sent if method == "Target.activateTarget"]
    assert activate == [{"targetId": "page-1"}]
    assert result["ok"] is True


async def test_show_uses_the_target_it_is_given(monkeypatch):
    """`unblock` shows the tab it just opened, not the first one (audit S4-F3).

    `marketplace-mcp unblock` passes the target id of the tab it opened, so a
    window that always showed the first page would raise the operator's Chrome
    on the wrong tab - the one thing the command exists to prevent.
    """
    fake_http(monkeypatch)
    socket = fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": 80, "top": 40, "width": 1280, "height": 720},
            }
        },
    )

    result = await chrome_cdp.show_browser_window(target_id="page-2")

    assert result["ok"] is True
    assert result["target_id"] == "page-2"
    get_window = [params for method, params in socket.sent if method == "Browser.getWindowForTarget"]
    activate = [params for method, params in socket.sent if method == "Target.activateTarget"]
    assert get_window == [{"targetId": "page-2"}]
    assert activate == [{"targetId": "page-2"}]


async def test_the_browser_websocket_is_dialed_on_the_host_that_answers(monkeypatch):
    """The endpoint's own loopback address must not be dialled (audit S4-F5).

    Chrome advertises `ws://127.0.0.1:9222/...` even when it was reached
    through `CHROME_CDP_HOST`, so the URL is rewritten to the host we know
    answers. With a non-default host a broken rewrite sends every
    browser-level command to the wrong machine.
    """
    monkeypatch.setattr(chrome_cdp, "CDP_HOST", "host.docker.internal")
    monkeypatch.setattr(chrome_cdp, "CDP_PORT", 9333)
    fake_http(monkeypatch)
    socket = fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": 80, "top": 40, "width": 1280, "height": 720},
            }
        },
    )

    await chrome_cdp.browser_status()

    assert socket.url == f"ws://{chrome_cdp.CDP_HOST}:{chrome_cdp.CDP_PORT}/devtools/browser/fake"


async def test_show_never_navigates_anywhere(monkeypatch):
    """The verb exists to show a window; it must not touch a website."""
    fake_http(monkeypatch)
    socket = fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": 80, "top": 40, "width": 1280, "height": 720},
            }
        },
    )

    await chrome_cdp.show_browser_window()

    # The socket must have been used at all: an empty command list would make
    # the subset check below pass for the wrong reason.
    assert socket.sent
    assert "Page.navigate" not in [method for method, _ in socket.sent]


async def test_show_resizes_a_window_too_small_to_work_in(monkeypatch):
    fake_http(monkeypatch)
    socket = fake_websocket(
        monkeypatch,
        {"Browser.getWindowForTarget": {"windowId": 7, "bounds": {"left": 80, "top": 40, "width": 320, "height": 240}}},
    )

    await chrome_cdp.show_browser_window()

    bounds_calls = [params for method, params in socket.sent if method == "Browser.setWindowBounds"]
    assert bounds_calls[-1]["bounds"] == {"left": 80, "top": 40, "width": 1280, "height": 900}


async def test_show_reports_an_unreachable_endpoint(monkeypatch):
    fake_http(monkeypatch, dead="version")

    result = await chrome_cdp.show_browser_window()

    assert result["ok"] is False
    assert result["reachable"] is False
    assert "9222" in str(result["reason"])


async def test_show_without_a_page_target_says_so(monkeypatch):
    fake_http(monkeypatch, targets=[])

    result = await chrome_cdp.show_browser_window()

    assert result["ok"] is False
    assert result["reachable"] is True
    assert "no page target" in str(result["reason"])


async def test_show_reports_a_window_it_could_not_move(monkeypatch):
    """A browser that answers but cannot move its window is an answer, not a crash."""
    fake_http(monkeypatch)

    def failing_connect(*_args, **_kwargs):
        raise RuntimeError("websocket refused")

    monkeypatch.setattr(chrome_cdp, "_websockets", SimpleNamespace(connect=failing_connect))

    result = await chrome_cdp.show_browser_window()

    assert result["ok"] is False
    assert result["reachable"] is True
    assert "could not be moved" in str(result["reason"])


# ------------------------------------------------------------ shared helper ----


async def test_reveal_owned_page_uses_the_shared_window_moving_helper(monkeypatch):
    """The owned-page reveal and ``browser show`` must not drift apart."""
    calls: list[tuple[int, dict, dict, bool]] = []

    async def fake_move(send, window_id, bounds, on_screen, *, force=False):
        calls.append((window_id, dict(bounds), dict(on_screen), force))
        return True

    monkeypatch.setattr(chrome_cdp, "_move_window_on_screen", fake_move)

    async def raw_send(method, params=None):
        if method == "Browser.getWindowForTarget":
            return {"windowId": 42, "bounds": {"left": -32000}}
        return {}

    page = chrome_cdp._RawCdpPage(SimpleNamespace(), "owned")
    page._send = raw_send

    assert await chrome_cdp.reveal_owned_page(page)
    assert len(calls) == 1
    window_id, bounds, on_screen, force = calls[0]
    assert window_id == 42
    assert bounds == {"left": -32000}
    assert on_screen == {"left": 80, "top": 80}
    assert force is False


async def test_a_broken_http_exchange_reads_as_no_answer(monkeypatch):
    """IncompleteRead is not an OSError, and it must not escape the CDP helpers.

    Before the shared HTTP helper these reads caught every exception; the
    narrower catch still has to cover a broken HTTP exchange.
    """

    def urlopen(url: str, *, timeout: float):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert chrome_cdp._page_target_count(path="/json/list", timeout_s=1.0) is None
    assert chrome_cdp._browser_ws_url() is None
    report = await chrome_cdp.browser_status()
    assert report["reachable"] is False
    shown = await chrome_cdp.show_browser_window()
    assert shown["ok"] is False


async def test_show_does_not_minimize_a_window_that_is_already_minimized(monkeypatch):
    fake_http(monkeypatch)
    socket = fake_websocket(
        monkeypatch,
        {
            "Browser.getWindowForTarget": {
                "windowId": 7,
                "bounds": {"left": 80, "top": 40, "width": 1280, "height": 720, "windowState": "minimized"},
            }
        },
    )

    result = await chrome_cdp.show_browser_window()

    bounds_calls = [params["bounds"] for method, params in socket.sent if method == "Browser.setWindowBounds"]
    assert bounds_calls[0] == {"windowState": "normal"}
    assert {"windowState": "minimized"} not in bounds_calls
    assert result["ok"] is True
