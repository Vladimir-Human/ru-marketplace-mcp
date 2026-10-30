"""Offline tests for the operator CLI.

Selfchecks are monkeypatched out — doctor's job is to aggregate and report, and
that logic is testable without touching a network.
"""

from __future__ import annotations

import ast
import datetime
import json
import os
import pathlib
import re
import sys
from types import SimpleNamespace

import pytest
from marketplace_connector import cli


@pytest.fixture(autouse=True)
def offline_cdp_probe(monkeypatch):
    """Even successful doctor runs must not depend on a local Chrome session."""
    from mcp_core.transport import chrome_cdp

    async def fake_probe():
        return {"reachable": False, "reason": "offline test"}

    monkeypatch.setattr(chrome_cdp, "probe_session", fake_probe)


def test_install_defaults_to_the_compare_profile(capsys):
    """One entry, one cheap server: a client pays for the tool list every request."""
    rc = cli.cmd_install(["claude"])

    assert rc == 0
    out = capsys.readouterr().out
    assert '"marketplace"' in out
    assert '"wildberries"' not in out
    assert '"compare-mcp"' in out
    assert "Profile: compare" in out
    assert "--profile full for every per-source tool" in out
    # The optional paid source still needs its token, whoever wires it in.
    assert "optional paid analytics" in out
    assert "MPSTATS_MP_AUTH" in out


def test_install_per_source_keeps_the_old_block(capsys):
    rc = cli.cmd_install(["claude", "--per-source"])

    assert rc == 0
    out = capsys.readouterr().out
    assert '"wildberries"' in out
    assert '"taobao"' in out
    assert '"aliexpress"' in out
    assert '"mpstats"' in out
    assert "optional paid analytics" in out
    assert "MPSTATS_MP_AUTH" in out
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


