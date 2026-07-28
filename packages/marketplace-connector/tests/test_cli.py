"""Offline tests for the operator CLI.

Selfchecks are monkeypatched out — doctor's job is to aggregate and report, and
that logic is testable without touching a network.
"""

from __future__ import annotations

import json

from marketplace_connector import cli


def test_install_prints_a_config_block_for_every_source(capsys):
    rc = cli.cmd_install(["claude"])

    assert rc == 0
    out = capsys.readouterr().out
    assert '"wildberries"' in out
    assert '"taobao"' in out
    assert "marketplace-mcp" in out


def test_config_block_covers_all_servers():
    block, note = cli._config_block()

    assert len(block) == len(cli.SERVERS)
    for key, script, _ in cli.SERVERS:
        assert block[key]["args"][-1] == script
    # The block must name a real location, never a /path/to placeholder the
    # operator has to hand-edit — that edit is where setup mistakes come from.
    assert "/path/to/" not in json.dumps(block)
    assert "/path/to/" not in note


def test_doctor_reports_per_source_status(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        table = {
            "wildberries": ("wildberries", "success", "ok"),
            "ozon": ("ozon", "inconclusive", "blocked"),
        }
        return table.get(name, (name, "success", "ok"))

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    rc = cli.cmd_doctor(["wildberries", "ozon"])

    out = capsys.readouterr().out
    assert "wildberries" in out
    assert "ozon" in out
    # One source inconclusive: not an alarm (that is 1), not a clean bill of
    # health either. A cron job that treats 0 as "all good" must not see 0 here.
    assert rc == 2


def test_doctor_exits_nonzero_on_drift(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        return name, "drift_detected", "search:drift"

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    rc = cli.cmd_doctor(["wildberries"])

    assert rc == 1


def test_doctor_survives_a_selfcheck_that_raises(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        return name, "error", "RuntimeError: boom"

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    rc = cli.cmd_doctor(["avito"])

    out = capsys.readouterr().out
    assert "avito" in out
    assert rc == 2  # unjudgeable, not healthy


def test_main_routes_subcommands_and_rejects_unknown(capsys):
    assert cli.main(["install", "claude"]) == 0
    assert cli.main(["no-such-command"]) == 2
    assert cli.main(["--help"]) == 0


def test_doctor_writes_a_status_file(monkeypatch, capsys, tmp_path):
    async def fake_selfcheck(name, module_path, tool_name):
        return name, "success", "ok"

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)
    target = tmp_path / "status.json"

    rc = cli.cmd_doctor(["wildberries", "--status-file", str(target)])

    assert rc == 0
    import json as _json

    report = _json.loads(target.read_text())
    assert report["healthy"] == 1
    assert report["sources"]["wildberries"]["status"] == "success"
    assert "checked_at" in report


def test_install_rejects_an_unknown_client(capsys):
    """A typo must not silently print a Claude block for Cursor."""
    rc = cli.cmd_install(["cursour"])

    assert rc == 2
    assert "unknown client" in capsys.readouterr().err


def test_install_accepts_every_documented_client():
    for client in sorted(cli.KNOWN_CLIENTS):
        assert cli.cmd_install([client]) == 0


def test_config_block_falls_back_to_console_scripts_outside_a_checkout(monkeypatch):
    """Installed as a wheel there is no checkout, so uv run --directory is wrong."""
    monkeypatch.setattr(cli, "_workspace_root", lambda: None)
    monkeypatch.setattr(cli.shutil, "which", lambda script: f"/usr/local/bin/{script}")

    block, note = cli._config_block()

    assert block["wildberries"] == {"command": "/usr/local/bin/wb-mcp", "args": []}
    assert "console scripts" in note


def test_doctor_exit_code_prefers_drift_over_inconclusive(monkeypatch, capsys):
    """Drift is the alarm; a blocked source must not mask it."""

    async def fake_selfcheck(name, module_path, tool_name):
        if name == "wildberries":
            return name, "drift_detected", "search:drift"
        return name, "inconclusive", "blocked"

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    assert cli.cmd_doctor(["wildberries", "ozon"]) == 1


def test_doctor_returns_zero_only_when_everything_is_healthy(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        return name, "success", "ok"

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    assert cli.cmd_doctor(["wildberries", "ozon"]) == 0


# ------------------------------------------------------- actionable selfcheck detail ----


def test_doctor_shows_why_a_check_was_inconclusive():
    """ "inconclusive" alone cannot be acted on.

    Rate-limited, IP-banned and no-CDP all render as the same word, and the
    operator's next move differs for each: wait, change network, start Chrome.
    Connectors already classify this — doctor used to drop it on the floor.
    """
    rate_limited = {"state": "inconclusive", "reason": "rate_limited", "code": 429}
    banned = {"state": "inconclusive", "reason": "blocked", "code": 403}

    assert cli._check_detail(rate_limited) == "inconclusive (rate_limited http 429)"
    assert cli._check_detail(banned) == "inconclusive (blocked http 403)"


def test_a_healthy_check_stays_terse():
    assert cli._check_detail({"state": "healthy", "reason": None}) == "healthy"


def test_detail_survives_a_reason_without_a_code():
    assert cli._check_detail({"state": "drift", "reason": "parse_smoke_failed"}) == "drift (parse_smoke_failed)"


def test_detail_reads_model_objects_as_well_as_dicts():
    """Connectors return sub-checks as models in some packages, dicts in others."""

    class Entry:
        state = "inconclusive"
        reason = "transport_down"
        code = None

    assert cli._check_detail(Entry()) == "inconclusive (transport_down)"
