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

START_MODULE_CONTRACT
PURPOSE: Run one marketplace selfcheck or tool call under the shared live budget.
SCOPE: Argument parsing, budget install, selfcheck dispatch, tool call, doctor round, result and ledger output.
DEPENDS: scripts/live_budget.py; marketplace_connector.cli._SELFCHECKS and its doctor command; connector modules loaded by name.
LINKS: M-LIVE-CHECK, R-LIVE-BUDGET.
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


async def _doctor(argv: list[str], budget: live_budget.Budget) -> int:
    """Run the marketplace CLI's doctor round under the budget.

    The CLI runs its own event loop for the selfchecks, so it is called from
    a worker thread instead of from inside this one. Its exit code is the
    doctor's own; the ledger summary follows it.
    """
    from marketplace_connector.cli import main as doctor_main

    code = await asyncio.to_thread(doctor_main, ["doctor", *argv])
    _ledger_report(budget)
    return code


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
            return 2
        from marketplace_connector.cli import _SELFCHECKS

        found = next(((module, tool) for name, module, tool in _SELFCHECKS if name == argv[1]), None)
        if found is None:
            known = ", ".join(name for name, _module, _tool in _SELFCHECKS)
            print(f"unknown source {argv[1]!r}; known: {known}", file=sys.stderr)
            return 2
        module_path, tool_name = found
        tool = getattr(__import__(module_path, fromlist=[tool_name]), tool_name)
        result = await tool()
    elif mode == "call":
        if len(argv) != 4:
            print("usage: live_check.py call <module> <tool> '<json kwargs>'", file=sys.stderr)
            return 2
        module_path, tool_name, raw_kwargs = argv[1], argv[2], argv[3]
        try:
            kwargs = json.loads(raw_kwargs)
        except json.JSONDecodeError as exc:
            print(f"kwargs must be a JSON object: {exc}", file=sys.stderr)
            return 2
        if not isinstance(kwargs, dict):
            print("kwargs must be a JSON object", file=sys.stderr)
            return 2
        tool = getattr(__import__(module_path, fromlist=[tool_name]), tool_name)
        result = await tool(**kwargs)
    else:
        print(__doc__, file=sys.stderr)
        return 2

    print(json.dumps(_dump(result), ensure_ascii=False, indent=1, default=str))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    if not os.environ.get("LIVE_BUDGET_LEDGER"):
        os.environ["LIVE_BUDGET_LEDGER"] = str(REPO_ROOT / ".live-budget.json")
    try:
        budget = live_budget.install()
    except AttributeError as exc:
        print(f"live budget cannot be installed: {exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(argv, budget))
    except live_budget.BudgetExhausted as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
