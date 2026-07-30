#!/usr/bin/env python3
"""Fail if the documented offline-test count disagrees with the real one.

The README and ARCHITECTURE advertise how many offline tests there are, as a
plain signal of how much is actually verified. The number is written by hand in
seven places and changes every time anyone adds a test, so it rots on a
schedule: it has already been wrong three times in this repository's short life
— once as three different values in three files, and twice more while the very
audit that fixed it was adding tests of its own.

That is the same shape as the version problem `check_versions.py` solves, and it
gets the same answer: stop trusting people to remember, and let a script
compare. Collection only — no test bodies run, so this costs about a second.

Usage:
    python scripts/check_test_count.py

Runs in CI and as part of the release gate.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that quote the figure, and the pattern that finds it. The count always
# appears immediately before the word "offline"/"офлайн" so ordinary numbers in
# the same file are not mistaken for it.
QUOTED = re.compile(r"(\d[\d  ]*)(?=\s*(?:офлайн|offline))")

DOC_FILES = ["README.md", "docs/ARCHITECTURE.md"]

# The selection CI runs and the docs describe.
MARKERS = "not live and not cdp"


def _collected() -> int:
    """Ask pytest how many tests the documented selection collects."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "-m", MARKERS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # Two shapes, depending on whether the marker deselected anything:
    #   "946/950 tests collected (4 deselected)"  -> the selected count is first
    #   "950 tests collected"
    # The selected count is the one the docs mean; the total includes the live
    # tests the marker just excluded.
    match = re.search(r"(\d+)/\d+\s+tests? collected", proc.stdout) or re.search(
        r"(\d+)\s+tests? collected", proc.stdout
    )
    if match is None:
        sys.stderr.write("could not read a collected count out of pytest:\n")
        sys.stderr.write(proc.stdout[-2000:] or proc.stderr[-2000:])
        raise SystemExit(2)
    return int(match.group(1))


def main() -> int:
    actual = _collected()
    wrong: list[str] = []
    seen = 0

    for name in DOC_FILES:
        path = REPO_ROOT / name
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in QUOTED.finditer(line):
                seen += 1
                quoted = int(re.sub(r"[^\d]", "", match.group(1)))
                if quoted != actual:
                    wrong.append(f"{name}:{lineno}: says {quoted}, collection says {actual}")

    if wrong:
        sys.stderr.write("documented test count is out of date:\n")
        for item in wrong:
            sys.stderr.write(f"  {item}\n")
        sys.stderr.write(f"\nReplace it with {actual} and re-run.\n")
        return 1

    if seen == 0:
        sys.stderr.write("no documented test count found — did the wording change?\n")
        return 1

    sys.stderr.write(f"documented test count agrees in {seen} places: {actual}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
