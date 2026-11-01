"""The root conftest must not touch the live budget on an offline selection.

R-LIVE-BUDGET: an offline run imports no extra module and creates no ledger;
only a selected `live` test installs the budget.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _conftest() -> Any:
    spec = importlib.util.spec_from_file_location("root_conftest_under_test", ROOT / "conftest.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_offline_selection_imports_no_budget(monkeypatch: Any) -> None:
    sys.modules.pop("live_budget", None)
    conftest = _conftest()

    class OfflineItem:
        def get_closest_marker(self, name: str) -> None:
            assert name == "live"
            return None

    conftest.pytest_collection_modifyitems(None, [OfflineItem()])

    assert "live_budget" not in sys.modules


def test_a_live_selection_installs_the_budget_on_the_repository_ledger(monkeypatch: Any) -> None:
    """The live branch must count the requests it makes (audit S4-F13).

    Without `install()` a `pytest -m live` run would hammer the marketplaces
    uncounted: the 40-requests-per-run and 8-per-marketplace budget exists for
    exactly that, and the ledger is what `scripts/live_check.py` shares with it.
    """
    installed: list[str] = []
    fake_budget = types.ModuleType("live_budget")
    fake_budget.install = lambda: installed.append("installed")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "live_budget", fake_budget)
    # setenv, not delenv: delenv of an absent variable records nothing, so the
    # ledger path the conftest writes would leak into every later test.
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", "")
    conftest = _conftest()

    class LiveItem:
        def get_closest_marker(self, name: str) -> object:
            assert name == "live"
            return object()

    conftest.pytest_collection_modifyitems(None, [LiveItem()])

    assert installed == ["installed"]
    assert os.environ["LIVE_BUDGET_LEDGER"] == str(ROOT / ".live-budget.json")


class _BudgetExhausted(RuntimeError):
    pass


def _report_after(conftest: Any, item: Any, raised: BaseException) -> Any:
    """Drive the report wrapper the way pytest does: yield, then send the report."""
    import pytest

    def boom() -> None:
        raise raised

    call = pytest.CallInfo.from_call(boom, when="call")
    report = pytest.TestReport(
        nodeid="t.py::t", location=("t.py", 3, "t"), keywords={}, outcome="failed", longrepr="boom", when="call"
    )
    wrapper = conftest.pytest_runtest_makereport(item, call)
    next(wrapper)
    try:
        wrapper.send(report)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("the wrapper must return after one yield")


class _Item:
    def __init__(self, live: bool) -> None:
        self.live = live
        self.path = Path("t.py")
        self.location = ("t.py", 3, "t")

    def get_closest_marker(self, name: str) -> object | None:
        assert name == "live"
        return object() if self.live else None


def _fake_budget(monkeypatch: Any) -> None:
    fake_budget = types.ModuleType("live_budget")
    fake_budget.BudgetExhausted = _BudgetExhausted  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "live_budget", fake_budget)


def test_a_live_test_the_budget_refused_is_reported_skipped(monkeypatch: Any) -> None:
    """The nightly doctor alarm runs first on the shared ledger, so the canary
    can meet a spent budget: that test did not fail, it was not allowed to run.
    A failure would read as "parser drift or an unrecognised block". The
    refusal is found behind a tool's wrapping error too."""
    _fake_budget(monkeypatch)
    conftest = _conftest()
    try:
        try:
            raise _BudgetExhausted("wb: 8 of 8 requests used")
        except _BudgetExhausted as refusal:
            raise ValueError("transport_down: request refused") from refusal
    except ValueError as wrapped:
        report = _report_after(conftest, _Item(live=True), wrapped)

    assert report.outcome == "skipped"
    assert "live budget spent" in report.longrepr[2]
    assert "wb: 8 of 8 requests used" in report.longrepr[2]


def test_other_failures_and_offline_tests_still_fail(monkeypatch: Any) -> None:
    """Only a budget refusal on a live test is a skip: a real failure of a live
    test, and anything an offline test raises, keep failing."""
    _fake_budget(monkeypatch)
    conftest = _conftest()

    assert _report_after(conftest, _Item(live=True), AssertionError("no priced items")).outcome == "failed"
    assert _report_after(conftest, _Item(live=False), _BudgetExhausted("cap")).outcome == "failed"
