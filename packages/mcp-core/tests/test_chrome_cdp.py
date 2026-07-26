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