def test_dsh_install_emits_a_cordis_patch_instead_of_mcp_servers(capsys):
    """dsh does not read claude_desktop_config.json; print its patch format."""
    rc = cli.cmd_install(["dsh"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "- insert:" in out
    assert "@deepseek-ai/dsh-mcp-client" in out
    assert "serverName: rumarket" in out
    assert "compare-mcp" in out
    assert "decision-mcp" in out
    assert "marketplace-mcp" in out
    assert "mcpServers" not in out


def test_dsh_install_rows_are_disabled_until_the_env_gate_is_set(capsys):
    rc = cli.cmd_install(["dsh"])

    assert rc == 0
    out = capsys.readouterr().out
    assert cli.DSH_ENV_DIR in out
    assert cli.DSH_ENV_FULL in out
    assert 'disabled: !!js "!process.env.RU_MARKETPLACE_MCP_DIR' in out
    assert out.count("disabled: !!js") == 3


def test_dsh_patch_block_falls_back_to_console_scripts_outside_a_checkout(monkeypatch):
    monkeypatch.setattr(cli, "_workspace_root", lambda: None)
    monkeypatch.setattr(cli.shutil, "which", lambda script: f"/usr/local/bin/{script}")

    block, note = cli._dsh_patch_block()

    assert 'command: "/usr/local/bin/compare-mcp"' in block
    assert 'command: "/usr/local/bin/marketplace-mcp"' in block
    assert "enable switch" in note
    assert "console script compare-mcp" in note
    assert "console script marketplace-mcp" in note


def test_generated_dsh_modes_match_the_committed_patch():
    root = pathlib.Path(__file__).resolve().parents[3]
    committed = (root / "dsh" / "cordis.patch.yml").read_text(encoding="utf-8")
    generated, _ = cli._dsh_patch_block()

    # Mode precedence and disabled-row directory fallback must survive copying
    # the install output instead of installing the bundled DSH patch.
    for pattern in (r'disabled: !!js "([^"]+)"', r'- !!js "(process.env.[^"]+)"'):
        assert re.findall(pattern, generated) == re.findall(pattern, committed)
    assert re.findall(r"          - ([\w-]+-mcp)\n", generated + "\n") == [
        "compare-mcp",
        "decision-mcp",
        "marketplace-mcp",
    ]


def test_doctor_reports_per_source_status(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        table = {
            "wildberries": ("wildberries", "success", "ok", {}),
            "ozon": ("ozon", "inconclusive", "blocked", {}),
        }
        return table.get(name, (name, "success", "ok", {}))

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
        return name, "drift_detected", "search:drift", {}

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    rc = cli.cmd_doctor(["wildberries"])

    assert rc == 1


def test_doctor_survives_a_selfcheck_that_raises(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        return name, "error", "RuntimeError: boom", {}

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    rc = cli.cmd_doctor(["avito"])

    out = capsys.readouterr().out
    assert "avito" in out
    # A crashed selfcheck is not an anti-bot wall: the advice says so.
    assert cli.NEXT_STEP_SELFCHECK_ERROR in out
    assert rc == 2  # unjudgeable, not healthy


def test_main_routes_subcommands_and_rejects_unknown(capsys):
    assert cli.main(["install", "claude"]) == 0
    assert cli.main(["no-such-command"]) == 2
    assert cli.main(["--help"]) == 0


def test_doctor_writes_a_status_file(monkeypatch, capsys, tmp_path):
    async def fake_selfcheck(name, module_path, tool_name):
        return name, "success", "ok", {}

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


@pytest.mark.parametrize("argv", [["claude", "--bogus"], ["dsh", "cursor"], ["--bogus"]])
def test_install_rejects_extra_arguments_and_unknown_flags(argv, capsys):
    assert cli.cmd_install(argv) == 2
    captured = capsys.readouterr()
    assert captured.err
    assert not captured.out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["ozno"], "unknown source"),
        (["wildberries", "ozno"], "unknown source"),
        ([" "], "unknown source"),
        (["--bogus"], "unknown doctor option"),
        (["wildberries", "--bogus"], "unknown doctor option"),
        (["--status-file"], "requires a path"),
        (["--status-file", ""], "requires a path"),
        (["--status-file", "--bogus"], "requires a path"),
        (["--status-file", "status.json", "--status-file", "other.json"], "only be supplied once"),
    ],
)
def test_invalid_doctor_arguments_fail_before_any_checks(argv, message, monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    def unexpected_call(*args):
        pytest.fail("invalid arguments must not perform network checks")

    monkeypatch.setattr(cli, "_run_one_selfcheck", unexpected_call)
    monkeypatch.setattr(chrome_cdp, "probe_session", unexpected_call)

    assert cli.cmd_doctor(argv) == 2
    captured = capsys.readouterr()
    assert message in captured.err
    assert not captured.out


def test_doctor_accepts_install_source_names_and_includes_aliexpress(monkeypatch):
    checked = []

    async def fake_selfcheck(name, module_path, tool_name):
        checked.append((name, module_path, tool_name))
        return name, "success", "ok", {}

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    assert cli.cmd_doctor(["WB", "yandex-market", "ym", "detsky-mir", "detmir", "ali", "aliexpress"]) == 0
    assert [name for name, _, _ in checked] == ["wildberries", "yandex_market", "detsky_mir", "aliexpress"]
    assert checked[-1] == ("aliexpress", "aliexpress_connector.server", "aliexpress_selfcheck")


@pytest.mark.parametrize("as_dict", [True, False])
async def test_run_one_selfcheck_reads_dict_and_model_responses(as_dict, monkeypatch):
    payload = {
        "status": "inconclusive",
        "checks": {"search": {"state": "inconclusive", "reason": "blocked", "code": 403}},
    }

    async def selfcheck():
        return payload if as_dict else SimpleNamespace(**payload)

    monkeypatch.setitem(sys.modules, "cli_test_connector", SimpleNamespace(selfcheck=selfcheck))

    name, status, detail, checks = await cli._run_one_selfcheck("test", "cli_test_connector", "selfcheck")

    assert (name, status, detail) == (
        "test",
        "inconclusive",
        "search:inconclusive (blocked http 403)",
    )
    # The per-check reasons travel with the result: the next step is chosen
    # from them, and a rendered string cannot be told apart from a code.
    assert checks["search"]["reason"] == "blocked"


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
            return name, "drift_detected", "search:drift", {}
        return name, "inconclusive", "blocked", {}

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    assert cli.cmd_doctor(["wildberries", "ozon"]) == 1


def test_doctor_returns_zero_only_when_everything_is_healthy(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        return name, "success", "ok", {}

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
    assert cli._check_detail({"state": "healthy", "detail": "42 valid products", "notes": ["ok"]}) == "healthy"


def test_doctor_preserves_yandex_empty_shell_diagnosis():
    from yandex_connector.models_output import YandexSelfcheckEntry

    check = YandexSelfcheckEntry(
        state="inconclusive",
        detail='{"error": "transport_down", "message": "yandex_card: empty_product_shell',
        notes=["product frame arrived hollow: degraded serving or a delisted product"],
    )
    assert "empty_product_shell" in cli._check_detail(check)
    assert cli._check_detail(check).startswith("inconclusive (")


def test_doctor_uses_notes_if_no_reason_or_detail():
    assert cli._check_detail({"state": "drift", "notes": ["price field vanished", "inspect the parser"]}) == (
        "drift (price field vanished; inspect the parser)"
    )


def test_doctor_keeps_diagnostics_on_one_bounded_line():
    detail = "empty_product_shell\n" + "unavailable " * 100
    rendered = cli._check_detail({"state": "inconclusive", "detail": detail})
    assert "\n" not in rendered
    assert len(rendered) <= len("inconclusive ()") + 240
    assert rendered.endswith("...)")


def test_detail_survives_a_reason_without_a_code():
    assert cli._check_detail({"state": "drift", "reason": "parse_smoke_failed"}) == "drift (parse_smoke_failed)"


def test_detail_reads_model_objects_as_well_as_dicts():
    """Connectors return sub-checks as models in some packages, dicts in others."""

    class Entry:
        state = "inconclusive"
        reason = "transport_down"
        code = None

    assert cli._check_detail(Entry()) == "inconclusive (transport_down)"


# --------------------------------------------------------------- install --write ----


@pytest.fixture
def claude_config(tmp_path, monkeypatch):
    """Point the Claude Desktop config at a temp file."""
    path = tmp_path / "claude_desktop_config.json"
    monkeypatch.setenv("MARKETPLACE_MCP_CLAUDE_CONFIG", str(path))
    return path


def test_install_write_creates_a_new_config_file(claude_config):
    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8"))
    entry = document["mcpServers"]["marketplace"]
    assert entry["command"]
    assert entry["args"][-1] == "compare-mcp"


def test_install_write_keeps_other_servers_and_settings(claude_config):
    claude_config.write_text(
        json.dumps(
            {
                "mcpServers": {"other": {"command": "other-mcp", "args": []}},
                "someSetting": {"keep": True},
            }
        ),
        encoding="utf-8",
    )

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["other"] == {"command": "other-mcp", "args": []}
    assert document["mcpServers"]["marketplace"]
    assert document["someSetting"] == {"keep": True}


def test_install_write_is_idempotent(claude_config, capsys):
    assert cli.cmd_install(["claude", "--write"]) == 0
    first = claude_config.read_bytes()
    capsys.readouterr()

    assert cli.cmd_install(["claude", "--write"]) == 0

    out = capsys.readouterr().out
    assert "already configured" in out
    assert claude_config.read_bytes() == first
    assert list(claude_config.parent.glob("*.bak-*")) == []


def test_install_write_leaves_a_backup_of_the_old_file(claude_config):
    old = json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}})
    claude_config.write_text(old, encoding="utf-8")

    assert cli.cmd_install(["claude", "--write"]) == 0

    backups = list(claude_config.parent.glob("claude_desktop_config.json.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == old


@pytest.mark.parametrize(
    ("broken", "message"),
    [
        (b"{ this is not json", "is not valid JSON"),
        (b"[]", "is not a JSON object"),
        (b'["marketplace"]', "is not a JSON object"),
        (b'{"mcpServers": []}', "non-object 'mcpServers'"),
        (b'{"mcpServers": "marketplace"}', "non-object 'mcpServers'"),
        (b'{"mcpServers": {"marketplace": {"command": NaN}}}', "is not valid JSON"),
    ],
)
def test_install_write_refuses_malformed_json(claude_config, broken, message, capsys):
    """A config the tool cannot understand is never rewritten (audit S4-F19).

    Invalid JSON is only one way to write a file the tool cannot merge into:
    a root that is not an object, an ``mcpServers`` that is not an object, or
    a ``NaN`` (which is not JSON at all) each have their own guard.
    """
    claude_config.write_bytes(broken)

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 2
    assert message in capsys.readouterr().err
    assert claude_config.read_bytes() == broken
    assert list(claude_config.parent.glob("*.bak-*")) == []


def test_install_write_reads_a_config_saved_with_a_utf8_bom(claude_config):
    """A BOM is valid JSON for the operator; some editors add one (audit S1-F3)."""
    claude_config.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}).encode("utf-8")
    )

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8-sig"))
    assert document["mcpServers"]["other"] == {"command": "x", "args": []}
    assert document["mcpServers"]["marketplace"]
    # The write stays plain UTF-8: a BOM in the client's own file breaks it.
    assert not claude_config.read_bytes().startswith(b"\xef\xbb\xbf")


