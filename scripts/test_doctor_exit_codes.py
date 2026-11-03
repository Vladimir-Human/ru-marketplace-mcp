"""R17: real doctor CLI entries distinguish checker failure from a source block.

R-DOCTOR-EXIT-CODES is exercised through cli.main and live_check.main, never a
replacement aggregation helper. Fake selfcheck modules and an in-memory budget
make these checks offline; neither client configs nor temporary files are used.

START_MODULE_CONTRACT
PURPOSE: Prove R17's hard-failure, blocked, drift, healthy and budget-refusal verdicts at the operator entries.
SCOPE: All doctor sources, CLI/live runner, status counts, unblock discovery/re-check and setup health/re-check.
DEPENDS: marketplace_connector.cli; scripts/live_check.py; scripts/live_budget.py; pytest; stdlib contextlib,io,json,pathlib,sys,types.
LINKS: M-MARKETPLACE, M-LIVE-CHECK, R-DOCTOR-EXIT-CODES.
END_MODULE_CONTRACT
START_MODULE_MAP
  fake_selfchecks      importable fake connector modules at the real registry paths
  offline_runtime     fake CDP probe and a real in-memory budget, without transport hooks
  fake_browser        fake browser lifecycle for unblock/setup, never launch Chrome
  test_*              real-entry regressions, each with a valid positive control
END_MODULE_MAP
"""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from marketplace_connector import cli

sys.path.insert(0, str(Path(__file__).parent))
import live_budget
import live_check

SELFCHECKS = tuple(cli._SELFCHECKS)
ENTRIES = pytest.mark.parametrize("entry", [cli, live_check], ids=["cli", "live-check"])

# START_BLOCK_OFFLINE_RUNTIME


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    """No CDP request, budget ledger file, transport hook or ambient ledger."""
    budget = live_budget.Budget(total=40, per_source=8, gap_s=0, ledger=live_budget.Ledger())
    monkeypatch.setattr(live_budget, "install", lambda: budget)
    monkeypatch.setattr(live_budget, "installed_budget", lambda: budget)
    monkeypatch.delenv("LIVE_BUDGET_LEDGER", raising=False)
    monkeypatch.setattr(cli, "_probe_cdp", lambda: (False, "Chrome CDP: offline fake."))
    return budget


@pytest.fixture
def fake_selfchecks(monkeypatch):
    """Replace connector modules, not the CLI's selfcheck wrapper or helpers."""

    def install(source, response):
        _, module_path, tool_name = next(row for row in SELFCHECKS if row[0] == source)
        module = ModuleType(module_path)
        module.response = response
        module.calls = 0

        async def selfcheck():
            module.calls += 1
            result = module.response() if callable(module.response) else module.response
            if isinstance(result, Exception):
                raise result
            return result

        setattr(module, tool_name, selfcheck)
        monkeypatch.setitem(sys.modules, module_path, module)
        return module

    return install


@pytest.fixture
def fake_browser(monkeypatch):
    from mcp_core.transport import chrome_cdp

    events = []

    async def start(*, visible=True):
        events.append("start")
        return {"ok": True, "already_running": True, "host": "127.0.0.1", "port": 9222}

    async def open_tabs(urls):
        events.append("open")
        return {"ok": True, "target_ids": [f"target-{i}" for i in range(len(urls))]}

    async def show(*, target_id=None):
        events.append("show")
        return {"ok": True}

    monkeypatch.setattr(chrome_cdp, "start_browser", start)
    monkeypatch.setattr(chrome_cdp, "open_tabs", open_tabs)
    monkeypatch.setattr(chrome_cdp, "show_browser_window", show)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    return events


# END_BLOCK_OFFLINE_RUNTIME
# START_BLOCK_DOCTOR_VERDICTS


