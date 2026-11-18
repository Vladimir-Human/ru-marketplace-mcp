"""Offline tests for late-2026 MCP clients, WB_TRANSPORT=dom unblock, and CLI edge cases."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from marketplace_connector import cli


@pytest.mark.req("R-CLI-INSTALL")
def test_install_write_vscode_uses_servers_root_and_stdio_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vscode_cfg = tmp_path / "Code" / "User" / "mcp.json"
    monkeypatch.setenv("MARKETPLACE_MCP_VSCODE_CONFIG", str(vscode_cfg))
    vscode_cfg.parent.mkdir(parents=True)
    vscode_cfg.write_text(
        json.dumps({"servers": {"existing": {"type": "stdio", "command": "other"}}, "inputs": []}),
        encoding="utf-8",
    )

    assert cli.cmd_install(["vscode", "--write"]) == 0
    doc = json.loads(vscode_cfg.read_text(encoding="utf-8"))
    assert "mcpServers" not in doc
    assert doc["inputs"] == []
    assert doc["servers"]["existing"]["command"] == "other"
    entry = doc["servers"]["marketplace"]
    assert entry["type"] == "stdio"
    assert entry["args"][-1] == "compare-mcp"
    capsys.readouterr()

    # Re-running full -> without --profile keeps existing profile in VS Code too.
    assert cli.cmd_install(["vscode", "--write", "--force", "--profile", "full"]) == 0
    assert cli.cmd_install(["vscode", "--write"]) == 0
    doc_after = json.loads(vscode_cfg.read_text(encoding="utf-8"))
    assert doc_after["servers"]["marketplace"]["args"][-1] == "marketplace-mcp"


@pytest.mark.req("R-CLI-INSTALL")
def test_install_write_windsurf_antigravity_gemini_and_codex_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for client_name in ("windsurf", "antigravity", "gemini"):
        path = cli._config_path(client_name)
        assert path.is_relative_to(tmp_path)
        if client_name == "gemini":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"theme": "Default"}), encoding="utf-8")
        assert cli.cmd_install([client_name, "--write"]) == 0
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["mcpServers"]["marketplace"]["args"][-1] == "compare-mcp"
        if client_name == "gemini":
            assert doc["theme"] == "Default"

    capsys.readouterr()
    assert cli.cmd_install(["codex", "--write"]) == 0
    codex_out = capsys.readouterr().out
    assert "codex mcp add marketplace --" in codex_out
    assert "compare-mcp" in codex_out


@pytest.mark.req("R-CLI-UNBLOCK", "R-DOCTOR-NEXT-STEP")
def test_wb_transport_dom_unblocks_wildberries_and_setup_filters_non_browser_walls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WB_TRANSPORT", "api")
    assert cli.cmd_unblock(["wildberries"]) == 2
    capsys.readouterr()

    monkeypatch.setenv("WB_TRANSPORT", "dom")
    assert cli.cmd_unblock(["wildberries", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "https://www.wildberries.ru/" in out

    # Under WB_TRANSPORT=api, even if a non-browser source reported a wall reason, setup filters it out.
    monkeypatch.setenv("WB_TRANSPORT", "api")
    claude_cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    claude_cfg.parent.mkdir(parents=True, exist_ok=True)

    def fake_selfchecks(_only: set[str] | None):
        wall = {"state": "inconclusive", "reason": "blocked", "code": 403}
        return [
            ("wildberries", "inconclusive", "card:blocked", {"card": wall}),
            ("ozon", "inconclusive", "search:blocked", {"search": wall}),
        ]

    unblock_calls: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_selfchecks", fake_selfchecks)
    monkeypatch.setattr(cli, "_probe_cdp", lambda: (True, "Chrome CDP: reachable."))
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.setattr(cli, "cmd_unblock", lambda names: unblock_calls.append(list(names)) or 0)

    assert cli.cmd_setup(["--client", "claude", "--no-browser"]) == 0
    assert unblock_calls == [["ozon"]]


@pytest.mark.req("R-CLI-UNBLOCK")
@pytest.mark.parametrize("bad_wait", ["nan", "inf", "-inf"])
def test_unblock_wait_rejects_nan_and_inf_and_keeps_negative_message(
    bad_wait: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.cmd_unblock(["ozon", "--wait", bad_wait]) == 2
    assert "--wait" in capsys.readouterr().err

    assert cli.cmd_unblock(["ozon", "--wait", "-1"]) == 2
    assert "cannot be negative" in capsys.readouterr().err


@pytest.mark.req("R-CLI-SETUP")
def test_setup_multi_client_with_claude_code_reminds_to_run_mcp_add(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    claude_cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    claude_cfg.parent.mkdir(parents=True, exist_ok=True)

    rc = cli.cmd_setup(["--client", "claude", "--client", "claude-code", "--no-browser", "--no-doctor"])
    assert rc == 0
    out = capfd.readouterr().out
    assert "claude mcp add" in out.splitlines()[-1]


@pytest.mark.req("R-DOCTOR-NEXT-STEP")
def test_next_step_budget_refused_precedence_with_cdp_and_transport_down() -> None:
    pure_budget = {"budget": {"state": "inconclusive", "reason": "budget_refused"}}
    step_pure = cli.next_step(
        "ozon", "inconclusive", pure_budget, cdp_reachable=False, install_mode=cli.INSTALL_CHECKOUT
    )
    assert step_pure is not None and "budget refused" in step_pure

    mixed = {
        "search": {"state": "inconclusive", "reason": "transport_down"},
        "budget": {"state": "inconclusive", "reason": "budget_refused"},
    }
    assert (
        cli.next_step("lamoda", "inconclusive", mixed, cdp_reachable=False, install_mode=cli.INSTALL_CHECKOUT)
        == cli.NEXT_STEP_CDP_DOWN
    )
    step_up = cli.next_step("lamoda", "inconclusive", mixed, cdp_reachable=True, install_mode=cli.INSTALL_CHECKOUT)
    assert step_up is not None and "uv run marketplace-mcp unblock lamoda" in step_up
