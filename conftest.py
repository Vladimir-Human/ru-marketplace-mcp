"""Session-wide live request budget for `pytest -m live`.

The owner's budget applies to any live run, not only to `scripts/live_check.py`:
40 requests per run, 8 per marketplace, at least 3 s between two requests to the
same marketplace. This hook installs the counting once, after marker
deselection, and only when at least one selected test carries the `live`
marker. An offline run imports nothing extra and creates no ledger.

The ledger defaults to `.live-budget.json` in the repository root (ignored by
git) so `pytest -m live` and `scripts/live_check.py` share one run.

A live test whose request the budget refused is reported as skipped, with the
budget's reason, not as failed: the nightly doctor alarm runs first on the same
ledger, so a spent budget is the expected state there, and a failure would read
as "parser drift or an unrecognised block" when it is neither.

START_MODULE_CONTRACT
PURPOSE: Install the live request budget once for a pytest session that selects a live test; report a budget refusal as a skip.
SCOPE: Marker-aware collection and report hooks; no budget or extra import for an offline selection.
DEPENDS: M-LIVE-BUDGET only on a live selection; pytest, stdlib os,sys,pathlib.
LINKS: M-LIVE-CONFTEST, R-LIVE-BUDGET, R-OFFLINE.
START_MODULE_MAP
  pytest_collection_modifyitems   install the shared ledger after marker deselection
  pytest_runtest_makereport       a live test the budget refused is skipped with the reason
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Install the budget once per session when a live test is selected."""
    if not any(item.get_closest_marker("live") is not None for item in items):
        return

    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import live_budget

    if not os.environ.get("LIVE_BUDGET_LEDGER"):
        os.environ["LIVE_BUDGET_LEDGER"] = str(ROOT / ".live-budget.json")
    live_budget.install()


def _budget_refusal(exc: BaseException | None) -> BaseException | None:
    """The live budget's refusal behind this exception, if any.

    Tool code may wrap it (``raise_tool_error`` inside ``except Exception``), so
    the cause and context chain is walked, not only the outer exception.
    """
    budget = sys.modules.get("live_budget")
    if budget is None:
        return None
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, budget.BudgetExhausted):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Report a live test the budget refused as skipped, with the budget's reason."""
    report = yield
    if call.when != "call" or call.excinfo is None or item.get_closest_marker("live") is None:
        return report
    refusal = _budget_refusal(call.excinfo.value)
    if refusal is not None:
        report.outcome = "skipped"
        reason = f"Skipped: live budget spent before this test: {refusal}"
        report.longrepr = (str(item.path), (item.location[1] or 0) + 1, reason)
    return report