@ENTRIES
@pytest.mark.parametrize("source", [row[0] for row in SELFCHECKS])
def test_doctor_verdicts(entry, source, fake_selfchecks, capsys):
    """R-DOCTOR-EXIT-CODES: every source and both entries use the real wrapper."""
    module = fake_selfchecks(source, ValueError("broken selfcheck"))

    assert entry.main(["doctor", source]) == 4
    out = capsys.readouterr().out
    assert "ValueError: broken selfcheck" in out
    assert "0 healthy, 0 blocked/inconclusive, 0 drifted, 1 failed" in out
    assert cli.NEXT_STEP_SELFCHECK_ERROR in out

    # Positive controls retain all three native source verdicts, not just 0.
    for status, state, reason, code in [
        ("success", "healthy", None, 0),
        ("inconclusive", "inconclusive", "blocked", 2),
        ("drift_detected", "drift", "schema_drift", 1),
    ]:
        module.response = {"status": status, "checks": {"search": {"state": state, "reason": reason}}}
        assert entry.main(["doctor", source]) == code
        assert status in capsys.readouterr().out
    assert module.calls == 4


@ENTRIES
def test_unknown_doctor_source_is_hard(entry, fake_selfchecks, monkeypatch, capsys):
    """R-DOCTOR-EXIT-CODES: an unknown source is usage failure before any checks."""
    module = fake_selfchecks("ozon", {"status": "success", "checks": {}})

    def unexpected_probe():
        pytest.fail("invalid doctor usage must not probe CDP")

    with monkeypatch.context() as guard:
        guard.setattr(cli, "_probe_cdp", unexpected_probe)
        assert entry.main(["doctor", "ozon", "not-a-source"]) == 4
    captured = capsys.readouterr()
    assert "unknown source(s): not_a_source" in captured.err
    assert module.calls == 0
    assert "Marketplace connector health" not in captured.out

    assert entry.main(["doctor", "ozon"]) == 0
    assert module.calls == 1


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"status": "unexpected", "checks": {}}, id="unknown-status"),
        pytest.param({"checks": {}}, id="missing-status"),
        pytest.param({"status": "success", "checks": []}, id="empty-non-mapping-checks"),
        pytest.param({"status": "success", "checks": ["healthy"]}, id="non-mapping-checks"),
    ],
)
def test_invalid_selfcheck_output_is_hard(response, fake_selfchecks, capsys):
    """R-DOCTOR-EXIT-CODES: malformed checker output is never health or a block."""
    module = fake_selfchecks("ozon", response)

    assert cli.main(["doctor", "ozon"]) == 4
    out = capsys.readouterr().out
    assert "1 failed" in out
    assert cli.NEXT_STEP_SELFCHECK_ERROR in out

    module.response = {"status": "success", "checks": {}}
    assert cli.main(["doctor", "ozon"]) == 0


def test_hard_failure_precedes_drift_and_blocks(fake_selfchecks, capsys):
    """R-DOCTOR-EXIT-CODES: mixed results preserve hard failure before drift/block."""
    failed = fake_selfchecks("ozon", ValueError("broken selfcheck"))
    drifted = fake_selfchecks("wildberries", {"status": "drift_detected", "checks": {}})
    blocked = fake_selfchecks("yandex_market", {"status": "inconclusive", "checks": {}})
    args = ["doctor", "ozon", "wildberries", "yandex_market"]

    assert cli.main(args) == 4
    assert "0 healthy, 1 blocked/inconclusive, 1 drifted, 1 failed" in capsys.readouterr().out

    failed.response = {"status": "success", "checks": {}}
    assert cli.main(args) == 1
    drifted.response = {"status": "success", "checks": {}}
    assert cli.main(args) == 2
    blocked.response = {"status": "success", "checks": {}}
    assert cli.main(args) == 0


