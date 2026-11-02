"""R16: a status answer must not carry a credential.

``probe_session`` and ``browser_status`` answer the operator's questions about
the scraping Chrome. When the endpoint or the window state fails, the reason is
built from the exception message — and an exception message can quote a proxy
credential or a token. The reason is an answer, not a log line: it reaches the
operator's terminal and the agent alike, so it is scrubbed before it is stored.

Offline by construction: the CDP endpoint and the websocket are faked, and no
browser is started or attached.
"""

from __future__ import annotations

from mcp_core.transport import chrome_cdp

PROXY = "http://proxy-user:proxy-secret@proxy.example:8080"
TOKEN = "Bearer AUDIT_FAKE_TOKEN_123456"


class _Browser:
    """A Playwright attach that fails on entry."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def __aenter__(self) -> _Browser:
        raise OSError(self._message)

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Channel:
    """A browser-level CDP channel whose every command fails."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def __aenter__(self) -> _Channel:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def _send(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        raise OSError(self._message)


def _live_endpoint(path: str, *, timeout_s: float = 5.0) -> object:
    """A CDP endpoint that answers both discovery reads."""
    if path == "/json/version":
        return {"Browser": "Chrome/141.0.7390.55"}
    return [{"id": "page-1", "type": "page", "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/page-1"}]


async def test_the_probe_reason_does_not_carry_a_proxy_credential(monkeypatch):
    """The reachability probe answers a reason, and it must be scrubbed."""
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: True)
    monkeypatch.setattr(chrome_cdp, "get_browser", lambda *a, **k: _Browser(f"connect failed via {PROXY}"))

    result = await chrome_cdp.probe_session()

    reason = str(result["reason"])
    assert "proxy-secret" not in reason
    assert "proxy-user" not in reason
    # The host stays readable: the operator still knows where to look.
    assert "proxy.example" in reason
    assert "OSError" in reason


async def test_the_probe_reason_does_not_carry_a_token(monkeypatch):
    """A bearer token in the exception text is replaced, not echoed."""
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: True)
    monkeypatch.setattr(chrome_cdp, "get_browser", lambda *a, **k: _Browser(f"attach refused: {TOKEN}"))

    result = await chrome_cdp.probe_session()

    reason = str(result["reason"])
    assert "AUDIT_FAKE_TOKEN_123456" not in reason
    assert "<redacted>" in reason


async def test_the_browser_status_reason_does_not_carry_a_token(monkeypatch):
    """``browser status`` is the operator's status answer."""
    monkeypatch.setattr(chrome_cdp, "_cdp_http_json", _live_endpoint)
    monkeypatch.setattr(chrome_cdp, "_browser_cdp_channel", lambda: _Channel(f"window failed: {TOKEN}"))

    report = await chrome_cdp.browser_status()

    reason = str(report["reason"])
    assert "AUDIT_FAKE_TOKEN_123456" not in reason
    assert "<redacted>" in reason
    # The facts the operator needs are still there.
    assert report["reachable"] is True
    assert report["page_targets"] == 1


async def test_the_open_tabs_reason_does_not_carry_a_token(monkeypatch):
    """A failed tab creation reports a reason, and it is scrubbed too."""
    monkeypatch.setattr(chrome_cdp, "_browser_cdp_channel", lambda: _Channel(f"create failed: {TOKEN}"))

    report = await chrome_cdp.open_tabs(["https://example.com/item"])

    assert report["ok"] is False
    assert "AUDIT_FAKE_TOKEN_123456" not in str(report["reason"])
    assert "<redacted>" in str(report["reason"])


async def test_an_unreachable_endpoint_is_still_reported(monkeypatch):
    """The positive control: a dead endpoint still answers with a reason."""
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)

    result = await chrome_cdp.probe_session()

    assert result["reachable"] is False
    assert "nothing listening" in str(result["reason"])
