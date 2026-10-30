"""Offline helpers and fake attach/raw-CDP tests for ``chrome_cdp``.

The helpers compute paths, candidate binaries, ports and environment hints;
fake Playwright and HTTP objects reproduce attach-timeout diagnostics without
an operator session.

Platform-specific branches are driven by patching ``sys.platform``, so Linux CI
still covers the Windows and macOS paths.

Nothing here binds a socket, starts a process, or touches a real Chrome.
"""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from mcp_core.transport import chrome_cdp

# ------------------------------------------------------------------- port ----


def test_port_defaults_to_9222_when_unset(monkeypatch):
    monkeypatch.delenv("CHROME_CDP_PORT", raising=False)
    assert chrome_cdp._port_from_env() == 9222


def test_port_reads_the_environment(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_PORT", "9333")
    assert chrome_cdp._port_from_env() == 9333


@pytest.mark.parametrize("bad", ["", "not-a-number", "9222.5", "0", "-1", "65536", "99999"])
def test_a_nonsense_port_falls_back_to_the_default(monkeypatch, bad):
    """A typo'd port must not become a connection attempt to port 0 or 99999."""
    monkeypatch.setenv("CHROME_CDP_PORT", bad)
    assert chrome_cdp._port_from_env() == 9222


@pytest.mark.parametrize("edge", ["1", "65535"])
def test_the_valid_port_range_is_inclusive(monkeypatch, edge):
    monkeypatch.setenv("CHROME_CDP_PORT", edge)
    assert chrome_cdp._port_from_env() == int(edge)


# ------------------------------------------------------------------- host ----


def test_host_defaults_to_loopback_when_unset(monkeypatch):
    monkeypatch.delenv("CHROME_CDP_HOST", raising=False)
    assert chrome_cdp._host_from_env() == "127.0.0.1"


def test_host_reads_the_environment(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_HOST", "host.docker.internal")
    assert chrome_cdp._host_from_env() == "host.docker.internal"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "http://127.0.0.1",
        "127.0.0.1:9222",
        "user:pass@proxy",
        "127.0.0.1/path",
        "127 .0.0.1",
    ],
)
def test_a_malformed_host_falls_back_to_loopback(monkeypatch, bad):
    """A host with a scheme, port, credentials or path must never reach the dialer."""
    monkeypatch.setenv("CHROME_CDP_HOST", bad)
    assert chrome_cdp._host_from_env() == "127.0.0.1"