@ENTRIES
@pytest.mark.parametrize("mode", ["raised", "wrapped", "swallowed-success", "swallowed-block", "swallowed-drift"])
def test_budget_refusal_is_not_health(entry, mode, fake_selfchecks, offline_runtime, capsys):
    """R-DOCTOR-EXIT-CODES: raised/wrapped/swallowed refusal is budget_refused."""
    budget = offline_runtime
    budget.total = 0

    def refused_selfcheck():
        try:
            # reserve() only books a request; with a zero cap it refuses before
            # booking or sending anything. No transport is called in this test.
            budget.reserve("httpx", "https://www.ozon.ru/")
        except live_budget.BudgetExhausted as exc:
            if mode == "raised":
                raise
            if mode == "wrapped":
                raise RuntimeError("wrapped transport failure") from exc
            status, state, reason = {
                "swallowed-success": ("success", "healthy", None),
                "swallowed-block": ("inconclusive", "inconclusive", "transport_down"),
                "swallowed-drift": ("drift_detected", "drift", "schema_drift"),
            }[mode]
            return {"status": status, "checks": {"search": {"state": state, "reason": reason}}}
        pytest.fail("a zero-cap budget must refuse this request")

    module = fake_selfchecks("ozon", refused_selfcheck)

    code = entry.main(["doctor", "ozon"])
    out = capsys.readouterr().out
    assert "budget_refused" in out
    assert code == (1 if mode == "swallowed-drift" else 2)
    assert code != 0
    assert "0 healthy" in out
    assert "0 failed" in out
    if mode != "swallowed-drift":
        assert "this says nothing about the source" in out
    assert budget.refused == ["ozon"]
    assert budget.ledger.load() == []

    # A previous refusal must not taint a later checker that needs no request.
    module.response = {"status": "success", "checks": {}}
    assert entry.main(["doctor", "ozon"]) == 0


def test_status_report_counts_failed_checkers(fake_selfchecks, monkeypatch):
    """R-DOCTOR-EXIT-CODES: the public status snapshot has a separate failed count."""
    failed = fake_selfchecks("ozon", ValueError("broken selfcheck"))
    fake_selfchecks("wildberries", {"status": "success", "checks": {}})
    status_file = StringIO()
    status_file.name = "status.tmp"
    replaced = []
    monkeypatch.setattr(cli.tempfile, "NamedTemporaryFile", lambda *args, **kwargs: nullcontext(status_file))
    monkeypatch.setattr(Path, "replace", lambda self, target: replaced.append(target))
    args = ["doctor", "ozon", "wildberries", "--status-file", "status.json"]

    code = cli.main(args)
    report = json.loads(status_file.getvalue())
    assert report["failed"] == 1
    assert (report["healthy"], report["blocked"], report["drifted"]) == (1, 0, 0)
    assert report["sources"]["ozon"]["status"] == "error"
    assert code == 4
    assert replaced == [Path("status.json")]

    status_file.seek(0)
    status_file.truncate(0)
    failed.response = {"status": "success", "checks": {}}
    assert cli.main(args) == 0
    report = json.loads(status_file.getvalue())
    assert report["healthy"] == 2
    assert report["failed"] == 0


@ENTRIES
@pytest.mark.parametrize("status", ["success", "drift_detected", "inconclusive"])
@pytest.mark.parametrize(
    "check",
    [pytest.param(None, id="null-subcheck"), pytest.param({"state": "invalid"}, id="invalid-state")],
)
def test_supplied_invalid_subcheck_is_hard_under_every_outer_status(entry, status, check, fake_selfchecks, capsys):
    """R-DOCTOR-EXIT-CODES: supplied invalid entries cannot inherit a valid outer verdict.

    Prepublication requirement, 2026-11-03: reject a present null subcheck or
    unknown state at the real CLI/live_check entry, not a mocked validator.
    """
    module = fake_selfchecks("ozon", {"status": status, "checks": {"search": check}})

    assert entry.main(["doctor", "ozon"]) == 4
    out = capsys.readouterr().out
    assert "0 healthy, 0 blocked/inconclusive, 0 drifted, 1 failed" in out
    assert cli.NEXT_STEP_SELFCHECK_ERROR in out
    assert module.calls == 1