def test_install_write_refuses_a_config_that_is_not_utf8(claude_config, capsys):
    """UTF-16 is an encoding problem, not a JSON problem (audit S1-F3).

    The old message ("not valid JSON") sends the operator to delete a config
    that is perfectly valid JSON in another encoding.
    """
    original = json.dumps({"mcpServers": {}}).encode("utf-16")
    claude_config.write_bytes(original)

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "is not UTF-8 text; save it as UTF-8 and re-run" in err
    assert "not valid JSON" not in err
    assert claude_config.read_bytes() == original
    assert list(claude_config.parent.glob("*.bak-*")) == []


def test_install_write_never_overwrites_an_earlier_backup(claude_config, monkeypatch):
    """Two writes in the same second must not land on one backup name (audit S1-F4).

    The stamp has one-second resolution, so the second write used to overwrite
    the only copy of the original config.
    """
    frozen = SimpleNamespace(
        datetime=SimpleNamespace(now=lambda tz=None: datetime.datetime(2026, 10, 30, 12, 0, 0)),
        UTC=datetime.UTC,
    )
    monkeypatch.setattr(cli, "datetime", frozen)
    original = json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}})
    claude_config.write_text(original, encoding="utf-8")

    assert cli.cmd_install(["claude", "--write"]) == 0
    assert cli.cmd_install(["claude", "--write", "--force", "--profile", "full"]) == 0

    backups = sorted(claude_config.parent.glob("claude_desktop_config.json.bak-*"))
    assert len(backups) == 2
    # The first backup still holds the original file.
    assert backups[0].read_text(encoding="utf-8") == original


def test_install_write_keeps_a_symlinked_config_a_symlink(claude_config, tmp_path):
    """A symlinked config must stay a symlink (audit S1-F2).

    Replacing the link with a regular file detaches the client from the
    operator's real file (a dotfiles repo, a synced folder): the next sync
    silently restores the old config and the server disappears.
    """
    target = tmp_path / "dotfiles" / "claude_desktop_config.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}), encoding="utf-8")
    try:
        os.symlink(target, claude_config)
    except OSError as exc:
        pytest.skip(f"this machine cannot create symlinks ({exc}); Windows needs developer mode or admin rights")

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 0
    assert claude_config.is_symlink()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["mcpServers"]["marketplace"]
    assert document["mcpServers"]["other"] == {"command": "x", "args": []}
    # The backup sits next to the real file, not next to the link.
    assert len(list(target.parent.glob("*.bak-*"))) == 1
    assert list(claude_config.parent.glob("*.bak-*")) == []


