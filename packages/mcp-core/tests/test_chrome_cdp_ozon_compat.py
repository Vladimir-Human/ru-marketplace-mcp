"""Ozon compatibility stays target-scoped and precedes the first navigation."""

from __future__ import annotations

import json
import urllib.request
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from mcp_core.transport import chrome_cdp as C

OZON = "https://www.ozon.ru/"
VERSION = {
    "product": "Chrome/157.0.8103.78",
    "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
}


def test_windows_identity_preserves_native_version_language_and_brands():
    original = dict(VERSION)
    override = C._windows_chrome_ua_override(VERSION)
    assert override["userAgent"] == VERSION["userAgent"].replace("X11; Linux x86_64", "Windows NT 10.0; Win64; x64")
    assert override["platform"] == "Win32"
    assert override["userAgentMetadata"] == {
        "platform": "Windows",
        "platformVersion": "15.0.0",
        "architecture": "x86",
        "model": "",
        "mobile": False,
        "bitness": "64",
        "wow64": False,
    }
    # Chromium supplies these omitted fields from the real browser metadata.
    assert "acceptLanguage" not in override
    assert not {"brands", "fullVersionList", "fullVersion"} & override["userAgentMetadata"].keys()
    assert original == VERSION


def test_windows_identity_keeps_a_native_full_version_token():
    version = {**VERSION, "userAgent": VERSION["userAgent"].replace("157.0.0.0", "157.0.8103.78")}
    assert "Chrome/157.0.8103.78" in C._windows_chrome_ua_override(version)["userAgent"]


@pytest.mark.parametrize(
    "version",
    [
        {},
        {"product": VERSION["product"]},
        {**VERSION, "product": 157},
        {**VERSION, "userAgent": None},
        {**VERSION, "userAgent": []},
        {**VERSION, "product": "unknown-browser"},
        {**VERSION, "product": "Chrome/156.0.0.0"},
        {**VERSION, "userAgent": "Chrome/157.0.0.0"},
    ],
)
def test_windows_identity_never_invents_a_missing_browser_version(version):
    with pytest.raises(RuntimeError, match=r"Browser\.getVersion"):
        C._windows_chrome_ua_override(version)


@pytest.mark.parametrize("url", [OZON, "https://ozon.ru/", "https://www.ozon.ru:443/search/?text=test"])
def test_windows_identity_accepts_only_supported_ozon_origins(url):
    C._validate_ozon_compat_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://tracking.ozon.ru/",
        "https://www.ozon.ru.evil.test/",
        "https://evilozon.ru/",
        "https://wildberries.ru/",
        "http://www.ozon.ru/",
        "https://www.ozon.ru:444/",
        "https://user:secret@www.ozon.ru/",
    ],
)
async def test_windows_identity_rejects_other_origins_before_any_navigation(monkeypatch, url):
    async def forbidden_permit(_host):
        pytest.fail("an unsupported origin must fail before browser access or navigation budget")

    monkeypatch.setattr(C, "_acquire_navigation_permit", forbidden_permit)
    with pytest.raises(ValueError, match="exact HTTPS"):
        async with C.open_page(url, 0, ozon_windows_compat=True):
            pytest.fail("unsupported origin was yielded")


@pytest.fixture
def playwright_tab(monkeypatch):
    state = SimpleNamespace(events=[], status=200, fail_override=False)

    class Session:
        async def send(self, method, params=None):
            state.events.append((method, params))
            if method == "Browser.getVersion":
                return dict(VERSION)
            if state.fail_override:
                raise RuntimeError("CDP override refused")
            return {}

        async def detach(self):
            state.events.append(("detach", None))

    class Page:
        url = "about:blank"

        async def goto(self, url, **_kwargs):
            state.events.append(("goto", url))
            self.url = url
            return SimpleNamespace(status=state.status)

        @asynccontextmanager
        async def expect_navigation(self, **_kwargs):
            yield SimpleNamespace()
            raise C._PlaywrightTimeoutError("the block did not reload")

        async def close(self):
            state.events.append(("close", None))

    page = Page()

    class Context:
        async def new_cdp_session(self, target):
            assert target is page
            state.events.append(("attach", None))
            return Session()

    @asynccontextmanager
    async def context():
        yield Context()

    async def new_tab(_ctx):
        state.events.append(("new_tab", None))
        return page

    monkeypatch.setattr(C, "STEALTH", False)
    monkeypatch.setattr(C, "get_context", context)
    monkeypatch.setattr(C, "_new_tab", new_tab)
    return state


