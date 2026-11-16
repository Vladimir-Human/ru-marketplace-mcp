"""Offline tests for ``start_browser``: the operator-facing Chrome launch.

``_find_chrome``, ``subprocess.Popen`` and the CDP readiness wait are faked, so
no process is spawned and no socket is opened. The contract pinned here is what
the operator gets: a visible launch by default, today's off-screen launch on
request, a refusal for a remote host, and a report instead of an exception.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp_core.transport import chrome_cdp


@pytest.fixture
def spawned(monkeypatch) -> list[list[str]]:
    """Capture spawned argument lists; ``_find_chrome`` and the profile exist."""
    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, args, **_kwargs):
            calls.append(list(args))

    monkeypatch.setattr(chrome_cdp, "_find_chrome", lambda: "/chrome")
    monkeypatch.setattr(chrome_cdp, "subprocess", type("Sub", (), {"Popen": FakePopen, "DEVNULL": -3}))
    # The workspace root already exists, so the profile mkdir is a no-op.
    monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", str(Path.cwd()))
    monkeypatch.setattr(chrome_cdp, "HEADLESS", False)
    monkeypatch.setattr(chrome_cdp, "CDP_IS_LOOPBACK", True)
    monkeypatch.setattr(chrome_cdp.sys, "platform", "win32")
    # The launch stores its process; restore the module's state afterwards.
    monkeypatch.setattr(chrome_cdp, "_last_spawn", None)
    return calls


async def _bind_now(_timeout_s: float) -> bool:
    return True


async def _never_binds(_timeout_s: float) -> bool:
    return False


@pytest.fixture
def exit_code_after_launch(monkeypatch, spawned):
    """Make the faked Chrome process pollable; the test sets what ``poll()`` returns."""
    state: dict[str, int | None] = {"code": None}

    class PolledPopen:
        def __init__(self, args, **_kwargs):
            spawned.append(list(args))

        def poll(self) -> int | None:
            return state["code"]

    monkeypatch.setattr(chrome_cdp, "subprocess", type("Sub", (), {"Popen": PolledPopen, "DEVNULL": -3}))
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)
    monkeypatch.setattr(chrome_cdp, "_wait_for_cdp_port", _never_binds)
    return state


@pytest.mark.req("R-CLI-BROWSER-START.unbound-reason")
async def test_a_chrome_that_hands_off_and_quits_names_the_holder_of_the_profile(exit_code_after_launch):
    """Exit 0 is Chrome passing its start to another Chrome on the same profile (measured 2026-11-15)."""
    exit_code_after_launch["code"] = 0

    result = await chrome_cdp.start_browser(visible=False)

    assert result["ok"] is False
    reason = str(result["reason"])
    assert "exited with code 0" in reason
    assert f"another Chrome already holds the profile {chrome_cdp.SCRAPING_PROFILE}" in reason
    assert f":{chrome_cdp.CDP_PORT}" in reason


@pytest.mark.req("R-CLI-BROWSER-START.unbound-reason")
@pytest.mark.parametrize(
    ("code", "shown"),
    [(1, "code 1 "), (3221225477, "code 3221225477 (0xC0000005)"), (-11, "code -11 (signal 11)")],
)
async def test_a_chrome_that_fails_to_start_reports_its_exit_code(exit_code_after_launch, code, shown):
    """A non-zero exit is a failed start, not another Chrome holding the profile."""
    exit_code_after_launch["code"] = code

    result = await chrome_cdp.start_browser(visible=False)

    reason = str(result["reason"])
    assert shown in reason
    assert "could not start" in reason
    assert "another Chrome" not in reason


@pytest.mark.req("R-CLI-BROWSER-START.unbound-reason")
async def test_a_chrome_still_running_is_not_blamed_on_another_chrome(exit_code_after_launch):
    """A live process holds the profile itself: it is slow or the port is taken."""
    exit_code_after_launch["code"] = None

    result = await chrome_cdp.start_browser(visible=True)

    reason = str(result["reason"])
    assert "Chrome is running" in reason
    assert "may still be starting" in reason
    assert f"port {chrome_cdp.CDP_PORT}" in reason
    assert "another Chrome" not in reason


@pytest.mark.req("R-CLI-BROWSER-START.unbound-reason")
async def test_the_connector_autostart_reports_the_same_reason(exit_code_after_launch, monkeypatch):
    """The autostart shares the reason, so a remembered failure says what Chrome did too."""
    monkeypatch.setattr(chrome_cdp, "CDP_IS_LOOPBACK", True)
    monkeypatch.setattr(chrome_cdp, "_autostart_failure", None)
    monkeypatch.setattr(chrome_cdp, "_AUTOSTART_LOCK", asyncio.Lock())
    exit_code_after_launch["code"] = 0

    ok, msg = await chrome_cdp._ensure_cdp_running(timeout_s=12.0)

    assert ok is False
    assert msg == chrome_cdp._unbound_reason(12.0)
    assert "exited with code 0" in msg


@pytest.mark.req("R-CLI-BROWSER-START")
async def test_visible_launch_has_no_off_screen_arguments(monkeypatch, spawned):
    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)
    monkeypatch.setattr(chrome_cdp, "_wait_for_cdp_port", _bind_now)
    hidden: list[bool] = []
    monkeypatch.setattr(chrome_cdp, "_hide_chrome_windows", lambda: hidden.append(True))

    result = await chrome_cdp.start_browser(visible=True)

    assert result["ok"] is True
    assert result["started"] is True
    assert result["already_running"] is False
    assert len(spawned) == 1
    args = spawned[0]
    assert "--window-position=-32000,-32000" not in args
    assert "--start-minimized" not in args
    assert "about:blank" in args
    assert hidden == []


async def test_hidden_launch_keeps_the_connector_off_screen_behaviour(monkeypatch, spawned):
    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)
    monkeypatch.setattr(chrome_cdp, "_wait_for_cdp_port", _bind_now)
    hidden: list[bool] = []
    monkeypatch.setattr(chrome_cdp, "_hide_chrome_windows", lambda: hidden.append(True))

    result = await chrome_cdp.start_browser(visible=False)

    assert result["ok"] is True
    assert result["visible"] is False
    assert result["off_screen"] is True
    args = spawned[0]
    assert "--window-position=-32000,-32000" in args
    assert "--start-minimized" in args
    assert hidden == [True]


async def test_an_answering_endpoint_spawns_nothing(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: True)

    def no_spawn(*_args, **_kwargs):
        raise AssertionError("start_browser must not spawn when CDP already answers")

    monkeypatch.setattr(chrome_cdp.subprocess, "Popen", no_spawn)

    result = await chrome_cdp.start_browser(visible=True)

    assert result["ok"] is True
    assert result["started"] is False
    assert result["already_running"] is True


async def test_a_remote_host_is_refused_without_spawning(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)
    monkeypatch.setattr(chrome_cdp, "CDP_IS_LOOPBACK", False)

    def no_spawn(*_args, **_kwargs):
        raise AssertionError("a remote CDP host must never spawn a local Chrome")

    monkeypatch.setattr(chrome_cdp.subprocess, "Popen", no_spawn)

    result = await chrome_cdp.start_browser(visible=True)

    assert result["ok"] is False
    assert result["started"] is False
    assert "loopback-only" in str(result["reason"])


@pytest.mark.req("R-CLI-BROWSER-START")
async def test_a_machine_without_chrome_is_told_to_set_the_binary(monkeypatch, spawned):
    """No Chrome is the most likely real `browser start` failure (audit S4-F22)."""
    monkeypatch.setattr(chrome_cdp, "_find_chrome", lambda: None)
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)

    result = await chrome_cdp.start_browser(visible=True)

    assert result["ok"] is False
    assert result["started"] is False
    assert "CHROME_BINARY" in str(result["reason"])
    assert spawned == []


async def test_an_unusable_scraping_profile_is_reported(monkeypatch, spawned, tmp_path):
    """The profile directory cannot be created: say so instead of "unknown" (audit S4-F22)."""
    profile = tmp_path / "profile-file"
    profile.write_text("this is a file, not a profile directory", encoding="utf-8")
    monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", str(profile))
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)

    result = await chrome_cdp.start_browser(visible=True)

    assert result["ok"] is False
    assert result["started"] is False
    reason = str(result["reason"])
    assert "unusable scraping profile" in reason
    assert profile.name in reason
    assert spawned == []


async def test_a_port_that_never_binds_names_the_profile_and_remote_debugging(monkeypatch, spawned):
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)

    async def never(_timeout_s: float) -> bool:
        return False

    monkeypatch.setattr(chrome_cdp, "_wait_for_cdp_port", never)

    result = await chrome_cdp.start_browser(visible=True)

    assert result["ok"] is False
    assert result["started"] is False
    reason = str(result["reason"])
    assert chrome_cdp.SCRAPING_PROFILE in reason
    assert "remote debugging" in reason
    assert len(spawned) == 1


@pytest.mark.req("R-CLI-BROWSER-START")
async def test_hidden_launch_without_stealth_matches_the_autostart(monkeypatch, spawned):
    """CHROME_STEALTH=0 keeps the autostart on-screen, so --hidden must too."""
    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)
    monkeypatch.setattr(chrome_cdp, "_wait_for_cdp_port", _bind_now)
    hidden: list[bool] = []
    monkeypatch.setattr(chrome_cdp, "_hide_chrome_windows", lambda: hidden.append(True))

    result = await chrome_cdp.start_browser(visible=False)

    assert result["ok"] is True
    assert result["off_screen"] is False
    assert "--window-position=-32000,-32000" not in spawned[0]
    assert hidden == []


@pytest.mark.req("R-CLI-BROWSER-START")
@pytest.mark.parametrize("platform", ["darwin", "linux"])
async def test_hidden_launch_outside_windows_is_not_reported_off_screen(monkeypatch, spawned, platform):
    """Parking the window is Windows-only, so --hidden elsewhere must not claim it (audit A4)."""
    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    monkeypatch.setattr(chrome_cdp.sys, "platform", platform)
    monkeypatch.setattr(chrome_cdp, "_running_as_root", lambda: False)
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: False)
    monkeypatch.setattr(chrome_cdp, "_wait_for_cdp_port", _bind_now)
    hidden: list[bool] = []
    monkeypatch.setattr(chrome_cdp, "_hide_chrome_windows", lambda: hidden.append(True))

    result = await chrome_cdp.start_browser(visible=False)

    assert result["ok"] is True
    assert result["off_screen"] is False
    assert "--window-position=-32000,-32000" not in spawned[0]
    assert hidden == []


class _TabChannel:
    """Fake browser-level channel; ``fail_on`` makes that createTarget call fail."""

    def __init__(self, fail_on: int | None = None, reply: dict | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.fail_on = fail_on
        self.reply = reply

    async def _send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append((method, dict(params or {})))
        if self.fail_on == len(self.sent):
            raise RuntimeError("CDP Target.createTarget: refused")
        return self.reply if self.reply is not None else {"targetId": f"t{len(self.sent)}"}


def _fake_channel(monkeypatch, channel: _TabChannel) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake():
        yield channel

    monkeypatch.setattr(chrome_cdp, "_browser_cdp_channel", fake)


@pytest.mark.req("R-CLI-UNBLOCK")
async def test_open_tabs_creates_one_new_target_per_url_and_navigates_nothing(monkeypatch):
    channel = _TabChannel()
    _fake_channel(monkeypatch, channel)

    result = await chrome_cdp.open_tabs(["https://a.example/", "https://b.example/"])

    assert result == {"ok": True, "target_ids": ["t1", "t2"], "reason": None}
    assert channel.sent == [
        ("Target.createTarget", {"url": "https://a.example/"}),
        ("Target.createTarget", {"url": "https://b.example/"}),
    ]


async def test_open_tabs_reports_how_many_tabs_stayed_open_before_a_failure(monkeypatch):
    _fake_channel(monkeypatch, _TabChannel(fail_on=2))

    result = await chrome_cdp.open_tabs(["https://a.example/", "https://b.example/"])

    assert result["ok"] is False
    assert result["target_ids"] == ["t1"]
    assert "opened 1 of 2 tab(s)" in result["reason"]
    assert "refused" in result["reason"]


async def test_open_tabs_treats_a_reply_without_a_target_id_as_a_failure(monkeypatch):
    _fake_channel(monkeypatch, _TabChannel(reply={}))

    result = await chrome_cdp.open_tabs(["https://a.example/"])

    assert result["ok"] is False
    assert result["target_ids"] == []
    assert "no target id" in result["reason"]


@pytest.mark.req("R-CLI-UNBLOCK")
@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/System32/config/SAM",
        "javascript:alert(1)",
        "chrome://settings",
        "data:text/html,<b>x</b>",
        "ftp://a.example/file",
    ],
)
async def test_open_tabs_refuses_a_non_http_url_before_any_cdp_command(monkeypatch, url):
    """The scheme guard open_page has, open_tabs needs too (audit S2-F7).

    An authenticated browser must never be aimed at ``file:///``; the refusal
    happens before the first CDP command, so no tab is opened at all.
    """
    channel = _TabChannel()
    _fake_channel(monkeypatch, channel)

    with pytest.raises(ValueError, match="open_tabs refuses a non-http"):
        await chrome_cdp.open_tabs(["https://a.example/", url])

    assert channel.sent == []
