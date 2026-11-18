"""R-CDP-REDIRECT-GUARD: no redirect hop leaves the caller's host policy.

A store page can answer with a 302 to another host. Chrome follows the chain
before ``open_page``'s final-URL check can see it, so the foreign page loads
with the scraping profile's cookies for it. The guard pauses every main-frame
document request (CDP Fetch domain), checks each hop against the caller's
policy and fails an off-policy hop with ``BlockedByClient`` before Chrome
sends it. The call then fails exactly as an off-host final URL does today:
``NavigationPolicyError``, which the tools answer as ``transport_down`` with
no page content.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from mcp_core.transport import chrome_cdp, nav_guard
from playwright.async_api import Error as PlaywrightError

SHOP = "https://shop.example/item"
FOREIGN = "https://foreign.invalid/landing"
ALLOWED_HOSTS = {"shop.example"}
MAIN = {"url": SHOP, "frameId": "main"}
FOREIGN_HOP = {"url": FOREIGN, "frameId": "main"}
SUB_FRAME_HOP = {"url": FOREIGN, "frameId": "child"}


class _GuardState:
    """What both transports recorded: the commands sent and the answers given."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.answers: list[tuple[str, dict[str, Any]]] = []
        self.hops: list[dict[str, Any]] = []
        self.fetch_enable_fails = False
        self.cleanup_hangs = False
        self.interception = True
        self.final_url = SHOP
        self.logs: list[tuple[str, dict[str, Any]]] = []

    def methods(self) -> list[str]:
        return [method for method, _ in self.commands]

    def answer_for(self, request_id: str) -> str | None:
        for kind, params in self.answers:
            if params.get("requestId") == request_id:
                return kind
        return None


@pytest.fixture
def guard_state(monkeypatch):
    """The guard's state, with its structured stderr event recorded instead of printed."""
    state = _GuardState()
    monkeypatch.setattr(nav_guard, "log_event", lambda event, **fields: state.logs.append((event, fields)))
    return state


def _permit(monkeypatch, calls: list[str]):
    async def acquire(_host: str):
        calls.append("acquire")
        return SimpleNamespace(
            ok=lambda: calls.append("ok"),
            neutral=lambda: calls.append("neutral"),
            refused=lambda _status: calls.append("refused"),
            release=lambda: calls.append("release"),
        )

    monkeypatch.setattr(chrome_cdp, "_acquire_navigation_permit", acquire)


# START_BLOCK_PLAYWRIGHT_PATH