@ENTRIES
@pytest.mark.parametrize("representation", ["empty", "dict", "model"])
@pytest.mark.parametrize(
    "status,state,reason,code",
    [
        ("success", "healthy", None, 0),
        ("drift_detected", "drift", "schema_mismatch", 1),
        ("inconclusive", "inconclusive", "transport_down", 2),
    ],
)
def test_valid_subchecks_keep_the_existing_outer_verdict(
    entry, representation, status, state, reason, code, fake_selfchecks, capsys
):
    """R-DOCTOR-EXIT-CODES: checks={} and recognized dict/model entries stay valid.

    Baseline SelfCheckEntryBase and resilience.selfcheck_entry document states
    healthy/drift/inconclusive: schema_mismatch is a drift diagnostic reason,
    not an invented fourth state. Empty checks retain the supplied outer status.
    """
    from mcp_core.models import SelfCheckEntryBase

    if representation == "empty":
        checks = {}
    elif representation == "model":
        checks = {"search": SelfCheckEntryBase(state=state, reason=reason)}
    else:
        checks = {"search": {"state": state, "reason": reason}}
    module = fake_selfchecks("ozon", {"status": status, "checks": checks})

    assert entry.main(["doctor", "ozon"]) == code
    out = capsys.readouterr().out
    assert status in out
    assert "0 failed" in out
    assert module.calls == 1


# END_BLOCK_DOCTOR_VERDICTS
# START_BLOCK_SHARED_CALLERS


@pytest.mark.parametrize("phase", ["discovery", "recheck"])
def test_unblock_checker_failures_are_hard(phase, fake_selfchecks, fake_browser, monkeypatch, capsys):
    """R-DOCTOR-EXIT-CODES: discovery/re-check cannot hide a broken checker."""
    module = fake_selfchecks("ozon", ValueError("broken selfcheck"))
    monkeypatch.setattr(cli, "_SELFCHECKS", [row for row in SELFCHECKS if row[0] == "ozon"])
    args = ["unblock", "--wait", "0"]
    if phase == "recheck":
        args.append("ozon")

    assert cli.main(args) == 4
    out = capsys.readouterr().out
    assert "1 failed" in out
    assert "nothing to unblock" not in out
    assert fake_browser == ([] if phase == "discovery" else ["start", "open", "show"])

    module.response = {"status": "success", "checks": {}}
    assert cli.main(args) == 0


@pytest.mark.parametrize("phase", ["health", "unblock"])
def test_setup_health_and_recheck_failures_are_hard(phase, fake_selfchecks, fake_browser, monkeypatch, capsys):
    """R-DOCTOR-EXIT-CODES: setup config success never hides a failed checker."""
    wall = {"status": "inconclusive", "checks": {"search": {"state": "inconclusive", "reason": "blocked"}}}
    module = fake_selfchecks("ozon", ValueError("broken selfcheck"))
    monkeypatch.setattr(cli, "_SELFCHECKS", [row for row in SELFCHECKS if row[0] == "ozon"])
    monkeypatch.setattr(cli, "_unified_entry", lambda script: ({"command": script, "args": []}, "offline entry"))
    if phase == "unblock":
        responses = iter([wall, ValueError("broken selfcheck")])
        module.response = lambda: next(responses)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    args = ["setup", "--client", "claude-code", "--profile", "compare", "--no-browser"]

    assert cli.main(args) == 4
    out = capsys.readouterr().out
    assert "1 failed" in out
    assert "claude mcp add" in out
    assert fake_browser == ([] if phase == "health" else ["start", "open", "show"])

    healthy = {"status": "success", "checks": {}}
    if phase == "unblock":
        responses = iter([wall, healthy])
        module.response = lambda: next(responses)
    else:
        module.response = healthy
    assert cli.main(args) == 0


def test_live_check_failed_doctor_invocation_is_hard(fake_selfchecks, monkeypatch, capsys):
    """R-DOCTOR-EXIT-CODES: a doctor that never completes is a hard failure too."""
    fake_selfchecks("ozon", {"status": "success", "checks": {}})

    def broken_doctor(argv):
        raise ValueError("doctor could not run")

    with monkeypatch.context() as broken:
        broken.setattr(cli, "main", broken_doctor)
        assert live_check.main(["doctor", "ozon"]) == 4
    captured = capsys.readouterr()
    assert "doctor failed: ValueError: doctor could not run" in captured.err
    assert "total 0/40" in captured.out

    assert live_check.main(["doctor", "ozon"]) == 0


# END_BLOCK_SHARED_CALLERS
