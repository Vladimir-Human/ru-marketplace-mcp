#!/usr/bin/env python3
"""Operator entry point for live checks under the request budget.

Every live request in the repository must go through this script or through
`pytest -m live`; both install `scripts/live_budget.py` before the first
marketplace request. The ledger defaults to `.live-budget.json` in the
repository root, so several commands run one budget instead of forty each.

Usage:
    python scripts/live_check.py selfcheck <source>
    python scripts/live_check.py call <module> <tool> '<json kwargs>'
    python scripts/live_check.py matrix [--only a,b] [--skip c]
    python scripts/live_check.py preflight  # the setup checks alone; no request
    python scripts/live_check.py doctor [source ...]
    python scripts/live_check.py ledger
    python scripts/live_check.py reset      # archive the ledger and start a new run

`doctor` runs the marketplace CLI's own doctor round - every selfcheck, live -
under the same budget as the other commands, so the release checklist's doctor
round cannot pass the caps. Matrix uses the same call machinery sequentially,
with whitelisted local history and a previous-release diff; any recorded states
exit 0, whole-run budget refusal exits 3. Matrix defaults
CHROME_CHALLENGE_HANDOFF_S=0 if unset and suppresses raw diagnostics.
Before call/matrix/doctor install any budget hooks or import connectors,
preflight verifies writable temp space, the Playwright driver and a
bare CDP attachment. It repairs TEMP/TMP or selects system Node when needed;
it never navigates or starts Chrome. Setup failure exits 4 without spending a
ledger entry.

Exit codes, and why 4 is separate: 0 the command ran (for doctor, everything
healthy), 1 doctor's drift alarm, 2 doctor's acknowledged blocked/inconclusive
verdict (including budget_refused), 3 a budget refusal outside doctor or a
failed doctor status-file write, 4 a checker, doctor invocation or this script's
setup/usage failed (including an unknown doctor source). The CI alarm keeps 2
non-alarm, but fails on 1 and 4: a broken checker or alarm must not hide behind
a blocked source. Doctor preserves budget refusal as unverified, never health.

START_MODULE_CONTRACT
PURPOSE: Run marketplace checks under the shared live budget without masking checker/doctor failure as a blocked source.
SCOPE: Temp/driver/bare-CDP preflight before call/matrix/doctor budget install, shared tool call, dispatch, doctor verdicts (0/1/2/3/4), result and ledger output.
DEPENDS: scripts/live_matrix.py; scripts/live_budget.py; mcp_core.transport.chrome_cdp endpoint; Playwright; stdlib contextlib,io,logging,tempfile,shutil,dataclasses; marketplace CLI and connector modules after preflight.
LINKS: M-LIVE-CHECK, R-LIVE-PREFLIGHT, R-LIVE-BUDGET, R-LIVE-MATRIX, R-DOCTOR-EXIT-CODES.
START_MODULE_MAP
  _check_temp          verify writable temp space, repairing TEMP/TMP inside the repo
  _check_driver        start/stop Playwright, retrying with system node on failure
  _check_cdp           bare attach/detach; on artifact EPERM: repo temp, then system node; never navigate/autostart
  _preflight           print one result per check and stop before budget/connector imports
  _dump                JSON-shaped result for a model, structured content or plain value
  _quiet_diagnostics   suppress raw prints and prebound logs for matrix; restore logging afterward
  _call_tool           one shared connector invocation for call and matrix
  matrix               dispatch guarded fixed-query probes with whitelisted local output
  _reset               archive the ledger under a timestamped name to start a new run
  _doctor              run the CLI's doctor round under the budget and report the ledger
  _run                 dispatch one live command with the budget installed
  main                 CLI wrapper with exit codes
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import io
import json
import logging
import os
import shlex
import shutil
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import live_budget

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hard checker/doctor and live_check setup/usage failures share 4, never the
# doctor's non-alarm 2. Unknown doctor sources are CLI usage failures too.
USAGE_EXIT = 4

# START_BLOCK_PREFLIGHT


@dataclass(frozen=True)
class _PreflightResult:
    status: Literal["ok", "fixed", "failed"]
    detail: str = ""


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'" if sys.platform == "win32" else shlex.quote(value)


def _temp_command(path: Path) -> str:
    quoted = _shell_quote(str(path))
    if sys.platform == "win32":
        return (
            f"New-Item -ItemType Directory -Force {quoted}; "
            f"$env:TEMP = {quoted}; $env:TMP = {quoted}; $env:TMPDIR = {quoted}"
        )
    return f"mkdir -p {quoted}; export TEMP={quoted} TMP={quoted} TMPDIR={quoted}"


def _node_command(node: str | None) -> str:
    if sys.platform == "win32":
        value = _shell_quote(node) if node else "(Get-Command node -CommandType Application).Source"
        return f"$env:PLAYWRIGHT_NODEJS_PATH = {value}"
    value = _shell_quote(node) if node else '"$(command -v node)"'
    return f"export PLAYWRIGHT_NODEJS_PATH={value}"


def _probe_temp(temp_root: Path | None = None) -> None:
    directory = Path(tempfile.mkdtemp(prefix="live-preflight-", dir=temp_root))
    try:
        (directory / "write-check").write_text("preflight\n", encoding="utf-8")
    finally:
        shutil.rmtree(directory)


def _check_temp() -> _PreflightResult:
    """R-LIVE-PREFLIGHT.temp: use the configured temp, or repair inside the repo."""
    tempfile.tempdir = None
    try:
        _probe_temp()
        return _PreflightResult("ok")
    except OSError:
        pass
    fallback = REPO_ROOT / ".tmp" / "live"
    # TMPDIR takes precedence in Python (and on POSIX Node), so repair it too.
    os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = str(fallback)
    tempfile.tempdir = None
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        _probe_temp(fallback)
    except OSError as exc:
        return _PreflightResult("failed", f"{type(exc).__name__}; run: {_temp_command(fallback)}")
    return _PreflightResult("fixed", f"TEMP=TMP=TMPDIR={fallback}")


async def _probe_driver() -> None:
    from playwright.async_api import async_playwright

    # Starting the public API proves the Node protocol handshake, not just --version.
    manager = async_playwright()
    try:
        await asyncio.wait_for(manager.start(), timeout=10)
    finally:
        # Also clean up a failed/timed-out handshake before retrying another Node.
        await asyncio.wait_for(manager.__aexit__(), timeout=10)


async def _check_driver() -> _PreflightResult:
    """R-LIVE-PREFLIGHT.driver: retry a failed Node handshake using system Node."""
    try:
        await _probe_driver()
        return _PreflightResult("ok")
    except Exception:
        pass
    node = shutil.which("node")
    if node:
        os.environ["PLAYWRIGHT_NODEJS_PATH"] = node
        try:
            await _probe_driver()
            return _PreflightResult("fixed", f"PLAYWRIGHT_NODEJS_PATH={node}")
        except Exception as exc:
            reason = type(exc).__name__
    else:
        reason = "system node not found on PATH"
    return _PreflightResult("failed", f"{reason}; run: {_node_command(node)}")


async def _probe_cdp() -> None:
    from mcp_core.transport.chrome_cdp import CDP_URL
    from playwright.async_api import async_playwright

    manager = async_playwright()
    try:
        driver = await asyncio.wait_for(manager.start(), timeout=10)
        browser = await asyncio.wait_for(driver.chromium.connect_over_cdp(CDP_URL, timeout=10_000), timeout=12)
        # A CDP-connected browser's close detaches; no contexts/pages are created or navigated.
        await asyncio.wait_for(browser.close(), timeout=10)
    finally:
        await asyncio.wait_for(manager.__aexit__(), timeout=10)


def _artifact_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return "mkdtemp" in text and ("eperm" in text or "eacces" in text)


def _repo_temp_fix() -> str | None:
    fallback = REPO_ROOT / ".tmp" / "live"
    if os.environ.get("TEMP") == str(fallback):
        return None
    fallback.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = str(fallback)
    tempfile.tempdir = None
    return f"TEMP=TMP=TMPDIR={fallback}"


def _system_node_fix() -> str | None:
    node = shutil.which("node")
    if not node or os.environ.get("PLAYWRIGHT_NODEJS_PATH") == node:
        return None
    os.environ["PLAYWRIGHT_NODEJS_PATH"] = node
    return f"PLAYWRIGHT_NODEJS_PATH={node}"


async def _check_cdp() -> _PreflightResult:
    """R-LIVE-PREFLIGHT.cdp: bare connect without Chrome autostart or requests to sites."""
    try:
        await _probe_cdp()
        return _PreflightResult("ok")
    except Exception as exc:
        failure = exc
    # Node can start yet get EPERM creating playwright-artifacts in a %TEMP% that
    # Python itself writes to (measured 2026-11-15: any node failed there, every node
    # passed with TEMP inside the repo). So move TEMP first, then try system node.
    applied: list[str] = []
    for fix in (_repo_temp_fix, _system_node_fix):
        if not _artifact_failure(failure):
            break
        change = fix()
        if change is None:
            continue
        applied.append(change)
        try:
            await _probe_cdp()
            return _PreflightResult("fixed", "; ".join(applied) + "; connected and disconnected")
        except Exception as exc:
            failure = exc
    reason = type(failure).__name__
    if _artifact_failure(failure):
        commands = f"{_temp_command(REPO_ROOT / '.tmp' / 'live')}; {_node_command(shutil.which('node'))}"
        return _PreflightResult("failed", f"{reason}; run: {commands}")
    return _PreflightResult("failed", f"{reason}; run: marketplace-mcp browser start")


async def _preflight() -> bool:
    """Finish every check before budget installation imports connector code."""
    temp = _check_temp()
    print(f"preflight temp: {temp.status}" + (f"; {temp.detail}" if temp.detail else ""), file=sys.stderr)
    if temp.status == "failed":
        return False
    for name, check in (("driver", _check_driver), ("cdp", _check_cdp)):
        result = await check()
        print(f"preflight {name}: {result.status}" + (f"; {result.detail}" if result.detail else ""), file=sys.stderr)
        if result.status == "failed":
            return False
    return True


# END_BLOCK_PREFLIGHT


def _dump(result: Any) -> Any:
    """Turn a tool result into JSON-serializable data without losing fields."""
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    return result


@contextlib.contextmanager
def _quiet_diagnostics() -> Iterator[None]:
    """Matrix whitelist: suppress prints and even loggers bound to an old stderr."""
    previous = logging.root.manager.disable
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        logging.disable(max(previous, logging.CRITICAL))
        try:
            yield
        finally:
            logging.disable(previous)


async def _call_tool(module_path: str, tool_name: str, kwargs: dict[str, Any]) -> Any:
    """One invocation/serialization path shared by call and the matrix."""
    tool = getattr(__import__(module_path, fromlist=[tool_name]), tool_name)
    return _dump(await tool(**kwargs))


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
    if mode == "matrix":
        import live_matrix

        return await live_matrix.run(argv[1:], budget)
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
        result = await _call_tool(module_path, tool_name, kwargs)
    else:
        print(__doc__, file=sys.stderr)
        return USAGE_EXIT

    print(json.dumps(_dump(result), ensure_ascii=False, indent=1, default=str))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return USAGE_EXIT
    matrix_mode = argv[0] == "matrix"
    if matrix_mode:
        import live_matrix

        try:
            live_matrix.select_cases(argv[1:])
        except (ValueError, OSError):
            print("matrix: invalid selection or fixed-query registry", file=sys.stderr)
            return USAGE_EXIT
        os.environ.setdefault("CHROME_CHALLENGE_HANDOFF_S", "0")
    if argv[0] == "preflight":
        return 0 if asyncio.run(_preflight()) else USAGE_EXIT
    if argv[0] in {"call", "matrix", "doctor"}:
        # Matrix diagnostics may contain local paths; retain the shared preflight
        # behavior, but expose only a fixed safe failure line in this mode.
        try:
            with _quiet_diagnostics() if matrix_mode else contextlib.nullcontext():
                ready = asyncio.run(_preflight())
        except Exception:
            if not matrix_mode:
                raise
            ready = False
        if not ready:
            if matrix_mode:
                print(
                    "matrix: preflight failed; `python scripts/live_check.py preflight` shows the fix", file=sys.stderr
                )
            return USAGE_EXIT
    if not os.environ.get("LIVE_BUDGET_LEDGER"):
        os.environ["LIVE_BUDGET_LEDGER"] = str(REPO_ROOT / ".live-budget.json")
    try:
        with _quiet_diagnostics() if matrix_mode else contextlib.nullcontext():
            budget = live_budget.install()
    except Exception as exc:
        if not matrix_mode and not isinstance(exc, AttributeError):
            raise
        message = "matrix: budget setup failed" if matrix_mode else f"live budget cannot be installed: {exc}"
        print(message, file=sys.stderr)
        return USAGE_EXIT
    try:
        return asyncio.run(_run(argv, budget))
    except live_budget.BudgetExhausted as exc:
        print("matrix: budget refused" if matrix_mode else f"stopped: {exc}", file=sys.stderr)
        return 3
    except Exception:
        if not matrix_mode:
            raise
        print("matrix: setup or output failed", file=sys.stderr)
        return USAGE_EXIT


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
