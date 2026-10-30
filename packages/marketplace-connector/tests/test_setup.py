"""Offline tests for ``marketplace-mcp setup``.

Every step is faked at its seam: detection is a temporary config folder or
``shutil.which``, the browser is ``chrome_cdp.start_browser``, health is
``_run_selfchecks`` and unblock is ``cmd_unblock``. No config outside the
temporary paths, no Chrome, no marketplace request.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from marketplace_connector import cli


@pytest.fixture(autouse=True)
def offline_cdp_probe(monkeypatch):
    """Even a real doctor-style table must not depend on a local Chrome."""
    from mcp_core.transport import chrome_cdp

    async def fake_probe():
        return {"reachable": False, "reason": "offline test"}

    monkeypatch.setattr(chrome_cdp, "probe_session", fake_probe)


@pytest.fixture
def claude_config(tmp_path, monkeypatch):
    folder = tmp_path / "Claude"
    path = folder / "claude_desktop_config.json"
    monkeypatch.setenv("MARKETPLACE_MCP_CLAUDE_CONFIG", str(path))
    return path


@pytest.fixture
def cursor_config(tmp_path, monkeypatch):
    folder = tmp_path / ".cursor"
    path = folder / "mcp.json"
    monkeypatch.setenv("MARKETPLACE_MCP_CURSOR_CONFIG", str(path))
    return path


@pytest.fixture
def no_claude_code(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda script: None)


@pytest.fixture
def fake_browser(monkeypatch):
    from mcp_core.transport import chrome_cdp

    calls: list[bool] = []

    async def fake_start(*, visible: bool = True) -> dict:
        calls.append(visible)
        return {"ok": True, "already_running": True, "host": "127.0.0.1", "port": 9222}

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)
    return calls


@pytest.fixture
def healthy_selfchecks(monkeypatch):
    calls: list[set[str] | None] = []

    def fake(only: set[str] | None):
        calls.append(only)
        return [("wildberries", "success", "search:healthy", {"search": {"state": "healthy"}})]

    monkeypatch.setattr(cli, "_run_selfchecks", fake)
    return calls


@pytest.fixture
def blocked_selfchecks(monkeypatch):
    def fake(only: set[str] | None):
        check = {"state": "inconclusive", "reason": "blocked", "code": 403}
        return [("ozon", "inconclusive", "search:inconclusive (blocked http 403)", {"search": check})]

    monkeypatch.setattr(cli, "_run_selfchecks", fake)


def test_setup_detects_a_claude_desktop_folder_and_writes_the_compare_profile(claude_config, no_claude_code, capfd):
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--no-browser", "--no-doctor"])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["marketplace"]["args"][-1] == "compare-mcp"
    out = capfd.readouterr().out
    assert "detected: Claude Desktop" in out
    assert "profile compare" in out
    assert "Restart Claude Desktop, then ask:" in out


def test_setup_detects_cursor_by_its_folder(cursor_config, no_claude_code, capfd):
    cursor_config.parent.mkdir()

    rc = cli.cmd_setup(["--no-browser", "--no-doctor"])

    assert rc == 0
    document = json.loads(cursor_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["marketplace"]["args"][-1] == "compare-mcp"
    assert "detected: Cursor" in capfd.readouterr().out


def test_setup_detects_claude_code_on_path_and_prints_the_add_line(claude_config, monkeypatch, capfd):
    """R-CLI-SETUP: run the printed add line before the restart/ask (audit S1-F5)."""
    monkeypatch.setattr(cli.shutil, "which", lambda script: "/usr/bin/claude" if script == "claude" else None)

    rc = cli.cmd_setup(["--no-browser", "--no-doctor"])

    assert rc == 0
    out = capfd.readouterr().out
    assert "detected: Claude Code" in out
    assert "claude mcp add marketplace" in out
    assert "compare-mcp" in out
    assert "run this line once (setup does not run it):" in out
    assert out.splitlines()[-1] == (
        "Run the claude mcp add line above, then restart Claude Code and ask: "
        "«Сравни цены на iPhone 15 128 ГБ на Wildberries, Ozon и Яндекс Маркете»"
    )
    assert not claude_config.exists()


def test_setup_client_override_ignores_detection(cursor_config, no_claude_code, capfd):
    rc = cli.cmd_setup(["--client", "cursor", "--no-browser", "--no-doctor"])

    assert rc == 0
    document = json.loads(cursor_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["marketplace"]["args"][-1] == "compare-mcp"
    assert "chosen with --client: Cursor" in capfd.readouterr().out


def test_setup_writes_with_a_backup_and_keeps_other_servers(claude_config, no_claude_code):
    claude_config.parent.mkdir()
    old = json.dumps({"mcpServers": {"other": {"command": "other-mcp", "args": []}}})
    claude_config.write_text(old, encoding="utf-8")

    rc = cli.cmd_setup(["--client", "claude", "--no-browser", "--no-doctor"])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["other"] == {"command": "other-mcp", "args": []}
    assert document["mcpServers"]["marketplace"]["args"][-1] == "compare-mcp"
    backups = list(claude_config.parent.glob("claude_desktop_config.json.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == old


def test_setup_explicit_profile_conflict_is_skipped_and_points_at_install_force(claude_config, no_claude_code, capfd):
    claude_config.parent.mkdir()
    full_entry, _ = cli._unified_entry("marketplace-mcp")
    claude_config.write_text(json.dumps({"mcpServers": {"marketplace": full_entry}}), encoding="utf-8")
    before = claude_config.read_bytes()

    rc = cli.cmd_setup(["--client", "claude", "--profile", "compare", "--no-browser", "--no-doctor"])

    assert rc == 1
    captured = capfd.readouterr()
    assert "profile full (marketplace-mcp)" in captured.err
    assert "asked for profile compare (compare-mcp)" in captured.err
    assert "marketplace-mcp install claude --write --force --profile compare" in captured.err
    assert "config skipped" in captured.err
    assert claude_config.read_bytes() == before
    assert list(claude_config.parent.glob("*.bak-*")) == []


def test_setup_succeeds_when_one_of_two_configs_was_written(claude_config, cursor_config, no_claude_code):
    claude_config.parent.mkdir()
    full_entry, _ = cli._unified_entry("marketplace-mcp")
    claude_config.write_text(json.dumps({"mcpServers": {"marketplace": full_entry}}), encoding="utf-8")

    rc = cli.cmd_setup(["--client", "claude", "--client", "cursor", "--no-browser", "--no-doctor"])

    assert rc == 0
    assert json.loads(cursor_config.read_text(encoding="utf-8"))["mcpServers"]["marketplace"]


def test_setup_no_browser_no_doctor_does_not_call_them(claude_config, no_claude_code, monkeypatch, capfd):
    from mcp_core.transport import chrome_cdp

    def unexpected(*args, **kwargs):
        pytest.fail("the skipped step must not run")

    monkeypatch.setattr(chrome_cdp, "start_browser", unexpected)
    monkeypatch.setattr(cli, "_run_selfchecks", unexpected)
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--client", "claude", "--no-browser", "--no-doctor"])

    assert rc == 0
    out = capfd.readouterr().out
    assert "Browser: skipped (--no-browser)" in out
    assert "Health: skipped (--no-doctor)" in out


def test_setup_dry_run_writes_nothing_and_calls_nothing(claude_config, no_claude_code, monkeypatch, capfd):
    from mcp_core.transport import chrome_cdp

    def unexpected(*args, **kwargs):
        pytest.fail("--dry-run must not start or request anything")

    monkeypatch.setattr(chrome_cdp, "start_browser", unexpected)
    monkeypatch.setattr(cli, "_run_selfchecks", unexpected)
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--client", "claude", "--dry-run"])

    assert rc == 0
    assert not claude_config.exists()
    out = capfd.readouterr().out
    assert "would start the scraping Chrome" in out
    assert "would run live selfchecks" in out
    assert str(claude_config) in out


def test_setup_non_tty_prints_the_unblock_command(
    claude_config, no_claude_code, fake_browser, blocked_selfchecks, capfd
):
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--client", "claude", "--no-browser"])

    assert rc == 0
    out = capfd.readouterr().out
    assert "marketplace-mcp unblock ozon" in out
    assert "open the unblock pages" not in out


def test_setup_tty_yes_runs_unblock(claude_config, no_claude_code, fake_browser, blocked_selfchecks, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "cmd_unblock", lambda names: calls.append(list(names)) or 0)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--client", "claude", "--no-browser"])

    assert rc == 0
    assert calls == [["ozon"]]


def test_setup_tty_no_skips_the_unblock_pages(
    claude_config, no_claude_code, fake_browser, blocked_selfchecks, monkeypatch, capfd
):
    """Answering "no" must not open the pages and must name the command (audit S4-F20)."""
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "cmd_unblock", lambda names: calls.append(list(names)) or 0)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--client", "claude", "--no-browser"])

    assert rc == 0
    out = capfd.readouterr().out
    assert "skipped; run: marketplace-mcp unblock ozon" in out
    assert calls == []


def test_setup_ctrl_c_at_the_prompt_cancels_before_unblock(
    claude_config, no_claude_code, blocked_selfchecks, monkeypatch, capfd
):
    """R-CLI-SETUP: Ctrl-C cancels the unblock offer with exit 130 (audit S1-F6)."""
    calls: list[list[str]] = []
    prompts: list[str] = []

    def interrupted(prompt):
        prompts.append(prompt)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_unblock", lambda names: calls.append(list(names)) or 0)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", interrupted)
    claude_config.parent.mkdir()

    try:
        rc = cli.cmd_setup(["--client", "claude", "--no-browser"])
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C escaped the setup prompt", pytrace=False)

    assert rc == 130
    assert prompts == ["   open the unblock pages for ozon now? [y/N] "]
    assert calls == []
    captured = capfd.readouterr()
    assert "cancelled" in captured.err.splitlines()
    assert "Restart Claude Desktop" not in captured.out


def test_setup_browser_failure_prints_the_reason_and_continues(claude_config, no_claude_code, monkeypatch, capfd):
    from mcp_core.transport import chrome_cdp

    async def failing_start(*, visible: bool = True) -> dict:
        return {"ok": False, "reason": "no Chrome on this machine"}

    monkeypatch.setattr(chrome_cdp, "start_browser", failing_start)
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--client", "claude", "--no-doctor"])

    assert rc == 0
    err = capfd.readouterr().err
    assert "no Chrome on this machine" in err
    assert json.loads(claude_config.read_text(encoding="utf-8"))["mcpServers"]["marketplace"]


def test_setup_no_client_prints_manual_install_commands_and_exits_one(
    claude_config, cursor_config, no_claude_code, capfd
):
    rc = cli.cmd_setup(["--no-browser", "--no-doctor"])

    assert rc == 1
    out = capfd.readouterr().out
    assert "no supported client detected" in out
    for client in cli.SETUP_CLIENTS:
        assert f"marketplace-mcp install {client} --write" in out


def test_setup_full_profile_writes_the_unified_script(claude_config, no_claude_code):
    claude_config.parent.mkdir()

    rc = cli.cmd_setup(["--client", "claude", "--profile", "full", "--no-browser", "--no-doctor"])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["marketplace"]["args"][-1] == "marketplace-mcp"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--profile", "tiny"], "unknown profile"),
        (["--profile"], "--profile requires"),
        (["--client", "dsh"], "unknown client"),
        (["--client"], "--client requires"),
        (["--bogus"], "unknown setup option"),
        (["extra"], "no positional arguments"),
    ],
)
def test_setup_bad_arguments_exit_two(argv, message, capfd):
    assert cli.cmd_setup(argv) == 2

    captured = capfd.readouterr()
    assert message in captured.err
    assert not captured.out


def test_main_routes_the_setup_verb(monkeypatch):
    received: list[list[str]] = []

    def fake_setup(arguments: list[str]) -> int:
        received.append(arguments)
        return 17

    monkeypatch.setattr(cli, "cmd_setup", fake_setup)

    assert cli.main(["setup", "--no-browser"]) == 17
    assert received == [["--no-browser"]]


def test_setup_keeps_the_profile_an_existing_entry_runs(claude_config, no_claude_code, capfd):
    """Compare is the default for new installs only: an operator's full profile stays."""
    claude_config.parent.mkdir()
    full_entry, _ = cli._unified_entry("marketplace-mcp")
    claude_config.write_text(json.dumps({"mcpServers": {"marketplace": full_entry}}), encoding="utf-8")
    before = claude_config.read_bytes()

    rc = cli.cmd_setup(["--client", "claude", "--no-browser", "--no-doctor"])

    assert rc == 0
    out = capfd.readouterr().out
    assert "profile full (already configured, kept)" in out
    assert "nothing to write" in out
    assert claude_config.read_bytes() == before
    assert list(claude_config.parent.glob("*.bak-*")) == []


def test_setup_dry_run_does_not_echo_other_servers_settings(claude_config, no_claude_code, capfd):
    claude_config.parent.mkdir()
    other = {"command": "other-mcp", "env": {"OTHER_API_KEY": "secret-value-123"}}
    claude_config.write_text(json.dumps({"mcpServers": {"other": other}}), encoding="utf-8")

    rc = cli.cmd_setup(["--client", "claude", "--dry-run"])

    assert rc == 0
    out = capfd.readouterr().out
    assert "secret-value-123" not in out
    assert "compare-mcp" in out
    assert str(claude_config) in out


def test_the_conftest_guard_keeps_every_client_path_inside_tmp_path(tmp_path):
    """The autouse guard is what keeps detection away from the developer's own configs."""
    assert cli._config_path("claude").is_relative_to(tmp_path)
    assert cli._config_path("cursor").is_relative_to(tmp_path)
    assert cli._detected_clients() == []
