#!/usr/bin/env python3
"""Operator entry point for live checks under the request budget.

Every live request in the repository must go through this script or through
`pytest -m live`; both install `scripts/live_budget.py` before the first
marketplace request. The ledger defaults to `.live-budget.json` in the
repository root, so several commands run one budget instead of forty each.

Usage:
    python scripts/live_check.py selfcheck <source>
    python scripts/live_check.py call <module> <tool> '<json kwargs>'
    python scripts/live_check.py doctor [source ...]
    python scripts/live_check.py ledger
    python scripts/live_check.py reset      # archive the ledger and start a new run

`doctor` runs the marketplace CLI's own doctor round - every selfcheck, live -
under the same budget as the other commands, so the release checklist's doctor
round cannot pass the caps.

Exit codes, and why 4 is separate: 0 the command ran (for doctor, everything
healthy), 1 doctor's drift alarm, 2 doctor's acknowledged blocked/inconclusive
verdict (including budget_refused), 3 a budget refusal outside doctor or a
failed doctor status-file write, 4 a checker, doctor invocation or this script's
setup/usage failed (including an unknown doctor source). The CI alarm keeps 2
non-alarm, but fails on 1 and 4: a broken checker or alarm must not hide behind
a blocked source. Doctor preserves budget refusal as unverified, never health.

START_MODULE_CONTRACT
PURPOSE: Run marketplace checks under the shared live budget without masking checker/doctor failure as a blocked source.
SCOPE: Argument parsing, budget install, selfcheck dispatch, tool call, doctor verdict propagation (0/1/2/3/4), result and ledger output.
DEPENDS: scripts/live_budget.py; marketplace_connector.cli._SELFCHECKS and its doctor command; connector modules loaded by name.
LINKS: M-LIVE-CHECK, R-LIVE-BUDGET, R-DOCTOR-EXIT-CODES.
START_MODULE_MAP
  _dump                JSON-shaped result for a model, structured content or plain value
  _reset               archive the ledger under a timestamped name to start a new run
  _doctor              run the CLI's doctor round under the budget and report the ledger
  _run                 dispatch one live command with the budget installed
  main                 CLI wrapper with exit codes
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import live_budget

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hard checker/doctor and live_check setup/usage failures share 4, never the
# doctor's non-alarm 2. Unknown doctor sources are CLI usage failures too.
USAGE_EXIT = 4


def _dump(result: Any) -> Any:
    """Turn a tool result into JSON-serializable data without losing fields."""
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    return result


def _ledger_report(budget: live_budget.Budget) -> int:
    entries = budget.ledger.load()
    counts: dict[str, int] = {}
    for entry in entries:
        group = str(entry.get("group", "?"))
        counts[group] = counts.get(group, 0) + 1
    detail = ", ".join(f"{group} {count}/{budget.per_source}" for group, count in sorted(counts.items()))
    print(f"total {len(entries)}/{budget.total}" + (f"; {detail}" if detail else ""))
    if budget.refused:
        refused: dict[str, int] = {}
        for group in budget.refused:
            refused[group] = refused.get(group, 0) + 1
        listed = ", ".join(f"{group} {count}" for group, count in sorted(refused.items()))
        print(
            f"refused before sending (over a cap): {listed}; the checks that needed them read "
            "transport_down or inconclusive, which says nothing about the site"
        )
    return 0


def _reset(budget: live_budget.Budget) -> int:
    """Start a new live run: the old ledger is kept under a timestamped name, never deleted."""
    path = budget.ledger.path
    if path is None or not path.exists():
        print("no ledger yet; the next live request starts a new run")
        return 0
    _ledger_report(budget)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    archived = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    path.replace(archived)
    print(f"archived the previous run to {archived.name}; the next live request starts a new run")
    return 0


# START_BLOCK_DOCTOR


async def _doctor(argv: list[str], budget: live_budget.Budget) -> int:
    """Run doctor; preserve hard failure 4, drift 1 and acknowledged block 2.

    The CLI owns its selfcheck event loop, so call it from a worker thread.
    Unknown-source/option usage failures come from that same CLI path. If the
    doctor invocation itself fails, report hard failure 4; budget refusal
    outside a selfcheck keeps the runner's 3. The ledger summary follows.
    """
    try:
        from marketplace_connector.cli import main as doctor_main

        code = await asyncio.to_thread(doctor_main, ["doctor", *argv])
    except live_budget.BudgetExhausted:
        raise
    except Exception as exc:
        print(f"doctor failed: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
        code = USAGE_EXIT
    _ledger_report(budget)
    return code


# END_BLOCK_DOCTOR


async def _run(argv: list[str], budget: live_budget.Budget) -> int:
    mode = argv[0]
    if mode == "ledger":
        return _ledger_report(budget)
    if mode == "reset":
        return _reset(budget)
    if mode == "doctor":
        return await _doctor(argv[1:], budget)
    if mode == "selfcheck":
        if len(argv) != 2:
            print("usage: live_check.py selfcheck <source>", file=sys.stderr)
            return USAGE_EXIT
        from marketplace_connector.cli import _SELFCHECKS

        found = next(((module, tool) for name, module, tool in _SELFCHECKS if name == argv[1]), None)
        if found is None:
            known = ", ".join(name for name, _module, _tool in _SELFCHECKS)
            print(f"unknown source {argv[1]!r}; known: {known}", file=sys.stderr)
            return USAGE_EXIT
        module_path, tool_name = found
        tool = getattr(__import__(module_path, fromlist=[tool_name]), tool_name)
        result = await tool()
    elif mode == "call":
        if len(argv) != 4:
            print("usage: live_check.py call <module> <tool> '<json kwargs>'", file=sys.stderr)
            return USAGE_EXIT
        module_path, tool_name, raw_kwargs = argv[1], argv[2], argv[3]
        try:
            kwargs = json.loads(raw_kwargs)
        except json.JSONDecodeError as exc:
            print(f"kwargs must be a JSON object: {exc}", file=sys.stderr)
            return USAGE_EXIT
        if not isinstance(kwargs, dict):
            print("kwargs must be a JSON object", file=sys.stderr)
            return USAGE_EXIT
        tool = getattr(__import__(module_path, fromlist=[tool_name]), tool_name)
        result = await tool(**kwargs)
    else:
        print(__doc__, file=sys.stderr)
        return USAGE_EXIT

    print(json.dumps(_dump(result), ensure_ascii=False, indent=1, default=str))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return USAGE_EXIT
    if not os.environ.get("LIVE_BUDGET_LEDGER"):
        os.environ["LIVE_BUDGET_LEDGER"] = str(REPO_ROOT / ".live-budget.json")
    try:
        budget = live_budget.install()
    except AttributeError as exc:
        print(f"live budget cannot be installed: {exc}", file=sys.stderr)
        return USAGE_EXIT
    try:
        return asyncio.run(_run(argv, budget))
    except live_budget.BudgetExhausted as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