class _FakeCdpSession:
    """A Playwright CDP session double: it pauses documents and waits for the answer.

    Chrome pauses a request when ``Fetch.enable`` is on and the request's
    stage matches; the client then continues or fails it. The double does the
    same: a paused request blocks the navigation until it is answered.
    """

    def __init__(self, state: _GuardState) -> None:
        self._state = state
        self._handlers: dict[str, Any] = {}

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._state.commands.append((method, params or {}))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main"}}}
        if method == "Fetch.enable" and self._state.fetch_enable_fails:
            self._state.interception = False
            raise RuntimeError("Fetch.enable refused")
        if method == "Fetch.disable" and self._state.cleanup_hangs:
            await asyncio.Event().wait()
        if method in {"Fetch.continueRequest", "Fetch.failRequest"}:
            self._state.answers.append(("continue" if method.endswith("continueRequest") else "fail", params or {}))
        return {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers[event] = handler

    def remove_listener(self, event: str, handler: Any) -> None:
        if self._handlers.get(event) is handler:
            self._handlers.pop(event)

    async def detach(self) -> None:
        if self._state.cleanup_hangs:
            await asyncio.Event().wait()


class _FakePlaywrightPage:
    """A page double whose navigation walks the redirect chain like Chrome does."""

    def __init__(self, state: _GuardState, session: _FakeCdpSession) -> None:
        self._state = state
        self._session = session
        self.url = "about:blank"
        self.closed = False

    async def goto(self, url: str, **_kwargs: Any) -> Any:
        for index, hop in enumerate(self._state.hops):
            request_id = f"hop-{index}"
            if not self._state.interception:
                continue
            self._pause(request_id, hop)
            answer = await self._await_answer(request_id)
            if answer == "fail":
                raise PlaywrightError(f"page.goto: net::ERR_BLOCKED_BY_CLIENT at {url}")
        self.url = self._state.final_url
        return SimpleNamespace(status=200)

    def _pause(self, request_id: str, hop: dict[str, Any]) -> None:
        if not self._state.interception:
            return
        handler = self._session._handlers.get("Fetch.requestPaused")
        assert handler is not None, "the guard must register the paused-request handler"
        handler(
            {
                "requestId": request_id,
                "frameId": hop.get("frameId", "main"),
                "resourceType": hop.get("resourceType", "Document"),
                "request": {"url": hop["url"]},
            }
        )

    async def _await_answer(self, request_id: str) -> str | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while loop.time() < deadline:
            answer = self._state.answer_for(request_id)
            if answer is not None:
                return answer
            await asyncio.sleep(0)
        raise AssertionError(f"the paused request {request_id} was never answered")

    async def close(self) -> None:
        self.closed = True

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


def _playwright_transport(monkeypatch, state: _GuardState) -> _FakePlaywrightPage:
    """Wire the public ``open_page`` to the Playwright double above."""
    session = _FakeCdpSession(state)
    page = _FakePlaywrightPage(state, session)

    class Context:
        browser = None

        async def new_cdp_session(self, target: Any) -> _FakeCdpSession:
            assert target is page
            return session

    @asynccontextmanager
    async def context():
        yield Context()

    async def new_tab(_ctx: Any) -> _FakePlaywrightPage:
        return page

    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "get_context", context)
    monkeypatch.setattr(chrome_cdp, "_new_tab", new_tab)
    return page


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_playwright_fails_a_foreign_redirect_hop_before_chrome_sends_it(monkeypatch, guard_state):
    """A 302 to a foreign host is failed with BlockedByClient, never continued."""
    state = guard_state
    state.hops = [MAIN, FOREIGN_HOP]
    _playwright_transport(monkeypatch, state)
    calls: list[str] = []
    _permit(monkeypatch, calls)

    with pytest.raises(chrome_cdp.NavigationPolicyError):
        async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS):
            pytest.fail("a refused hop must not yield a page")

    assert state.answer_for("hop-0") == "continue", "the allowed hop must go through"
    assert state.answer_for("hop-1") == "fail", "the foreign hop must be failed"
    failed = next(params for kind, params in state.answers if kind == "fail")
    assert failed["errorReason"] == "BlockedByClient"
    assert FOREIGN in failed.get("url", "") or failed["requestId"] == "hop-1"
    assert calls == ["acquire", "neutral", "release"], "our refusal must not blame the source"


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_playwright_continues_an_allowed_hop_and_a_foreign_sub_frame(monkeypatch, guard_state):
    """Allowed hosts and sub-frame documents pass untouched."""
    state = guard_state
    state.hops = [MAIN, SUB_FRAME_HOP]
    _playwright_transport(monkeypatch, state)

    async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS) as page:
        assert page.url == SHOP

    assert state.answer_for("hop-0") == "continue"
    assert state.answer_for("hop-1") == "continue", "a sub-frame document is not the main frame"


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_playwright_disables_the_interception_after_the_main_document(monkeypatch, guard_state):
    """The interception is off once the main document answered."""
    state = guard_state
    state.hops = [MAIN]
    _playwright_transport(monkeypatch, state)

    async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS):
        pass

    methods = state.methods()
    assert "Fetch.enable" in methods and "Fetch.disable" in methods
    assert methods.index("Fetch.disable") > methods.index("Fetch.enable")
    assert methods[-1] == "Fetch.disable", "the interception must be off when the call ends"


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_playwright_cleanup_that_hangs_does_not_hold_the_call(monkeypatch, guard_state):
    """Real Chrome (sim S9): after a timed-out navigation Fetch.disable and detach may not answer; exit stays bounded."""
    state = guard_state
    state.hops = [MAIN]
    state.cleanup_hangs = True
    _playwright_transport(monkeypatch, state)
    monkeypatch.setattr(nav_guard, "CLEANUP_TIMEOUT_S", 0.05, raising=False)

    async with asyncio.timeout(2):
        async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS):
            pass

    assert "Fetch.disable" in state.methods()


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_playwright_navigates_when_the_interception_cannot_be_switched_on(monkeypatch, guard_state):
    """A refused Fetch.enable keeps today's behaviour and says so."""
    state = guard_state
    state.hops = [MAIN]
    state.fetch_enable_fails = True
    _playwright_transport(monkeypatch, state)

    async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS) as page:
        assert page.url == SHOP

    assert state.answer_for("hop-0") is None, "nothing is paused without the interception"
    assert any(event == "cdp.nav_guard.unavailable" for event, _ in state.logs)


# END_BLOCK_PLAYWRIGHT_PATH
# START_BLOCK_RAW_PATH


