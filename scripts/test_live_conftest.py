"""The root conftest must not touch the live budget on an offline selection.

R-LIVE-BUDGET: an offline run imports no extra module and creates no ledger;
only a selected `live` test installs the budget.
"""

from __future__ import annotations

import importlib.util
import sys
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