async def test_playwright_applies_override_before_goto_and_keeps_session_until_closed(playwright_tab):
    async with C._playwright_page(OZON, 0, ozon_windows_compat=True):
        names = [name for name, _ in playwright_tab.events]
        assert names == ["new_tab", "attach", "Browser.getVersion", "Network.setUserAgentOverride", "goto"]
        assert playwright_tab.events[3][1] == C._windows_chrome_ua_override(VERSION)
    assert [name for name, _ in playwright_tab.events][-2:] == ["close", "detach"]


async def test_playwright_native_default_makes_no_identity_calls(playwright_tab):
    async with C._playwright_page(OZON, 0):
        pass
    assert [name for name, _ in playwright_tab.events] == ["new_tab", "goto", "close"]


async def test_playwright_compat_keeps_real_http_403_and_closes_tab(playwright_tab):
    playwright_tab.status = 403
    with pytest.raises(C.NavBlocked) as caught:
        async with C._playwright_page(OZON, 0, ozon_windows_compat=True):
            pytest.fail("a surviving HTTP 403 must not become a page")
    assert caught.value.status == 403
    assert [name for name, _ in playwright_tab.events][-2:] == ["close", "detach"]


async def test_playwright_override_failure_cleans_up_without_navigating(playwright_tab):
    playwright_tab.fail_override = True
    with pytest.raises(RuntimeError, match="override refused"):
        async with C._playwright_page(OZON, 0, ozon_windows_compat=True):
            pytest.fail("a failed compatibility setup must not yield")
    names = [name for name, _ in playwright_tab.events]
    assert "goto" not in names
    assert names[-2:] == ["close", "detach"]


