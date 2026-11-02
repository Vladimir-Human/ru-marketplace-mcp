"""R14: the raw-CDP page socket URL must be a valid URL for an IPv6 host.

The raw fallback rewrites the page websocket URL to the host this process
dials. An IPv6 literal needs brackets there (``ws://[::1]:9222/...``); without
them the URL cannot even be parsed, so the fallback can never attach to a
browser that listens on ``::1``.

No real browser is involved: the websocket and the discovery HTTP layer are
faked, exactly as the raw lifecycle tests do.
"""

from __future__ import annotations

import json
import urllib.request
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from mcp_core.transport import chrome_cdp


class _StopAfterUrl(RuntimeError):
    """Raised by the fake connect once the page socket URL was captured."""


class _Socket:
    """The slice of a websocket the raw page drives."""

    def __init__(self) -> None:
        self.last: dict = {}
        self.methods: list[str] = []

    async def send(self, raw: str) -> None:
        self.last = json.loads(raw)
        self.methods.append(self.last["method"])

    async def recv(self) -> str:
        result = {"targetId": "owned"} if self.last["method"] == "Target.createTarget" else {}
        return json.dumps({"id": self.last["id"], "result": result})


class _Response:
    """The context manager ``urllib.request.urlopen`` returns."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _discover(*args, **kwargs):
    """Answer the /json discovery with one owned page target."""
    payload = [{"id": "owned", "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/owned"}]
    return _Response(json.dumps(payload).encode())


def _discover_version(*args, **kwargs):
    """Answer the /json/version discovery with the browser websocket URL."""
    payload = {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/test"}
    return _Response(json.dumps(payload).encode())


async def _capture_page_url(monkeypatch, host: str, port: int = 9222) -> tuple[str, list[str]]:
    """Run the real raw fallback and return the page socket URL it dialled."""
    literal = f"[{host}]" if ":" in host else host
    urls: list[str] = []
    socket = _Socket()

    @asynccontextmanager
    async def connect(uri, **options):
        urls.append(uri)
        if len(urls) > 1:
            raise _StopAfterUrl("captured the generated page socket URL")
        yield socket

    monkeypatch.setattr(chrome_cdp, "CDP_HOST", host)
    monkeypatch.setattr(chrome_cdp, "CDP_PORT", port)
    monkeypatch.setattr(chrome_cdp, "CDP_URL", f"http://{literal}:{port}")
    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "_browser_ws_url", lambda: f"ws://{literal}:{port}/devtools/browser/test")
    monkeypatch.setattr(chrome_cdp, "_websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(urllib.request, "urlopen", _discover)

    with pytest.raises(_StopAfterUrl):
        async with chrome_cdp._raw_cdp_page("https://example.com/item", 0):
            pass
    return urls[-1], socket.methods


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "ws://127.0.0.1:9222/devtools/page/owned"),
        ("::1", "ws://[::1]:9222/devtools/page/owned"),
    ],
)
async def test_the_page_socket_url_is_built_for_the_host_we_dial(monkeypatch, host, expected):
    """The IPv6 host must produce a bracketed, parseable websocket URL."""
    url, methods = await _capture_page_url(monkeypatch, host)

    assert url == expected
    parsed = urlsplit(url)
    assert parsed.hostname == host
    assert parsed.port == 9222
    # The owned target is handed back even though the attach never happened.
    assert "Target.closeTarget" in methods


async def test_an_ipv6_host_is_bracketed_in_the_browser_socket_too(monkeypatch):
    """One builder for both sockets: the browser URL is valid for ``::1`` too."""
    monkeypatch.setattr(chrome_cdp, "CDP_HOST", "::1")
    monkeypatch.setattr(chrome_cdp, "CDP_PORT", 9222)
    monkeypatch.setattr(urllib.request, "urlopen", _discover_version)

    url = chrome_cdp._browser_ws_url()

    assert url == "ws://[::1]:9222/devtools/browser/test"
    assert urlsplit(url).hostname == "::1"
    assert urlsplit(url).port == 9222


@pytest.mark.parametrize("host", ["127.0.0.1", "host.docker.internal", "chrome.example"])
async def test_a_non_ipv6_host_is_untouched(monkeypatch, host):
    """IPv4, a Docker host and a plain hostname keep the spelling they had."""
    url, _ = await _capture_page_url(monkeypatch, host)

    assert url == f"ws://{host}:9222/devtools/page/owned"
    assert urlsplit(url).hostname == host


async def test_a_remote_cdp_host_is_dialed_at_its_own_port(monkeypatch):
    """The port comes from the environment, not from the advertised URL."""
    url, _ = await _capture_page_url(monkeypatch, "10.0.0.5", port=9333)

    assert url == "ws://10.0.0.5:9333/devtools/page/owned"
    assert urlsplit(url).port == 9333
