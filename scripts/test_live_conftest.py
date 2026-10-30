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