class _FakeRawSocket:
    """A raw CDP websocket double that pauses documents like Chrome does."""

    def __init__(self, state: _GuardState, kind: str) -> None:
        self._state = state
        self._kind = kind
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._hop = 0

    def push(self, message: dict[str, Any]) -> None:
        self._queue.put_nowait(json.dumps(message))

    async def send(self, raw: str) -> None:
        command = json.loads(raw)
        method = command["method"]
        self._state.commands.append((method, command.get("params") or {}))
        if method == "Target.createTarget":
            result: dict[str, Any] = {"targetId": "owned"}
        elif method == "Target.getTargets":
            result = {"targetInfos": []}
        elif method == "Page.navigate":
            result = {"frameId": "main"}
        elif method == "Fetch.enable" and self._state.fetch_enable_fails:
            self._state.interception = False
            raise RuntimeError("Fetch.enable refused")
        else:
            result = {}
        self.push({"id": command["id"], "result": result})
        if method == "Page.navigate":
            self._next()
        elif method == "Fetch.continueRequest":
            self._state.answers.append(("continue", command["params"]))
            self._next()
        elif method == "Fetch.failRequest":
            self._state.answers.append(("fail", command["params"]))

    def _next(self) -> None:
        """Pause the next hop, or answer with the main document."""
        if not self._state.interception:
            self._document()
            return
        if self._hop < len(self._state.hops):
            hop = self._state.hops[self._hop]
            self._hop += 1
            self.push(
                {
                    "method": "Fetch.requestPaused",
                    "params": {
                        "requestId": f"hop-{self._hop - 1}",
                        "frameId": hop.get("frameId", "main"),
                        "resourceType": hop.get("resourceType", "Document"),
                        "request": {"url": hop["url"]},
                    },
                }
            )
            return
        self._document()

    def _document(self) -> None:
        self.push(
            {
                "method": "Network.responseReceived",
                "params": {
                    "type": "Document",
                    "frameId": "main",
                    "response": {"status": 200, "url": self._state.final_url},
                },
            }
        )
        self.push({"method": "Page.loadEventFired", "params": {}})

    async def recv(self) -> str:
        return await self._queue.get()


def _raw_transport(monkeypatch, state: _GuardState) -> None:
    """Wire the public ``open_page`` to the raw-CDP double above."""
    browser = _FakeRawSocket(state, "browser")
    page = _FakeRawSocket(state, "page")

    @asynccontextmanager
    async def connect(url: str, **_kwargs: Any):
        yield browser if "/devtools/browser/" in url else page

    @asynccontextmanager
    async def playwright(_url: str, _wait_ms: int, **_kwargs: Any):
        raise chrome_cdp._CdpConnectTimeout("fake attach timeout forces the raw fallback")
        yield  # pragma: no cover -- declares the async context manager

    def discover(_url: str, timeout: float = 0) -> BytesIO:
        targets = [{"id": "owned", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/owned"}]
        return BytesIO(json.dumps(targets).encode("utf-8"))

    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "_playwright_page", playwright)
    monkeypatch.setattr(chrome_cdp, "_browser_ws_url", lambda: "ws://127.0.0.1:9222/devtools/browser/prepub")
    monkeypatch.setattr(chrome_cdp, "_websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(urllib.request, "urlopen", discover)


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_raw_fails_a_foreign_redirect_hop_before_chrome_sends_it(monkeypatch, guard_state):
    """The raw fallback refuses the foreign hop the same way the Playwright path does."""
    state = guard_state
    state.hops = [MAIN, FOREIGN_HOP]
    _raw_transport(monkeypatch, state)
    calls: list[str] = []
    _permit(monkeypatch, calls)

    with pytest.raises(chrome_cdp.NavigationPolicyError):
        async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS):
            pytest.fail("a refused hop must not yield a page")

    assert state.answer_for("hop-0") == "continue"
    assert state.answer_for("hop-1") == "fail"
    failed = next(params for kind, params in state.answers if kind == "fail")
    assert failed["errorReason"] == "BlockedByClient"
    assert calls == ["acquire", "neutral", "release"]


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_raw_continues_an_allowed_hop_and_a_foreign_sub_frame(monkeypatch, guard_state):
    """Allowed hosts and sub-frame documents pass untouched on the raw path."""
    state = guard_state
    state.hops = [MAIN, SUB_FRAME_HOP]
    _raw_transport(monkeypatch, state)

    async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS) as page:
        assert page.url == SHOP

    assert state.answer_for("hop-0") == "continue"
    assert state.answer_for("hop-1") == "continue"


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_raw_disables_the_interception_after_the_main_document(monkeypatch, guard_state):
    """The interception is off once the main document answered."""
    state = guard_state
    state.hops = [MAIN]
    _raw_transport(monkeypatch, state)

    async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS):
        pass

    methods = state.methods()
    assert methods.index("Fetch.enable") < methods.index("Page.navigate")
    assert methods.index("Fetch.disable") > methods.index("Page.navigate")


@pytest.mark.req("R-CDP-REDIRECT-GUARD")
async def test_raw_navigates_when_the_interception_cannot_be_switched_on(monkeypatch, guard_state):
    """A refused Fetch.enable keeps today's behaviour and says so."""
    state = guard_state
    state.hops = [MAIN]
    state.fetch_enable_fails = True
    _raw_transport(monkeypatch, state)

    async with chrome_cdp.open_page(SHOP, 0, allowed_hosts=ALLOWED_HOSTS) as page:
        assert page.url == SHOP

    assert state.answer_for("hop-0") is None
    assert any(event == "cdp.nav_guard.unavailable" for event, _ in state.logs)


# END_BLOCK_RAW_PATH
