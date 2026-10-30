"""Session-wide live request budget for `pytest -m live`.

The owner's budget applies to any live run, not only to `scripts/live_check.py`:
40 requests per run, 8 per marketplace, at least 3 s between two requests to the
same marketplace. This hook installs the counting once, after marker
deselection, and only when at least one selected test carries the `live`
marker. An offline run imports nothing extra and creates no ledger.

The ledger defaults to `.live-budget.json` in the repository root (ignored by
git) so `pytest -m live` and `scripts/live_check.py` share one run.

START_MODULE_CONTRACT
PURPOSE: Install the live request budget once for a pytest session that selects a live test.
SCOPE: Marker-aware collection hook; no budget or extra import for an offline selection.
DEPENDS: M-LIVE-BUDGET only on a live selection; pytest, stdlib os,sys,pathlib.
LINKS: M-LIVE-CONFTEST, R-LIVE-BUDGET, R-OFFLINE.
START_MODULE_MAP
  pytest_collection_modifyitems   install the shared ledger after marker deselection
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import os
import sys
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
