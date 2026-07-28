#!/usr/bin/env python3
"""Fail if the version number disagrees with itself across the repository.

One release carries one version, but it is written down in forty-odd places:
every package's ``pyproject.toml`` and ``__init__.py``, every server's
``SERVER_VERSION``, the MCP registry manifest, the compose file and the
deployment guide. Bumping a release by hand means editing all of them, and the
one that gets missed is silent — the wheel installs, the tests pass, and
``package.__version__`` reports the previous release to anyone who asks.

That is exactly what happened preparing 1.2.1: fourteen ``pyproject.toml``
files moved and thirteen ``__init__.py`` files did not. Nothing caught it,
because nothing was looking. This script looks.

The root ``pyproject.toml`` is the reference; everything else must agree with
it.

Usage:
    python scripts/check_versions.py            # check against the root version
    python scripts/check_versions.py 1.3.0      # also assert the root is 1.3.0

Runs in CI and as part of the release gate.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (regex, description). Each pattern captures the version in group 1 and is
# applied line by line, so a mismatch reports a file and a line number.
PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"')
DUNDER_VERSION = re.compile(r'^__version__\s*=\s*"([^"]+)"')
SERVER_VERSION = re.compile(r'^SERVER_VERSION\s*=\s*"([^"]+)"')
IMAGE_TAG = re.compile(r"ru-marketplace-mcp:(\d[^\s\"']*)")


class Mismatch(tuple[str, int, str, str]):
    """(display path, line number, what was found, what it should be)."""


def _scan(path: Path, pattern: re.Pattern[str], expected: str) -> tuple[list[Mismatch], int]:
    """Check every line of one file against one pattern.

    Returns the mismatches and how many declarations were seen, so a silent
    zero — a file that stopped declaring its version at all — is visible in the
    summary rather than passing as clean.
    """
    mismatches: list[Mismatch] = []
    seen = 0
    display = path.relative_to(REPO_ROOT)

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = pattern.search(line)
        if match is None:
            continue
        seen += 1
        found = match.group(1)
        if found != expected:
            mismatches.append(Mismatch((str(display), lineno, found, expected)))

    return mismatches, seen


def _root_version() -> str:
    root = REPO_ROOT / "pyproject.toml"
    for line in root.read_text(encoding="utf-8").splitlines():
        match = PYPROJECT_VERSION.match(line)
        if match:
            return match.group(1)
    raise SystemExit("pyproject.toml at the repository root declares no version")


def main(argv: list[str]) -> int:
    expected = _root_version()

    if argv:
        asked = argv[0].lstrip("v")
        if asked != expected:
            sys.stderr.write(f"root pyproject.toml says {expected}, you asked for {asked}\n")
            return 1

    mismatches: list[Mismatch] = []
    counts: dict[str, int] = {}

    def sweep(label: str, paths: list[Path], pattern: re.Pattern[str]) -> None:
        total = 0
        for path in paths:
            found, seen = _scan(path, pattern, expected)
            mismatches.extend(found)
            total += seen
        counts[label] = total

    sweep(
        "pyproject.toml",
        [REPO_ROOT / "pyproject.toml", *sorted(REPO_ROOT.glob("packages/*/pyproject.toml"))],
        PYPROJECT_VERSION,
    )
    sweep("__init__.py", sorted(REPO_ROOT.glob("packages/*/src/*/__init__.py")), DUNDER_VERSION)
    sweep("SERVER_VERSION", sorted(REPO_ROOT.glob("packages/*/src/*/server.py")), SERVER_VERSION)
    sweep(
        "image tag",
        [p for p in (REPO_ROOT / "docker-compose.yml", REPO_ROOT / "docs" / "DEPLOYMENT.md") if p.exists()],
        IMAGE_TAG,
    )

    # server.json is JSON, not line-oriented text: read the field directly.
    manifest = REPO_ROOT / "server.json"
    if manifest.exists():
        declared = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        counts["server.json"] = 1 if declared is not None else 0
        if declared != expected:
            mismatches.append(Mismatch(("server.json", 0, str(declared), expected)))

    if mismatches:
        sys.stderr.write(f"version disagreements (root pyproject.toml says {expected}):\n")
        for display, lineno, found, want in sorted(mismatches):
            where = f"{display}:{lineno}" if lineno else display
            sys.stderr.write(f"  {where}: {found} should be {want}\n")
        sys.stderr.write("\nOne release carries one version. Update the files above and re-run.\n")
        return 1

    total = sum(counts.values())
    summary = ", ".join(f"{count} {label}" for label, count in counts.items())
    sys.stderr.write(f"version {expected} agreed in {total} places ({summary})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
