"""The operator entry point proves its dispatch offline: a fake doctor round.

R-LIVE-BUDGET: the release checklist's doctor round runs through
``scripts/live_check.py``, so it must run under the same budget as the other
live commands, on a ledger of its own, and hand the doctor's own exit code
back to the caller.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import live_budget
import live_check


@pytest.fixture(autouse=True)
def _clean_installation() -> Any:
    live_budget.uninstall()
    yield
    live_budget.uninstall()


@pytest.fixture
def fake_doctor(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], bool, Any]]:
    """Replace the CLI's doctor with one that records how it was called."""
    calls: list[tuple[list[str], bool, Any]] = []

    def fake_main(argv: list[str]) -> int:
        budget = live_budget.installed_budget()
        calls.append((list(argv), budget is not None, budget.ledger.path if budget else None))
        return 2

    monkeypatch.setattr("marketplace_connector.cli.main", fake_main)
    return calls


def test_the_doctor_command_runs_under_the_budget(
    tmp_path: Path,
    fake_doctor: list[tuple[list[str], bool, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The budget is installed before the doctor starts, and the ledger is ours."""
    ledger = tmp_path / ".live-budget.json"
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(ledger))

    exit_code = live_check.main(["doctor"])

    assert exit_code == 2
    assert fake_doctor == [(["doctor"], True, ledger)]
    assert "total 0/40" in capsys.readouterr().out


def test_the_doctor_command_passes_its_arguments_through(
    tmp_path: Path,
    fake_doctor: list[tuple[list[str], bool, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))

    exit_code = live_check.main(["doctor", "ozon"])

    assert exit_code == 2
    assert fake_doctor[0][0] == ["doctor", "ozon"]


def test_the_ledger_summary_follows_the_doctor_round(
    tmp_path: Path,
    fake_doctor: list[tuple[list[str], bool, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What the run spent is reported after the doctor, not before."""
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))

    live_check.main(["doctor"])

    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1].startswith("total 0/40")


def test_the_ledger_summary_names_the_requests_the_budget_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A doctor row that read transport_down because of the cap must be explainable from the summary."""
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))
    monkeypatch.setenv("LIVE_BUDGET_PER_SOURCE", "1")

    def fake_main(argv: list[str]) -> int:
        budget = live_budget.installed_budget()
        assert budget is not None
        budget.reserve("chrome-load", "https://www.ozon.ru/")
        for _ in range(2):
            with pytest.raises(live_budget.BudgetExhausted):
                budget.reserve("in-page fetch", "https://www.ozon.ru/api/x")
        return 2

    monkeypatch.setattr("marketplace_connector.cli.main", fake_main)

    assert live_check.main(["doctor", "ozon"]) == 2

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-2] == "total 1/40; ozon 1/1"
    assert lines[-1].startswith("refused before sending (over a cap): ozon 2;")
