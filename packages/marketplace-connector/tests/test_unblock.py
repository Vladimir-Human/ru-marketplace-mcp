"""Offline tests for ``marketplace-mcp unblock``.

The browser is faked end to end: ``start_browser``, the browser-level CDP
channel and ``show_browser_window`` are replaced, so no Chrome is launched and
no page is opened. The tests pin the operator contract: the map covers exactly
the browser-backed doctor sources, one new tab per source, the first new tab is
the one shown, and the re-check runs only the chosen sources.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from marketplace_connector import cli, unblock
from marketplace_connector.capabilities import CAPABILITIES, browser_backed


async def _healthy(name: str, _module: str = "", _tool: str = ""):
    return name, "success", "search:healthy", {"search": {"state": "healthy"}}


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def _send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append((method, dict(params or {})))
        return {"targetId": f"target-{len(self.sent)}"}


@pytest.fixture
def fake_browser(monkeypatch):
    """Fake the launch, the tab creation and the window raise; record them."""
    from mcp_core.transport import chrome_cdp

    events: dict[str, list] = {"shown": [], "started": []}
    channel = FakeChannel()

    async def fake_start(*, visible: bool = True) -> dict:
        events["started"].append(visible)
        return {"ok": True, "started": True, "already_running": False, "host": "127.0.0.1", "port": 9222}

    @asynccontextmanager
    async def fake_channel():
        yield channel

    async def fake_show(*, target_id: str | None = None) -> dict:
        events["shown"].append(target_id)
        return {"ok": True, "reachable": True, "target_id": target_id or "target-1"}

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)
    monkeypatch.setattr(chrome_cdp, "_browser_cdp_channel", fake_channel)
    monkeypatch.setattr(chrome_cdp, "show_browser_window", fake_show)
    return channel, events


def test_the_map_covers_exactly_the_browser_backed_doctor_sources():
    doctor_browser_sources = {
        name for name, _module, _tool in cli._SELFCHECKS if browser_backed(CAPABILITIES.get(name))
    }

    assert set(unblock.UNBLOCK_PAGES) == doctor_browser_sources


def test_a_named_non_browser_source_exits_two(capsys):
    assert cli.cmd_unblock(["wildberries"]) == 2

    err = capsys.readouterr().err
    assert "not a browser source" in err
    assert "marketplace-mcp doctor wildberries" in err


def test_dry_run_lists_pages_and_performs_no_io(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    def forbidden(*_args, **_kwargs):
        raise AssertionError("--dry-run must not start or show a browser")

    monkeypatch.setattr(chrome_cdp, "start_browser", forbidden)
    monkeypatch.setattr(chrome_cdp, "show_browser_window", forbidden)

    assert cli.cmd_unblock(["ozon", "avito", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert unblock.UNBLOCK_PAGES["ozon"] in out
    assert unblock.UNBLOCK_PAGES["avito"] in out
    assert "Opens 2 page(s), one per source" in out
    assert "Dry run" in out


def test_dry_run_without_names_exits_two(capsys):
    assert cli.cmd_unblock(["--dry-run"]) == 2
    assert "needs source names" in capsys.readouterr().err


def test_auto_pick_takes_only_sources_with_a_wall_reason(monkeypatch, fake_browser, capsys):
    channel, _events = fake_browser
    calls: list[str] = []

    async def fake_selfcheck(name: str, module: str, tool: str):
        calls.append(name)
        # Blocked during the auto-pick pass, healthy after the human acted.
        if name == "ozon" and calls.count("ozon") == 1:
            return name, "inconclusive", "search:blocked", {"search": {"state": "inconclusive", "reason": "blocked"}}
        return await _healthy(name, module, tool)

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cli, "_probe_cdp", lambda: (True, "Chrome CDP: reachable."))

    assert cli.cmd_unblock(["--wait", "0"]) == 0

    opens = [params["url"] for method, params in channel.sent if method == "Target.createTarget"]
    assert opens == [unblock.UNBLOCK_PAGES["ozon"]]
    assert "ozon" in calls
    # The auto-pick pass and the re-check pass both ran; no other source was opened.
    assert calls.count("ozon") == 2


def test_named_sources_open_one_new_tab_each_and_show_the_first(monkeypatch, fake_browser, capsys):
    channel, events = fake_browser
    checked: list[str] = []

    async def fake_selfcheck(name: str, module: str, tool: str):
        checked.append(name)
        return await _healthy(name, module, tool)

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cli, "_probe_cdp", lambda: (True, "Chrome CDP: reachable."))

    assert cli.cmd_unblock(["ozon", "avito", "--wait", "0"]) == 0

    methods = [method for method, _params in channel.sent]
    assert methods == ["Target.createTarget", "Target.createTarget"]
    assert "Page.navigate" not in methods
    urls = [params["url"] for _method, params in channel.sent]
    assert urls == [unblock.UNBLOCK_PAGES["ozon"], unblock.UNBLOCK_PAGES["avito"]]
    assert events["started"] == [True]
    assert events["shown"] == ["target-1"]
    assert checked == ["ozon", "avito"]

    out = capsys.readouterr().out
    assert "Opens 2 page(s), one per source" in out
    assert unblock.UNBLOCK_PAGES["ozon"] in out
    assert "Re-check after the manual step:" in out


def test_recheck_waits_via_input_on_a_tty(monkeypatch, fake_browser, capsys):
    monkeypatch.setattr(cli, "_run_one_selfcheck", _healthy)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr(cli, "_probe_cdp", lambda: (True, "Chrome CDP: reachable."))
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(seconds))

    assert cli.cmd_unblock(["dns"]) == 0

    assert slept == []


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        ("drift_detected", "shape_drift", 1),
        ("inconclusive", "blocked", 2),
    ],
)
def test_exit_codes_follow_doctor(monkeypatch, fake_browser, status, reason, expected):
    async def fake_selfcheck(name: str, module: str, tool: str):
        return name, status, f"search:{reason}", {"search": {"state": status, "reason": reason}}

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cli, "_probe_cdp", lambda: (True, "Chrome CDP: reachable."))

    assert cli.cmd_unblock(["ozon", "--wait", "0"]) == expected


def test_unblock_rejects_unknown_options_and_wait_values(capsys):
    assert cli.cmd_unblock(["ozon", "--wait"]) == 2
    assert "--wait requires seconds" in capsys.readouterr().err

    assert cli.cmd_unblock(["ozon", "--wait", "soon"]) == 2
    assert "needs a number" in capsys.readouterr().err

    assert cli.cmd_unblock(["ozon", "--wait", "-1"]) == 2
    assert "cannot be negative" in capsys.readouterr().err

    assert cli.cmd_unblock(["ozon", "--frobnicate"]) == 2
    assert "unknown unblock option" in capsys.readouterr().err


def test_start_browser_failure_exits_one(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_start(*, visible: bool = True):
        return {"ok": False, "started": False, "already_running": False, "reason": "Chrome not found"}

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)

    assert cli.cmd_unblock(["ozon"]) == 1
    assert "Chrome not found" in capsys.readouterr().err


def test_a_show_failure_after_opening_tabs_exits_one(monkeypatch, fake_browser, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_show(*, target_id=None):
        return {"ok": False, "reachable": True, "reason": "the window could not be moved"}

    monkeypatch.setattr(chrome_cdp, "show_browser_window", fake_show)

    assert cli.cmd_unblock(["ozon"]) == 1
    assert "could not be moved" in capsys.readouterr().err


def test_a_tab_that_cannot_be_opened_exits_one_before_showing_or_waiting(monkeypatch, fake_browser, capsys):
    from mcp_core.transport import chrome_cdp

    _channel, events = fake_browser

    async def fake_open(urls):
        return {"ok": False, "target_ids": [], "reason": "opened 0 of 1 tab(s), then failed (RuntimeError: refused)"}

    def no_wait(*_args):
        raise AssertionError("unblock waited although no tab was opened")

    monkeypatch.setattr(chrome_cdp, "open_tabs", fake_open)
    monkeypatch.setattr(cli.time, "sleep", no_wait)
    monkeypatch.setattr("builtins.input", no_wait)

    assert cli.cmd_unblock(["ozon"]) == 1
    assert "opened 0 of 1 tab(s)" in capsys.readouterr().err
    assert events["shown"] == []


def test_simple_namespace_is_not_mistaken_for_a_check_mapping():
    """Guards the fake above: checks may arrive as model objects, read via _attr."""
    assert cli._blocked_sources([("x", "inconclusive", "", {"s": SimpleNamespace(reason="blocked")})]) == ["x"]
