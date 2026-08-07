#!/usr/bin/env python3
"""Fail if measured offline coverage drops below the documented floor.

The offline test count has ``check_test_count.py`` comparing the documented
figure to the real one. Coverage had no equivalent: the only thing standing
between "the suite quietly thinned out" and a merge was the CI job's
``--cov-fail-under`` flag, evaluated far from the change that caused the drop.
This gives coverage the same treatment the test count already gets — a script
that measures and compares, runnable locally and in the gate.

The floor is not hardcoded here. It is read from the CI workflow's
``--cov-fail-under`` flag, so this script cross-checks two sources that must
agree: the measured percentage and the documented floor. If someone raises the
floor in CI, this check enforces the new number automatically; if coverage
regresses below it, the failure points at the drop instead of surfacing in a
distant CI run.

Coverage needs the tests to actually run (a collection is not enough), so this
costs roughly one offline-suite run rather than the second that
``check_test_count.py`` costs.

Usage:
    python scripts/check_coverage_floor.py

Local gate companion in the pattern of ``check_test_count.py``: CI already
enforces the same floor through the coverage job's ``--cov-fail-under``, so
this script exists to surface a regression at the machine where the change was
made, before it travels.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The selection CI runs and the docs describe.
MARKERS = "not live and not cdp"

FLOOR_RE = re.compile(r"--cov-fail-under=(\d+(?:\.\d+)?)")


def _documented_floor() -> float:
    """Read the floor out of the CI workflow so this check enforces the same
    number CI does, rather than a copy of it."""
    if not CI_WORKFLOW.exists():
        sys.stderr.write(f"CI workflow not found at {CI_WORKFLOW}\n")
        raise SystemExit(2)
    match = FLOOR_RE.search(CI_WORKFLOW.read_text(encoding="utf-8"))
    if match is None:
        sys.stderr.write("no --cov-fail-under floor found in the CI workflow — did the wording change?\n")
        raise SystemExit(2)
    return float(match.group(1))


def _measured_coverage() -> float:
    """Run the offline suite with coverage and read the TOTAL percentage."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-m", MARKERS, "--cov", "--cov-report=term"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # The report ends with a line like:
    #   TOTAL    6777   1251   2028    343   78.90%
    match = re.search(r"^TOTAL\s+.*?(\d+(?:\.\d+)?)%\s*$", proc.stdout, re.MULTILINE)
    if match is None:
        sys.stderr.write("could not read a TOTAL coverage percentage out of pytest:\n")
        sys.stderr.write(proc.stdout[-2000:] or proc.stderr[-2000:])
        raise SystemExit(2)
    if proc.returncode != 0:
        sys.stderr.write("the offline suite itself failed; coverage is meaningless:\n")
        sys.stderr.write(proc.stdout[-2000:] or proc.stderr[-2000:])
        raise SystemExit(2)
    return float(match.group(1))


def main() -> int:
    floor = _documented_floor()
    measured = _measured_coverage()

    if measured < floor:
        sys.stderr.write(f"coverage regressed: measured {measured:.2f}% is below the documented floor {floor:.2f}%\n")
        sys.stderr.write("Restore the thinned tests, or lower the floor in the CI workflow deliberately.\n")
        return 1

    sys.stderr.write(f"coverage holds: measured {measured:.2f}% >= floor {floor:.2f}% (ci.yml)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