@pytest.fixture
def raw_tab(monkeypatch):
    state = SimpleNamespace(events=[], status=200)

    class RawPage:
        def __init__(self, socket, target_id):
            self.kind = socket.kind
            self.url = "about:blank"
            self._target_id = target_id

        async def _send(self, method, params=None, **_kwargs):
            state.events.append((self.kind, method, params))
            if method == "Target.createTarget":
                return {"targetId": "owned"}
            if method == "Browser.getVersion":
                return dict(VERSION)
            return {}

        async def goto_and_status(self, url, **_kwargs):
            state.events.append((self.kind, "goto", url))
            self.url = url
            return state.status

    @asynccontextmanager
    async def connect(url, **_kwargs):
        yield SimpleNamespace(kind="browser" if url.endswith("/browser") else "page")

    def discover(_url, timeout):
        return BytesIO(json.dumps([{"id": "owned", "webSocketDebuggerUrl": "ws://localhost/page"}]).encode())

    monkeypatch.setattr(C, "STEALTH", False)
    monkeypatch.setattr(C, "_browser_ws_url", lambda: "ws://localhost/browser")
    monkeypatch.setattr(C, "_websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(C, "_RawCdpPage", RawPage)
    monkeypatch.setattr(urllib.request, "urlopen", discover)
    return state


async def test_raw_applies_page_scoped_override_before_navigation_and_closes_owned_target(raw_tab):
    async with C._raw_cdp_page(OZON, 0, ozon_windows_compat=True):
        methods = [method for _, method, _ in raw_tab.events]
        assert (
            methods.index("Browser.getVersion") < methods.index("Network.setUserAgentOverride") < methods.index("goto")
        )
        override = next(entry for entry in raw_tab.events if entry[1] == "Network.setUserAgentOverride")
        assert override == ("page", "Network.setUserAgentOverride", C._windows_chrome_ua_override(VERSION))
        assert "Target.closeTarget" not in methods
    assert raw_tab.events[-1] == ("browser", "Target.closeTarget", {"targetId": "owned"})


async def test_raw_native_default_does_not_override_identity(raw_tab):
    async with C._raw_cdp_page(OZON, 0):
        pass
    assert not {"Browser.getVersion", "Network.setUserAgentOverride"} & {method for _, method, _ in raw_tab.events}


async def test_raw_compat_preserves_http_403(raw_tab):
    raw_tab.status = 403
    with pytest.raises(C.NavBlocked) as caught:
        async with C._raw_cdp_page(OZON, 0, ozon_windows_compat=True):
            pytest.fail("a raw HTTP 403 must remain a block")
    assert caught.value.status == 403
    assert raw_tab.events[-1] == ("browser", "Target.closeTarget", {"targetId": "owned"})


@pytest.mark.parametrize("final_url", [OZON, "https://other.test/"], ids=["allowed", "off-host"])
async def test_open_page_forwards_compat_to_raw_fallback_and_still_checks_final_host(monkeypatch, final_url):
    seen = []
    permit = SimpleNamespace(
        ok=lambda: seen.append("ok"), neutral=lambda: seen.append("neutral"), release=lambda: seen.append("release")
    )

    async def acquire(_host):
        return permit

    @asynccontextmanager
    async def playwright(_url, _wait_ms, **kwargs):
        assert kwargs["ozon_windows_compat"] is True
        raise C._CdpConnectTimeout("fake attach timeout")
        yield

    @asynccontextmanager
    async def raw(_url, _wait_ms, **kwargs):
        assert kwargs["ozon_windows_compat"] is True
        seen.append("raw")
        try:
            yield SimpleNamespace(url=final_url)
        finally:
            seen.append("closed")

    monkeypatch.setattr(C, "_acquire_navigation_permit", acquire)
    monkeypatch.setattr(C, "_playwright_page", playwright)
    monkeypatch.setattr(C, "_raw_cdp_page", raw)

    async def read():
        async with C.open_page(OZON, 0, ozon_windows_compat=True):
            seen.append("read")

    if final_url == OZON:
        await read()
        assert seen == ["raw", "ok", "release", "read", "closed"]
    else:
        with pytest.raises(C.NavigationPolicyError):
            await read()
        assert seen == ["raw", "neutral", "closed", "release"]


@pytest.mark.parametrize("transport", ["playwright", "raw"])
@pytest.mark.parametrize(
    ("final_url", "allowed"),
    [
        (OZON, True),
        ("https://ozon.ru/", True),
        ("http://www.ozon.ru/", False),
        ("https://www.ozon.ru:444/", False),
        ("https://other.test/", False),
    ],
    ids=["https-www", "https-bare", "http-downgrade", "other-port", "other-host"],
)
async def test_compat_rechecks_final_origin_in_both_transports(monkeypatch, transport, final_url, allowed):
    seen = []
    permit = SimpleNamespace(
        ok=lambda: seen.append("ok"),
        neutral=lambda: seen.append("neutral"),
        release=lambda: seen.append("release"),
    )

    async def acquire(_host):
        return permit

    @asynccontextmanager
    async def playwright(_url, _wait_ms, **kwargs):
        assert kwargs["ozon_windows_compat"] is True
        if transport == "raw":
            raise C._CdpConnectTimeout("fake attach timeout")
        seen.append("playwright")
        try:
            yield SimpleNamespace(url=final_url)
        finally:
            seen.append("closed")

    @asynccontextmanager
    async def raw(_url, _wait_ms, **kwargs):
        assert kwargs["ozon_windows_compat"] is True
        seen.append("raw")
        try:
            yield SimpleNamespace(url=final_url)
        finally:
            seen.append("closed")

    monkeypatch.setattr(C, "_acquire_navigation_permit", acquire)
    monkeypatch.setattr(C, "_playwright_page", playwright)
    monkeypatch.setattr(C, "_raw_cdp_page", raw)

    async def read():
        async with C.open_page(
            OZON,
            0,
            allowed_hosts={"www.ozon.ru", "ozon.ru", "other.test"},
            ozon_windows_compat=True,
        ):
            seen.append("read")

    if allowed:
        await read()
        assert seen == [transport, "ok", "release", "read", "closed"]
    else:
        with pytest.raises(C.NavigationPolicyError):
            await read()
        assert seen == [transport, "neutral", "closed", "release"]


@pytest.mark.parametrize("final_url", ["http://www.ozon.ru/", "https://www.ozon.ru:444/", "https://other.test/"])
async def test_native_mode_keeps_the_existing_final_host_policy(monkeypatch, final_url):
    seen = []
    permit = SimpleNamespace(ok=lambda: seen.append("ok"), release=lambda: seen.append("release"))

    async def acquire(_host):
        return permit

    @asynccontextmanager
    async def playwright(_url, _wait_ms, **kwargs):
        assert "ozon_windows_compat" not in kwargs
        try:
            yield SimpleNamespace(url=final_url)
        finally:
            seen.append("closed")

    monkeypatch.setattr(C, "_acquire_navigation_permit", acquire)
    monkeypatch.setattr(C, "_playwright_page", playwright)
    async with C.open_page(OZON, 0, allowed_hosts={"www.ozon.ru", "other.test"}):
        seen.append("read")
    assert seen == ["ok", "release", "read", "closed"]
