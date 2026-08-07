"""Tests for the pure helpers in ``mcp_core.transport.chrome_cdp``.

The CDP tier itself needs a real Chrome and the operator's own session, so it
cannot be tested offline. These functions can: they compute paths, candidate
binaries, ports and hints from the environment and the platform, and they are
exactly where a quiet mistake would send the connector looking for Chrome in the
wrong place on someone else's OS.

Platform-specific branches are driven by patching ``sys.platform``, so Linux CI
still covers the Windows and macOS paths.

Nothing here binds a socket, starts a process, or touches a real Chrome.
"""

from __future__ import annotations

import socket
from pathlib import Path

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
    """Chrome >= 151: Playwright cannot attach, but raw CDP answers — the probe
    must say reachable (the transport falls back), not 'down'."""

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


# ------------------------------------------------- raw-CDP fallback (Chrome >=151) ----
#
# Chrome >= 151 no longer answers Playwright's connect_over_cdp handshake, so
# open_page falls back to driving one tab over raw CDP. The wrapper JS and the
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


def test_goto_and_status_reports_a_block_page():
    """A 403 main document is a verdict open_page turns into NavBlocked."""
    import asyncio

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