def test_install_write_writes_through_a_symlinked_config(claude_config, tmp_path, monkeypatch, capsys):
    """The same case without a real symlink (audit S1-F2).

    Windows without developer mode cannot create one, so the link is
    simulated at the filesystem API: the write must land in the file the
    link points at and leave the link alone.
    """
    target = tmp_path / "dotfiles" / "claude_desktop_config.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    original = target.read_bytes()
    claude_config.write_bytes(original)
    real_is_symlink = pathlib.Path.is_symlink
    real_resolve = pathlib.Path.resolve

    def fake_is_symlink(self):
        return self == claude_config or real_is_symlink(self)

    def fake_resolve(self, *args, **kwargs):
        if self == claude_config:
            return target
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(pathlib.Path, "resolve", fake_resolve)

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 0
    # The merged config landed in the file the link points at.
    assert json.loads(target.read_text(encoding="utf-8"))["mcpServers"]["marketplace"]
    # The link itself was not replaced by a regular file.
    assert json.loads(claude_config.read_text(encoding="utf-8"))["mcpServers"] == {}
    assert claude_config.read_bytes() == original
    backups = list(target.parent.glob("*.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert list(claude_config.parent.glob("*.bak-*")) == []
    out = capsys.readouterr().out
    assert f"wrote {target}" in out
    assert f"backup: {backups[0]}" in out


def test_install_write_refuses_a_conflicting_entry(claude_config):
    claude_config.write_text(
        json.dumps({"mcpServers": {"marketplace": {"command": "something-else", "args": []}}}),
        encoding="utf-8",
    )
    before = claude_config.read_bytes()

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 2
    assert claude_config.read_bytes() == before


def test_install_write_changes_nothing_when_the_backup_fails(claude_config, monkeypatch, capsys):
    """A change is only made after the old file is safely copied."""
    claude_config.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}), encoding="utf-8")
    before = claude_config.read_bytes()

    def refuse(src, dst):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr(cli.shutil, "copy2", refuse)

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 3
    assert claude_config.read_bytes() == before
    assert "nothing was changed" in capsys.readouterr().err
    assert list(claude_config.parent.glob("*.bak-*")) == []


def test_install_write_force_replaces_a_conflicting_entry(claude_config):
    claude_config.write_text(
        json.dumps({"mcpServers": {"marketplace": {"command": "something-else", "args": []}}}),
        encoding="utf-8",
    )

    rc = cli.cmd_install(["claude", "--write", "--force"])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["marketplace"]["command"] != "something-else"
    assert list(claude_config.parent.glob("*.bak-*"))


def test_install_dry_run_writes_nothing(claude_config, capsys):
    rc = cli.cmd_install(["claude", "--write", "--dry-run"])

    assert rc == 0
    assert not claude_config.exists()
    out = capsys.readouterr().out
    assert "marketplace" in out
    assert str(claude_config) in out


def test_install_dry_run_names_but_never_prints_the_rest_of_the_file(claude_config, capsys):
    """Other servers' env blocks may hold keys: a dry run names what it keeps, never its content."""
    claude_config.write_text(
        json.dumps(
            {
                "mcpServers": {"other": {"command": "x", "args": [], "env": {"API_KEY": "FAKE-SECRET-1"}}},
                "theme": {"accent": "FAKE-SETTING-2"},
            }
        ),
        encoding="utf-8",
    )
    before = claude_config.read_bytes()

    rc = cli.cmd_install(["claude", "--write", "--dry-run"])

    assert rc == 0
    assert claude_config.read_bytes() == before
    out = capsys.readouterr().out
    assert '"marketplace"' in out
    assert "FAKE-SECRET-1" not in out
    assert "FAKE-SETTING-2" not in out
    assert "kept unchanged: other" in out
    assert "other settings kept: theme" in out
    assert "replaces:" not in out
    assert str(claude_config) in out