def test_an_ipv6_host_with_colons_is_kept(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_HOST", "::1")
    assert chrome_cdp._host_from_env() == "::1"


@pytest.mark.parametrize("spelling", ["::1", "[::1]"])
def test_an_ipv6_host_dials_in_brackets_and_stays_loopback(monkeypatch, spelling):
    """(audit S1-F7) Bare ::1 used to build http://::1:9222; [::1] used to count as remote."""
    monkeypatch.setenv("CHROME_CDP_HOST", spelling)
    host = chrome_cdp._host_from_env()

    assert host == "::1"
    assert host in chrome_cdp._LOOPBACK_HOSTS
    url = urlsplit(f"http://{chrome_cdp._url_host(host)}:9222/json/version")
    assert (url.hostname, url.port) == ("::1", 9222)


@pytest.mark.parametrize("bad", ["[::1", "::1]", "[[::1]]"])
def test_a_half_bracketed_ipv6_host_falls_back_to_loopback(monkeypatch, bad):
    monkeypatch.setenv("CHROME_CDP_HOST", bad)
    assert chrome_cdp._host_from_env() == "127.0.0.1"


def test_the_browser_websocket_of_an_ipv6_host_is_bracketed(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "CDP_HOST", "::1")
    monkeypatch.setattr(chrome_cdp, "CDP_PORT", 9222)
    monkeypatch.setattr(
        chrome_cdp,
        "_cdp_http_json",
        lambda path, timeout_s: {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"},
    )

    assert chrome_cdp._browser_ws_url() == "ws://[::1]:9222/devtools/browser/x"


def test_cdp_url_uses_the_configured_host(monkeypatch):
    """The module-level URL is built at import time; rebuild it from the helpers."""
    monkeypatch.setenv("CHROME_CDP_HOST", "chrome-sidecar")
    monkeypatch.setenv("CHROME_CDP_PORT", "9223")
    host = chrome_cdp._host_from_env()
    port = chrome_cdp._port_from_env()
    assert f"http://{host}:{port}" == "http://chrome-sidecar:9223"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_recognised(host):
    assert host in chrome_cdp._LOOPBACK_HOSTS


# ---------------------------------------------------------- profile paths ----


def test_windows_profile_dir_uses_localappdata(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\op\AppData\Local")

    result = chrome_cdp._default_profile_dir()

    assert result == Path(r"C:\Users\op\AppData\Local") / "Chrome-Scraping"


def test_windows_profile_dir_falls_back_to_home_without_localappdata(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert chrome_cdp._default_profile_dir().name == "Chrome-Scraping"


def test_macos_profile_dir_uses_application_support(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")

    result = chrome_cdp._default_profile_dir()

    assert result == Path.home() / "Library" / "Application Support" / "Chrome-Scraping"


def test_linux_profile_dir_honours_xdg_data_home(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/share")

    assert chrome_cdp._default_profile_dir() == Path("/custom/share/chrome-scraping")


def test_linux_profile_dir_defaults_to_local_share(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    assert chrome_cdp._default_profile_dir() == Path.home() / ".local" / "share" / "chrome-scraping"


# ------------------------------------------------------ chrome candidates ----


def test_an_explicit_binary_override_is_tried_first(monkeypatch):
    monkeypatch.setenv("CHROME_BINARY", "/opt/my-chrome")
    monkeypatch.setattr(chrome_cdp.sys, "platform", "linux")
    monkeypatch.setattr(chrome_cdp.shutil, "which", lambda _name: None)

    assert chrome_cdp._chrome_candidates()[0] == "/opt/my-chrome"


def test_candidates_never_contain_empty_entries(monkeypatch):
    """An unset ProgramFiles must not yield a path starting with a bare slash."""
    monkeypatch.delenv("CHROME_BINARY", raising=False)
    monkeypatch.setattr(chrome_cdp.sys, "platform", "linux")
    monkeypatch.setattr(chrome_cdp.shutil, "which", lambda _name: None)

    candidates = chrome_cdp._chrome_candidates()

    assert candidates
    assert all(candidate for candidate in candidates)


def test_windows_candidates_cover_chrome_and_edge(monkeypatch):
    monkeypatch.delenv("CHROME_BINARY", raising=False)
    monkeypatch.setattr(chrome_cdp.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\op\AppData\Local")

    candidates = chrome_cdp._chrome_candidates()

    assert any("chrome.exe" in c for c in candidates)
    assert any("msedge.exe" in c for c in candidates)
    # The x86 location is a real install target on 64-bit Windows.
    assert any("Program Files (x86)" in c for c in candidates)
    # Per-user installs live under LOCALAPPDATA and are easy to forget.
    assert any(r"AppData\Local" in c for c in candidates)


def test_windows_candidates_survive_missing_program_files_vars(monkeypatch):
    """ProgramFiles(x86) genuinely does not exist in an upper-cased form."""
    monkeypatch.delenv("CHROME_BINARY", raising=False)
    monkeypatch.setattr(chrome_cdp.sys, "platform", "win32")
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    candidates = chrome_cdp._chrome_candidates()

    assert candidates
    assert all(c.startswith("C:\\") for c in candidates)


def test_macos_candidates_include_a_per_user_install(monkeypatch):
    monkeypatch.delenv("CHROME_BINARY", raising=False)
    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")

    candidates = chrome_cdp._chrome_candidates()

    assert any(c.startswith("/Applications/Google Chrome.app") for c in candidates)
    assert any(str(Path.home()) in c for c in candidates)


def test_linux_candidates_prefer_a_resolved_path_over_a_guess(monkeypatch):
    """A PATH lookup beats a hardcoded /usr/bin guess, and both are offered."""
    monkeypatch.delenv("CHROME_BINARY", raising=False)
    monkeypatch.setattr(chrome_cdp.sys, "platform", "linux")
    monkeypatch.setattr(
        chrome_cdp.shutil,
        "which",
        lambda name: "/nix/store/abc/bin/chromium" if name == "chromium" else None,
    )

    candidates = chrome_cdp._chrome_candidates()

    assert candidates.index("/nix/store/abc/bin/chromium") < candidates.index("/usr/bin/chromium")


# ------------------------------------------------------------ setup hints ----


def test_setup_hint_names_the_powershell_script_on_windows(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "win32")

    hint = chrome_cdp.cdp_setup_hint()

    assert "start_chrome_cdp.ps1" in hint
    assert ".sh" not in hint


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_setup_hint_names_the_shell_script_elsewhere(monkeypatch, platform):
    monkeypatch.setattr(chrome_cdp.sys, "platform", platform)

    hint = chrome_cdp.cdp_setup_hint()

    assert "start_chrome_cdp.sh" in hint


def test_setup_hint_points_at_a_script_that_exists():
    """A hint naming a missing script would send the operator nowhere."""
    repo_root = Path(__file__).resolve().parents[3]
    assert (repo_root / "scripts" / "start_chrome_cdp.sh").is_file()
    assert (repo_root / "scripts" / "start_chrome_cdp.ps1").is_file()


# -------------------------------------------------------------- find chrome ----


def test_find_chrome_returns_the_first_existing_candidate(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "_chrome_candidates", lambda: ["/nope/one", "/yes/two", "/nope/three"])
    monkeypatch.setattr(chrome_cdp.Path, "exists", lambda self: str(self).replace("\\", "/") == "/yes/two")

    assert chrome_cdp._find_chrome() == "/yes/two"


def test_find_chrome_returns_none_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "_chrome_candidates", lambda: ["/nope/one", "/nope/two"])
    monkeypatch.setattr(chrome_cdp.Path, "exists", lambda self: False)

    assert chrome_cdp._find_chrome() is None


# --------------------------------------------------------------- port probe ----


def test_port_probe_reports_false_when_nothing_listens(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr(chrome_cdp.socket, "create_connection", refuse)
    assert chrome_cdp._cdp_port_open() is False


def test_port_probe_treats_a_timeout_as_closed(monkeypatch):
    def time_out(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(chrome_cdp.socket, "create_connection", time_out)
    assert chrome_cdp._cdp_port_open() is False


def test_port_probe_treats_an_os_error_as_closed(monkeypatch):
    """An unreachable network or bad address is 'no CDP', not a crash."""

    def blow_up(*_args, **_kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(chrome_cdp.socket, "create_connection", blow_up)
    assert chrome_cdp._cdp_port_open() is False


def test_port_probe_reports_true_and_closes_its_socket(monkeypatch):
    closed = {"value": False}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed["value"] = True
            return False

    monkeypatch.setattr(chrome_cdp.socket, "create_connection", lambda *a, **k: FakeSocket())

    assert chrome_cdp._cdp_port_open() is True
    assert closed["value"], "the probe must not leak a socket"


def test_port_probe_targets_loopback_only(monkeypatch):
    """Probing a remote host would be a scan; CDP is always local here."""
    seen = {}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def record(address, timeout=None):
        seen["address"] = address
        seen["timeout"] = timeout
        return FakeSocket()

    monkeypatch.setattr(chrome_cdp.socket, "create_connection", record)
    chrome_cdp._cdp_port_open()

    assert seen["address"][0] == "127.0.0.1"
    assert seen["timeout"] is not None, "an unbounded probe could hang a tool call"


# --------------------------------------------------------------- root check ----


def test_root_check_is_false_for_an_ordinary_user(monkeypatch):
    monkeypatch.setattr(chrome_cdp.os, "geteuid", lambda: 1000, raising=False)
    assert chrome_cdp._running_as_root() is False


def test_root_check_is_true_for_uid_zero(monkeypatch):
    """Chrome refuses --no-sandbox-less startup as root, so this must be detected."""
    monkeypatch.setattr(chrome_cdp.os, "geteuid", lambda: 0, raising=False)
    assert chrome_cdp._running_as_root() is True


def test_root_check_handles_platforms_without_geteuid(monkeypatch):
    """os.geteuid does not exist on Windows."""
    monkeypatch.delattr(chrome_cdp.os, "geteuid", raising=False)
    assert chrome_cdp._running_as_root() is False


# ------------------------------------------------------------- module state ----


def test_cdp_url_is_loopback():
    assert chrome_cdp.CDP_URL.startswith("http://127.0.0.1:")


def test_nav_fail_statuses_cover_blocks_and_gateway_errors():
    """Playwright resolves goto() for these, so a block page could reach a parser."""
    for status in (401, 403, 407, 429, 500, 502, 503, 504):
        assert status in chrome_cdp._NAV_FAIL_STATUSES
    # A 200 and a redirect are not navigation failures.
    assert 200 not in chrome_cdp._NAV_FAIL_STATUSES
    assert 302 not in chrome_cdp._NAV_FAIL_STATUSES


def test_nav_blocked_carries_the_status_and_url():
    exc = chrome_cdp.NavBlocked(403, "https://www.ozon.ru/product/1/")
    assert exc.status == 403
    assert "403" in str(exc)


def test_socket_module_is_the_real_one():
    """Guards against a monkeypatch leaking out of the tests above."""
    assert chrome_cdp.socket is socket


# ----------------------------------------------------------- attach timeout ----


@pytest.mark.parametrize("page_count", [0, 10])
async def test_attach_timeout_reports_open_tabs_and_preserves_raw_fallback(monkeypatch, page_count):
    import json
    import urllib.request
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from mcp_core.errors import TransportDownError

    calls = []

    async def connect(url, *, timeout):
        assert (url, timeout) == (chrome_cdp.CDP_URL, 10_000)
        raise chrome_cdp._PlaywrightTimeoutError("attach stalled")

    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return json.dumps([{"type": "page"}] * page_count + [{"type": "service_worker"}]).encode()

    def urlopen(url, *, timeout):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: True)
    monkeypatch.setattr(chrome_cdp, "async_playwright", playwright)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(chrome_cdp._CdpConnectTimeout) as caught:
        async with chrome_cdp.get_browser():
            pytest.fail("a failed attach must not yield a browser")

    message = str(caught.value)
    assert "timed out after 10 s" in message
    assert f"{page_count} tabs are open in the scraping Chrome" in message
    assert "close tabs" in message.lower() and "retry" in message.lower()
    assert "version" not in message.lower() and "newer" not in message.lower()
    assert isinstance(caught.value, TransportDownError)
    assert isinstance(caught.value, RuntimeError)
    assert caught.value.to_dict()["error"] == "transport_down"
    assert calls == [(f"{chrome_cdp.CDP_URL}/json/list", 2.0)]


async def test_open_page_keeps_the_tab_count_when_raw_fallback_fails_too(monkeypatch):
    """When the raw-CDP fallback also fails, the error still names the open tabs."""
    from contextlib import asynccontextmanager

    from mcp_core.errors import TransportDownError

    @asynccontextmanager
    async def attach_times_out(_url, _wait_ms):
        raise chrome_cdp._CdpConnectTimeout("attach timed out after 10 s; 12 tabs are open in the scraping Chrome.")
        yield

    @asynccontextmanager
    async def raw_fails(_url, _wait_ms):
        raise TimeoutError("raw navigation stalled")
        yield

    monkeypatch.setattr(chrome_cdp, "_playwright_page", attach_times_out)
    monkeypatch.setattr(chrome_cdp, "_raw_cdp_page", raw_fails)

    with pytest.raises(TransportDownError) as caught:
        async with chrome_cdp.open_page("https://example.com/", 0):
            pytest.fail("a failed fallback must not yield a page")

    message = str(caught.value)
    assert "12 tabs are open" in message
    assert "raw-CDP fallback failed too: TimeoutError" in message


@pytest.mark.parametrize("failure", ["network", "invalid_json", "not_a_list"])
async def test_attach_timeout_reports_unavailable_tab_count_neutrally(monkeypatch, failure):
    import urllib.request
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    async def connect(*_args, **_kwargs):
        raise chrome_cdp._PlaywrightTimeoutError("attach stalled")

    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return b"not JSON" if failure == "invalid_json" else b"{}"

    def urlopen(*_args, **_kwargs):
        if failure == "network":
            raise OSError("private upstream details must not leak")
        return Response()

    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: True)
    monkeypatch.setattr(chrome_cdp, "async_playwright", playwright)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(chrome_cdp._CdpConnectTimeout) as caught:
        async with chrome_cdp.get_browser():
            pytest.fail("a failed attach must not yield a browser")

    message = str(caught.value)
    assert "timed out after 10 s" in message
    assert "open tab count could not be obtained" in message
    assert "close tabs" in message.lower() and "retry" in message.lower()
    assert "version" not in message.lower() and "newer" not in message.lower()
    assert "private upstream" not in message


# ------------------------------------------------------------- probe_session ----


def test_probe_session_reports_unreachable_when_port_closed(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)
    import asyncio

    result = asyncio.run(chrome_cdp.probe_session())

    assert result["reachable"] is False
    assert "nothing listening" in str(result["reason"])


def test_probe_session_never_raises(monkeypatch):
    """A wedged browser must surface as reachable=False, not an exception."""
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: True)

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("wedged")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(chrome_cdp, "get_browser", lambda: _Boom())
    import asyncio

    result = asyncio.run(chrome_cdp.probe_session())

    assert result["reachable"] is False
    assert "wedged" in str(result["reason"])


def test_probe_session_reports_reachable_when_only_the_playwright_attach_fails(monkeypatch):
    """A timed-out Playwright attach can leave raw CDP usable, not 'down'."""

    class _AttachFails:
        async def __aenter__(self):
            raise chrome_cdp._CdpConnectTimeout("handshake unsupported")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: True)
    monkeypatch.setattr(chrome_cdp, "get_browser", lambda: _AttachFails())
    monkeypatch.setattr(chrome_cdp, "_raw_page_count", lambda: 7)
    import asyncio

    result = asyncio.run(chrome_cdp.probe_session())

    assert result["reachable"] is True
    assert result["contexts"] == 7
    assert "raw CDP" in str(result["reason"])


# ----------------------------------------------------------- raw-CDP fallback ----
#
# When Playwright's attach times out, open_page falls back to driving one tab
# over raw CDP. The wrapper JS and the
# page shim are testable offline; the websocket below is a fake that replays
# canned CDP messages.


def _run_js_expression(expression: str) -> object:
    """Execute a JS expression in Node and return its awaited JSON value."""
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is present wherever jsdom tests run
        pytest.skip("node not available")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "probe.js"
        script.write_text(
            "const out = (" + expression + ");\n"
            "Promise.resolve(out).then((v) => process.stdout.write(JSON.stringify({v: v === undefined ? null : v})));\n",
            encoding="utf-8",
        )
        result = subprocess.run([node, str(script)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")[:400]
    import json as _json

    return _json.loads(result.stdout.decode("utf-8"))["v"]


def test_evaluate_wrapper_invokes_a_function_with_the_arg():
    wrapper = chrome_cdp._evaluate_expression("(args) => args.a + 1", {"a": 41})
    assert _run_js_expression(wrapper) == 42


def test_evaluate_wrapper_awaits_async_functions():
    """The connectors' fetch-in-page scripts are async arrow functions."""
    wrapper = chrome_cdp._evaluate_expression("async (args) => { return args.x * 2; }", {"x": 21})
    assert _run_js_expression(wrapper) == 42


def test_evaluate_wrapper_calls_no_arg_functions():
    wrapper = chrome_cdp._evaluate_expression("() => 'привет'", None)
    assert _run_js_expression(wrapper) == "привет"


def test_evaluate_wrapper_passes_plain_expressions_through():
    wrapper = chrome_cdp._evaluate_expression("1 + 2", None)
    assert _run_js_expression(wrapper) == 3


def test_evaluate_wrapper_escapes_json_in_the_arg():
    """Args travel as a JSON literal spliced into JS — quotes and backslashes
    in string values must survive verbatim."""
    wrapper = chrome_cdp._evaluate_expression("(args) => args.text", {"text": 'he said "hi" \\ and <t>'})
    assert _run_js_expression(wrapper) == 'he said "hi" \\ and <t>'


class _FakeWs:
    """Replays canned CDP messages; records everything sent."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.sent: list[dict] = []

    async def send(self, message):
        import json as _json

        self.sent.append(_json.loads(message))

    async def recv(self):
        import asyncio as _asyncio
        import json as _json

        await _asyncio.sleep(0)
        if not self._responses:
            await _asyncio.Event().wait()
        return _json.dumps(self._responses.pop(0))


def test_raw_page_evaluate_sends_runtime_evaluate_and_returns_the_value():
    import asyncio

    ws = _FakeWs([{"id": 1, "result": {"result": {"type": "string", "value": "ok"}}}])
    page = chrome_cdp._RawCdpPage(ws, "T1")

    assert asyncio.run(page.evaluate("() => 'ok'")) == "ok"
    sent = ws.sent[0]
    assert sent["method"] == "Runtime.evaluate"
    assert sent["params"]["awaitPromise"] is True
    assert sent["params"]["returnByValue"] is True


def test_raw_page_evaluate_raises_on_exception_details():
    import asyncio

    ws = _FakeWs(
        [{"id": 1, "result": {"exceptionDetails": {"text": "Uncaught", "exception": {"description": "Error: boom"}}}}]
    )
    page = chrome_cdp._RawCdpPage(ws, "T1")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(page.evaluate("() => { throw new Error('boom'); }"))


def test_raw_page_tracks_the_main_frame_url_and_ignores_subframes():
    page = chrome_cdp._RawCdpPage(_FakeWs([]), "T1")

    page._note_event(
        {"method": "Page.frameNavigated", "params": {"frame": {"url": "https://x/child", "parentId": "F0"}}}
    )
    assert page.url == "about:blank"

    page._note_event({"method": "Page.frameNavigated", "params": {"frame": {"url": "https://x/main"}}})
    assert page.url == "https://x/main"


def test_goto_and_status_returns_the_last_document_status_and_stops_on_load():
    import asyncio

    ws = _FakeWs(
        [
            {"id": 1, "result": {"frameId": "F"}},
            {
                "method": "Network.responseReceived",
                "params": {"type": "Document", "response": {"status": 307, "url": "https://x/hop"}},
            },
            {
                "method": "Network.responseReceived",
                "params": {"type": "Script", "response": {"status": 500, "url": "https://x/app.js"}},
            },
            {
                "method": "Network.responseReceived",
                "params": {"type": "Document", "response": {"status": 200, "url": "https://x/final"}},
            },
            {"method": "Page.loadEventFired", "params": {}},
        ]
    )
    page = chrome_cdp._RawCdpPage(ws, "T1")

    assert asyncio.run(page.goto_and_status("https://x/")) == 200
    assert page.url == "https://x/final"


def test_goto_and_status_reports_a_block_page(monkeypatch):
    """A 403 that outlives the anti-bot recheck is a verdict open_page turns into NavBlocked."""
    import asyncio

    monkeypatch.setattr(chrome_cdp, "_ANTIBOT_RECHECK_S", 0.05)

    ws = _FakeWs(
        [
            {"id": 1, "result": {"frameId": "F"}},
            {
                "method": "Network.responseReceived",
                "params": {"type": "Document", "response": {"status": 403, "url": "https://x/blocked"}},
            },
            {"method": "Page.loadEventFired", "params": {}},
        ]
    )
    page = chrome_cdp._RawCdpPage(ws, "T1")

    assert asyncio.run(page.goto_and_status("https://x/")) == 403


def test_goto_and_status_waits_out_an_antibot_reload():
    """Ozon's 403 + self-reloading JS check ends on the real page, not a block."""
    import asyncio

    ws = _FakeWs(
        [
            {"id": 1, "result": {"frameId": "F"}},
            {"method": "Network.responseReceived", "params": {"type": "Document", "response": {"status": 403}}},
            {"method": "Page.loadEventFired", "params": {}},
            {
                "method": "Network.responseReceived",
                "params": {"type": "Document", "response": {"status": 200, "url": "https://x/real"}},
            },
            {"method": "Page.loadEventFired", "params": {}},
        ]
    )
    page = chrome_cdp._RawCdpPage(ws, "T1")

    assert asyncio.run(page.goto_and_status("https://x/")) == 200
    assert page.url == "https://x/real"


def test_goto_and_status_ignores_a_subframe_document(monkeypatch):
    """A 200 iframe on a 403 block page is not the page's status or URL."""
    import asyncio

    monkeypatch.setattr(chrome_cdp, "_ANTIBOT_RECHECK_S", 0.05)

    ws = _FakeWs(
        [
            {"id": 1, "result": {"frameId": "F"}},
            {
                "method": "Network.responseReceived",
                "params": {"type": "Document", "frameId": "F", "response": {"status": 403, "url": "https://x/"}},
            },
            {"method": "Page.loadEventFired", "params": {}},
            {
                "method": "Network.responseReceived",
                "params": {"type": "Document", "frameId": "IF", "response": {"status": 200, "url": "https://widget/"}},
            },
        ]
    )
    page = chrome_cdp._RawCdpPage(ws, "T1")

    assert asyncio.run(page.goto_and_status("https://x/")) == 403
    assert page.url == "https://x/"


class _FakeNavigation:
    """page.expect_navigation(): resolves to a response, to None, or times out."""

    def __init__(self, page, outcome):
        self._page = page
        self._outcome = outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self._outcome == "timeout":
            raise chrome_cdp._PlaywrightTimeoutError("no navigation")
        if self._outcome is not None:
            self._page.url = "https://shop.test/real"
        return False

    @property
    async def value(self):
        from types import SimpleNamespace

        return None if self._outcome is None else SimpleNamespace(status=self._outcome)


class _FakeNavPage:
    """Just enough of a Playwright page for _playwright_page's 403 recheck."""

    def __init__(self, outcome):
        self.url = "https://shop.test/p"
        self._outcome = outcome
        self.waits: list[dict] = []

    async def goto(self, url, **_kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(status=403)

    def expect_navigation(self, **kwargs):
        self.waits.append(kwargs)
        return _FakeNavigation(self, self._outcome)

    async def wait_for_timeout(self, _ms):
        return None

    async def close(self):
        return None


def _patch_playwright_tab(monkeypatch, fake_page):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_context():
        yield object()

    async def fake_new_tab(_ctx):
        return fake_page

    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "get_context", fake_context)
    monkeypatch.setattr(chrome_cdp, "_new_tab", fake_new_tab)


async def test_playwright_403_recovers_when_the_main_frame_reloads_to_200(monkeypatch):
    """The recheck waits for the new document to land, so page.url is already the new one."""
    fake_page = _FakeNavPage(outcome=200)
    _patch_playwright_tab(monkeypatch, fake_page)

    async with chrome_cdp._playwright_page("https://shop.test/p", wait_ms=0) as page:
        assert page.url == "https://shop.test/real"

    assert fake_page.waits == [{"wait_until": "domcontentloaded", "timeout": chrome_cdp._ANTIBOT_RECHECK_S * 1000}]


@pytest.mark.parametrize("outcome", ["timeout", None, 403], ids=["no-reload", "history-api", "reload-403"])
async def test_playwright_403_stays_a_block_without_a_good_reload(monkeypatch, outcome):
    """No navigation, a History API change or another 403 all end as NavBlocked(403)."""
    _patch_playwright_tab(monkeypatch, _FakeNavPage(outcome=outcome))

    with pytest.raises(chrome_cdp.NavBlocked) as blocked:
        async with chrome_cdp._playwright_page("https://shop.test/p", wait_ms=0):
            pass

    assert blocked.value.status == 403