def test_install_dry_run_with_force_names_the_entry_it_replaces(claude_config, capsys):
    claude_config.write_text(
        json.dumps({"mcpServers": {"marketplace": {"command": "old", "env": {"K": "FAKE-OLD-3"}}}}),
        encoding="utf-8",
    )

    rc = cli.cmd_install(["claude", "--write", "--dry-run", "--force"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "replaces: marketplace" in out
    assert "FAKE-OLD-3" not in out


def test_install_claude_code_write_prints_a_claude_mcp_add_line(capsys):
    rc = cli.cmd_install(["claude-code", "--write"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "claude mcp add marketplace" in out
    assert "compare-mcp" in out


@pytest.mark.parametrize("options", [[], ["--profile", "decision"], ["--profile", "full"], ["--per-source"]])
def test_install_claude_code_paste_block_names_the_project_config(options, capsys):
    """R-CLI-INSTALL: user add lines precede the project-only JSON (audit S1-F1)."""
    assert cli.cmd_install(["claude-code", "--write", *options]) == 0
    write_lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("claude mcp add ")]
    assert write_lines

    assert cli.cmd_install(["claude-code", *options]) == 0

    out = capsys.readouterr().out
    header = "# Or add this to a project's .mcp.json (mcpServers):"
    before, separator, after = out.partition(header)
    assert separator == header
    assert [line for line in before.splitlines() if line.startswith("claude mcp add ")] == write_lines
    assert "# Add to your mcpServers block (claude-code)." not in out
    entries, _ = json.JSONDecoder().raw_decode(after.lstrip())
    assert set(entries) == {line.split()[3] for line in write_lines}
    assert all(entry["command"] for entry in entries.values())


def test_install_claude_code_full_profile_prints_the_unified_script(capsys):
    rc = cli.cmd_install(["claude-code", "--write", "--profile", "full"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "claude mcp add marketplace" in out
    assert "marketplace-mcp" in out


@pytest.mark.parametrize(
    ("profile", "script"), [("compare", "compare-mcp"), ("decision", "decision-mcp"), ("full", "marketplace-mcp")]
)
def test_install_profile_selects_the_entry_script(profile, script, claude_config):
    rc = cli.cmd_install(["claude", "--write", "--profile", profile])

    assert rc == 0
    document = json.loads(claude_config.read_text(encoding="utf-8"))
    assert document["mcpServers"]["marketplace"]["args"][-1] == script


def test_install_unknown_profile_exits_two(capsys):
    assert cli.cmd_install(["claude", "--profile", "tiny"]) == 2

    err = capsys.readouterr().err
    assert "unknown profile 'tiny'" in err
    assert "compare, decision, full" in err


def test_install_profile_requires_a_value(capsys):
    assert cli.cmd_install(["claude", "--profile"]) == 2
    assert "--profile requires" in capsys.readouterr().err


def test_install_refuses_per_source_with_a_profile(capsys):
    assert cli.cmd_install(["claude", "--per-source", "--profile", "full"]) == 2
    assert "--per-source" in capsys.readouterr().err


def test_install_conflict_names_the_configured_and_asked_profiles(claude_config, capsys):
    full_entry, _ = cli._unified_entry("marketplace-mcp")
    claude_config.write_text(json.dumps({"mcpServers": {"marketplace": full_entry}}), encoding="utf-8")
    before = claude_config.read_bytes()

    rc = cli.cmd_install(["claude", "--write", "--profile", "compare"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "profile full (marketplace-mcp)" in err
    assert "asked for profile compare (compare-mcp)" in err
    assert claude_config.read_bytes() == before


def test_unified_entry_takes_the_profile_script(monkeypatch):
    monkeypatch.setattr(cli, "_workspace_root", lambda: pathlib.Path("/checkout"))
    monkeypatch.setattr(cli.shutil, "which", lambda script: f"/usr/bin/{script}")

    entry, note = cli._unified_entry("compare-mcp")

    assert entry["args"] == ["run", "--directory", str(pathlib.Path("/checkout")), "compare-mcp"]
    assert str(pathlib.Path("/checkout")) in note


def test_install_dsh_refuses_write(capsys):
    rc = cli.cmd_install(["dsh", "--write"])

    assert rc == 2
    captured = capsys.readouterr()
    assert "dsh" in captured.err
    assert not captured.out


@pytest.mark.parametrize(
    ("flag", "message"),
    [("--dry-run", "--dry-run only makes sense"), ("--force", "--force only makes sense")],
)
def test_install_flags_that_need_write_are_refused(flag, message, capsys):
    rc = cli.cmd_install(["claude", flag])

    assert rc == 2
    assert message in capsys.readouterr().err


def test_unified_entry_resolves_uv_to_an_absolute_path(monkeypatch):
    monkeypatch.setattr(cli, "_workspace_root", lambda: pathlib.Path("/checkout"))
    monkeypatch.setattr(cli.shutil, "which", lambda script: f"/usr/bin/{script}")

    entry, note = cli._unified_entry()

    assert entry["command"] == "/usr/bin/uv"
    assert entry["args"] == ["run", "--directory", str(pathlib.Path("/checkout")), "marketplace-mcp"]
    assert str(pathlib.Path("/checkout")) in note


def test_unified_entry_falls_back_to_bare_uv_with_a_warning(monkeypatch):
    monkeypatch.setattr(cli, "_workspace_root", lambda: pathlib.Path("/checkout"))
    monkeypatch.setattr(cli.shutil, "which", lambda script: None)

    entry, note = cli._unified_entry()

    assert entry["command"] == "uv"
    assert "uv was not found on PATH" in note


def test_install_warns_once_on_stderr_when_uv_is_missing(monkeypatch, capsys):
    """The warning is the caller's to print: once per command run, on stderr."""
    monkeypatch.setattr(cli, "_workspace_root", lambda: pathlib.Path("/checkout"))
    monkeypatch.setattr(cli.shutil, "which", lambda script: None)

    assert cli.cmd_install(["claude"]) == 0

    captured = capsys.readouterr()
    assert captured.err.count("uv was not found on PATH") == 1


def test_unified_entry_uses_the_installed_console_script(monkeypatch):
    monkeypatch.setattr(cli, "_workspace_root", lambda: None)
    monkeypatch.setattr(cli.shutil, "which", lambda script: f"/usr/local/bin/{script}")

    entry, note = cli._unified_entry()

    assert entry == {"command": "/usr/local/bin/marketplace-mcp", "args": []}
    assert "console script" in note


@pytest.mark.parametrize(
    ("platform", "expected_parts"),
    [
        ("win32", ("AppData", "Roaming", "Claude", "claude_desktop_config.json")),
        ("darwin", ("Library", "Application Support", "Claude", "claude_desktop_config.json")),
        ("linux", (".config", "Claude", "claude_desktop_config.json")),
    ],
)
def test_config_path_follows_the_platform(platform, expected_parts, monkeypatch, tmp_path):
    monkeypatch.delenv("MARKETPLACE_MCP_CLAUDE_CONFIG", raising=False)
    monkeypatch.setattr(cli.sys, "platform", platform)
    monkeypatch.setattr(cli.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

    path = cli._config_path("claude")

    assert path == tmp_path.joinpath(*expected_parts)


def test_config_path_for_cursor_is_under_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("MARKETPLACE_MCP_CURSOR_CONFIG", raising=False)
    monkeypatch.setattr(cli.pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    assert cli._config_path("cursor") == tmp_path / ".cursor" / "mcp.json"


# ------------------------------------------------------------------------- usage -----


def test_help_prints_a_short_usage_screen(capsys):
    assert cli.main(["--help"]) == 0

    out = capsys.readouterr().out
    assert "install" in out
    assert "doctor" in out
    assert "marketplace-mcp setup" in out
    assert "--profile" in out
    assert "marketplace-mcp install claude --write" in out
    assert "docs/QUICKSTART.md" in out
    assert len(out.splitlines()) <= 25


def test_help_verb_prints_the_same_screen(capsys):
    assert cli.main(["help"]) == 0

    assert "docs/QUICKSTART.md" in capsys.readouterr().out


def test_an_unknown_verb_prints_the_usage_and_exits_two(capsys):
    assert cli.main(["frobnicate"]) == 2

    captured = capsys.readouterr()
    assert "unknown command" in captured.err
    assert "docs/QUICKSTART.md" in captured.err


# --------------------------------------------------------------- doctor next step ----


def test_next_step_for_an_unreachable_browser():
    step = cli.next_step(
        "avito",
        "inconclusive",
        {"search": {"state": "inconclusive", "reason": "transport_down"}},
        cdp_reachable=False,
    )

    assert step == cli.NEXT_STEP_CDP_DOWN
    assert "start_chrome_cdp.ps1" in step


def test_next_step_for_a_login_wall():
    step = cli.next_step(
        "taobao",
        "inconclusive",
        {"search": {"state": "inconclusive", "reason": "login_wall"}},
        cdp_reachable=True,
    )

    assert step == cli.NEXT_STEP_BROWSER_WALL.format(source="taobao")
    assert "marketplace-mcp unblock taobao" in step
    assert "pass the check or log in" in step
    assert "-Visible" not in step


def test_next_step_for_megamarket_not_authenticated():
    step = cli.next_step(
        "megamarket",
        "inconclusive",
        {"card": {"state": "inconclusive", "reason": "not_authenticated"}},
        cdp_reachable=True,
    )

    assert step == cli.NEXT_STEP_BROWSER_WALL.format(source="megamarket")


def test_next_step_for_wb_api_refusal():
    step = cli.next_step(
        "wildberries",
        "inconclusive",
        {"card": {"state": "inconclusive", "reason": "transport_down"}},
        cdp_reachable=True,
        wb_transport="api",
    )

    assert step == cli.NEXT_STEP_WB_DOM_FALLBACK
    assert "fall back" in step
    assert "WB_TRANSPORT=dom" in step
    assert "doctor" in step


def test_next_step_for_wb_api_refusal_with_fallback_off():
    step = cli.next_step(
        "wildberries",
        "inconclusive",
        {"card": {"state": "inconclusive", "reason": "transport_down"}},
        cdp_reachable=True,
        wb_transport="api",
        wb_dom_fallback="0",
    )

    assert step == cli.NEXT_STEP_WB_TRANSPORT
    assert "WB_TRANSPORT=dom" in step
    assert "fall back" not in step


def test_next_step_in_wb_dom_mode_is_not_the_dom_advice():
    step = cli.next_step(
        "wildberries",
        "inconclusive",
        {"card": {"state": "inconclusive", "reason": "transport_down"}},
        cdp_reachable=True,
        wb_transport="dom",
    )

    assert step != cli.NEXT_STEP_WB_TRANSPORT
    assert step == cli.NEXT_STEP_ANTIBOT


def test_next_step_for_mpstats_auth():
    step = cli.next_step("mpstats", "inconclusive", {"auth": {"state": "inconclusive", "reason": "auth_missing"}})

    assert step == cli.NEXT_STEP_MPSTATS_AUTH
    assert "MPSTATS_MP_AUTH" in step


def test_next_step_for_drift():
    step = cli.next_step(
        "citilink",
        "drift_detected",
        {"search": {"state": "drift", "reason": "parse_smoke_failed"}},
    )

    assert step == cli.NEXT_STEP_DRIFT.format(source="citilink")
    assert "diagnose_drift.py citilink" in step


def test_next_step_for_drift_the_diagnose_script_cannot_probe():
    """The script's argparse refuses every source outside its own tables."""
    step = cli.next_step(
        "ozon",
        "drift_detected",
        {"search": {"state": "drift", "reason": "parse_smoke_failed"}},
    )

    assert step == cli.NEXT_STEP_DRIFT_ISSUE.format(source="ozon")
    assert "diagnose_drift.py" not in step
    assert "https://github.com/Vladimir-Human/ru-marketplace-mcp/issues" in step


def test_the_diagnose_advice_matches_what_the_script_accepts():
    """The advice must not name a script that rejects the source.

    The script is parsed, not imported or run: importing it pulls the Chrome
    transport in, and running it would touch a real browser.
    """
    script = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "diagnose_drift.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    tables: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            tables[node.target.id] = node.value
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            tables[node.targets[0].id] = node.value
    sources = ast.literal_eval(tables["SOURCES"])
    api_sources = ast.literal_eval(tables["API_SOURCES"])
    expected = frozenset(sources) | frozenset(api_sources)

    assert expected == cli.DIAGNOSE_DRIFT_SOURCES
    assert "ozon" not in cli.DIAGNOSE_DRIFT_SOURCES


def test_next_step_for_a_rate_limited_source():
    """A 429 is the site asking us to slow down: waiting is the fix."""
    step = cli.next_step(
        "avito",
        "inconclusive",
        {"search": {"state": "inconclusive", "reason": "rate_limited", "code": 429}},
        cdp_reachable=True,
    )

    assert step == cli.NEXT_STEP_RATE_LIMITED.format(source="avito")
    assert "wait 10-15 minutes" in step
    assert "marketplace-mcp doctor avito" in step


def test_next_step_for_a_crashed_selfcheck():
    """The selfcheck raised: the site never answered, so it is not a wall."""
    step = cli.next_step("avito", "error", {})

    assert step == cli.NEXT_STEP_SELFCHECK_ERROR
    assert "https://github.com/Vladimir-Human/ru-marketplace-mcp/issues" in step


def test_next_step_for_anything_else_inconclusive():
    step = cli.next_step(
        "yandex_market",
        "inconclusive",
        {"search": {"state": "inconclusive", "reason": "transport_down"}},
    )

    assert step == cli.NEXT_STEP_ANTIBOT


def test_a_healthy_source_has_no_next_step():
    assert cli.next_step("wildberries", "success", {"card": {"state": "healthy"}}) is None


@pytest.mark.parametrize(
    "step",
    [
        cli.NEXT_STEP_CDP_DOWN,
        cli.NEXT_STEP_BROWSER_WALL,
        cli.NEXT_STEP_WB_TRANSPORT,
        cli.NEXT_STEP_WB_DOM_FALLBACK,
        cli.NEXT_STEP_MPSTATS_AUTH,
        cli.NEXT_STEP_DRIFT,
        cli.NEXT_STEP_DRIFT_ISSUE,
        cli.NEXT_STEP_RATE_LIMITED,
        cli.NEXT_STEP_SELFCHECK_ERROR,
        cli.NEXT_STEP_ANTIBOT,
    ],
)
def test_next_step_strings_never_suggest_a_solver_a_proxy_or_a_retry_loop(step):
    """This project asks the operator to act; it never sells a workaround."""
    lowered = step.lower()
    for word in ("captcha", "solver", "stealth", "fingerprint", "proxy", "retry", "retries", "rotate"):
        assert word not in lowered, f"{word!r} must not appear in operator advice: {step}"


def test_doctor_prints_the_next_step_under_the_row(monkeypatch, capsys):
    async def fake_selfcheck(name, module_path, tool_name):
        return (
            name,
            "inconclusive",
            "search:inconclusive (transport_down)",
            {"search": {"state": "inconclusive", "reason": "transport_down"}},
        )

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)

    rc = cli.cmd_doctor(["avito"])

    out = capsys.readouterr().out
    assert cli.NEXT_STEP_CDP_DOWN in out
    assert out.index("avito") < out.index(cli.NEXT_STEP_CDP_DOWN)
    assert rc == 2


@pytest.mark.parametrize(
    ("fallback", "expected"),
    [("1", cli.NEXT_STEP_WB_DOM_FALLBACK), ("0", cli.NEXT_STEP_WB_TRANSPORT)],
)
def test_doctor_reads_the_wb_dom_fallback_setting(monkeypatch, capsys, fallback, expected):
    async def fake_selfcheck(name, module_path, tool_name):
        return (
            name,
            "inconclusive",
            "card:inconclusive (transport_down)",
            {"card": {"state": "inconclusive", "reason": "transport_down"}},
        )

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)
    monkeypatch.setattr(cli, "_probe_cdp", lambda: (True, ""))
    monkeypatch.delenv("WB_TRANSPORT", raising=False)
    monkeypatch.setenv("WB_DOM_FALLBACK", fallback)

    assert cli.cmd_doctor(["wildberries"]) == 2
    out = capsys.readouterr().out
    assert expected in out
    other = cli.NEXT_STEP_WB_TRANSPORT if expected == cli.NEXT_STEP_WB_DOM_FALLBACK else cli.NEXT_STEP_WB_DOM_FALLBACK
    assert other not in out


def test_doctor_status_file_carries_the_next_step(monkeypatch, capsys, tmp_path):
    async def fake_selfcheck(name, module_path, tool_name):
        return (
            name,
            "inconclusive",
            "search:inconclusive (rate_limited http 429)",
            {"search": {"state": "inconclusive", "reason": "rate_limited"}},
        )

    monkeypatch.setattr(cli, "_run_one_selfcheck", fake_selfcheck)
    target = tmp_path / "status.json"

    rc = cli.cmd_doctor(["yandex_market", "--status-file", str(target)])

    assert rc == 2
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["sources"]["yandex_market"]["next_step"] == cli.NEXT_STEP_RATE_LIMITED.format(source="yandex_market")


# -------------------------------------------------------------- browser verbs ----


def _fake_status(**overrides):
    report = {
        "reachable": True,
        "host": "127.0.0.1",
        "port": 9222,
        "version": "Chrome/151.0.0.0",
        "page_targets": 2,
        "window": "off-screen",
        "reason": None,
    }
    report.update(overrides)
    return report


def test_browser_status_reports_the_window_state(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_status():
        return _fake_status()

    monkeypatch.setattr(chrome_cdp, "browser_status", fake_status)

    assert cli.cmd_browser(["status"]) == 0

    out = capsys.readouterr().out
    assert "Chrome/151.0.0.0" in out
    assert "Page targets: 2" in out
    assert "Window: off-screen" in out
    assert "Note:" not in out


def test_browser_status_says_why_the_window_state_is_unknown(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_status():
        return _fake_status(window="unknown", reason="the window state could not be read (RuntimeError: x)")

    monkeypatch.setattr(chrome_cdp, "browser_status", fake_status)

    assert cli.cmd_browser(["status"]) == 0

    out = capsys.readouterr().out
    assert "Window: unknown" in out
    assert "Note: the window state could not be read" in out


def test_browser_status_exits_one_with_the_start_hint_when_chrome_is_absent(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_status():
        return _fake_status(reachable=False, reason="nothing answers on 127.0.0.1:9222")

    monkeypatch.setattr(chrome_cdp, "browser_status", fake_status)

    assert cli.cmd_browser(["status"]) == 1

    out = capsys.readouterr().out
    assert "not reachable" in out
    assert cli.START_CHROME_HINT in out


def test_browser_show_says_what_it_did(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_show():
        return {
            "ok": True,
            "reachable": True,
            "target_id": "page-1",
            "window_id": 7,
            "bounds": {"left": 80, "top": 40, "width": 1280, "height": 720},
        }

    monkeypatch.setattr(chrome_cdp, "show_browser_window", fake_show)

    assert cli.cmd_browser(["show"]) == 0

    out = capsys.readouterr().out
    assert "80,40" in out
    assert "1280x720" in out


def test_browser_show_exits_one_with_the_start_hint_when_chrome_is_absent(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_show():
        return {"ok": False, "reachable": False, "reason": "nothing answers on 127.0.0.1:9222"}

    monkeypatch.setattr(chrome_cdp, "show_browser_window", fake_show)

    assert cli.cmd_browser(["show"]) == 1

    err = capsys.readouterr().err
    assert "could not show" in err
    assert cli.START_CHROME_HINT in err


def test_browser_show_reports_a_window_it_could_not_move(monkeypatch, capsys):
    """The browser answers, so "start Chrome" would be the wrong advice here."""
    from mcp_core.transport import chrome_cdp

    async def fake_show():
        return {"ok": False, "reachable": True, "reason": "the window could not be moved (RuntimeError: refused)"}

    monkeypatch.setattr(chrome_cdp, "show_browser_window", fake_show)

    assert cli.cmd_browser(["show"]) == 1

    err = capsys.readouterr().err
    assert "could not show" in err
    assert cli.START_CHROME_HINT not in err


def test_browser_rejects_an_unknown_verb_and_extra_arguments(capsys):
    assert cli.cmd_browser(["frobnicate"]) == 2
    assert "unknown browser verb" in capsys.readouterr().err

    assert cli.cmd_browser([]) == 2
    assert "browser needs a verb" in capsys.readouterr().err

    assert cli.cmd_browser(["status", "--force"]) == 2
    assert "takes no arguments" in capsys.readouterr().err


def test_the_usage_screen_names_the_browser_verbs(capsys):
    assert cli.main(["--help"]) == 0

    out = capsys.readouterr().out
    assert "marketplace-mcp browser status" in out
    assert "marketplace-mcp browser show" in out
    assert "marketplace-mcp browser start" in out
    assert "marketplace-mcp unblock" in out
    assert len(out.splitlines()) <= 25


def test_main_routes_the_browser_verb(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_status():
        return _fake_status()

    monkeypatch.setattr(chrome_cdp, "browser_status", fake_status)

    assert cli.main(["browser", "status"]) == 0
    assert "Chrome/151.0.0.0" in capsys.readouterr().out


def test_browser_start_reports_a_visible_launch(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    seen: list[bool] = []

    async def fake_start(*, visible: bool = True):
        seen.append(visible)
        return {
            "ok": True,
            "started": True,
            "already_running": False,
            "host": "127.0.0.1",
            "port": 9222,
            "profile": "C:/profile",
        }

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)

    assert cli.cmd_browser(["start"]) == 0

    out = capsys.readouterr().out
    assert seen == [True]
    assert "Started Chrome with CDP on 127.0.0.1:9222" in out
    assert "C:/profile" in out


def test_browser_start_hidden_asks_for_the_off_screen_launch(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    seen: list[bool] = []

    async def fake_start(*, visible: bool = True):
        seen.append(visible)
        return {
            "ok": True,
            "started": True,
            "already_running": False,
            "host": "127.0.0.1",
            "port": 9222,
            "profile": "C:/profile",
            "off_screen": True,
        }

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)

    assert cli.cmd_browser(["start", "--hidden"]) == 0

    out = capsys.readouterr().out
    assert seen == [False]
    assert "The window is off-screen" in out
    assert "marketplace-mcp browser show" in out


def test_browser_start_hidden_that_parked_nothing_says_so(monkeypatch, capsys):
    """Outside Windows (or with CHROME_STEALTH off) --hidden parks nothing (audit A4)."""
    from mcp_core.transport import chrome_cdp

    async def fake_start(*, visible: bool = True):
        return {"ok": True, "started": True, "host": "127.0.0.1", "port": 9222, "off_screen": False}

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)

    assert cli.cmd_browser(["start", "--hidden"]) == 0

    out = capsys.readouterr().out
    assert "The window is off-screen" not in out
    assert "only on Windows with CHROME_STEALTH on" in out


def test_browser_start_already_running_points_at_browser_show(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_start(*, visible: bool = True):
        return {
            "ok": True,
            "started": False,
            "already_running": True,
            "host": "127.0.0.1",
            "port": 9222,
            "profile": "C:/profile",
        }

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)

    assert cli.cmd_browser(["start"]) == 0

    out = capsys.readouterr().out
    assert "already running" in out
    assert "marketplace-mcp browser show" in out


def test_browser_start_exits_one_with_the_reason(monkeypatch, capsys):
    from mcp_core.transport import chrome_cdp

    async def fake_start(*, visible: bool = True):
        return {"ok": False, "started": False, "already_running": False, "reason": "Chrome/Chromium not found"}

    monkeypatch.setattr(chrome_cdp, "start_browser", fake_start)

    assert cli.cmd_browser(["start"]) == 1

    err = capsys.readouterr().err
    assert "could not start" in err
    assert "Chrome/Chromium not found" in err


def test_browser_start_rejects_unknown_arguments(capsys):
    assert cli.cmd_browser(["start", "--frobnicate"]) == 2
    assert "unknown browser start option" in capsys.readouterr().err

    assert cli.cmd_browser(["start", "extra"]) == 2
    assert "takes no arguments besides --hidden" in capsys.readouterr().err


def test_main_routes_the_unblock_verb(monkeypatch, capsys):
    assert cli.main(["unblock", "ozon", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "https://www.ozon.ru/" in out
    assert "Dry run" in out


def test_install_write_keeps_the_configured_profile_without_a_profile_flag(claude_config, capsys):
    """Re-running install on an existing full entry keeps it instead of failing on a conflict."""
    full_entry, _ = cli._unified_entry("marketplace-mcp")
    claude_config.write_text(json.dumps({"mcpServers": {"marketplace": full_entry}}), encoding="utf-8")
    before = claude_config.read_bytes()

    rc = cli.cmd_install(["claude", "--write"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "already runs profile full; keeping it" in captured.err
    assert "nothing to write" in captured.out
    assert claude_config.read_bytes() == before
