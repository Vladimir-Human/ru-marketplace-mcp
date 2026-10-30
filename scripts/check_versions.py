#!/usr/bin/env python3
"""Fail if the version number disagrees with itself across the repository.

One release carries one version, but it is written down in eighty-five places:
every package's ``pyproject.toml`` and ``__init__.py``, every server's
``SERVER_VERSION``, the MCP registry manifest, the compose file, the
deployment guide and the DSH README. Bumping a release by hand means editing all of them, and the
one that gets missed is silent — the wheel installs, the tests pass, and
``package.__version__`` reports the previous release to anyone who asks.

That is exactly what happened preparing 1.2.1: fourteen ``pyproject.toml``
files moved and thirteen ``__init__.py`` files did not. Nothing caught it,
because nothing was looking. This script looks.

The list of declaration sites lives in ``scripts/version_sites.py``, shared with
``scripts/bump_version.py``, so a site cannot be checked and not bumped.

The root ``pyproject.toml`` is the reference; everything else must agree with
it.

Usage:
    python scripts/check_versions.py            # check against the root version
    python scripts/check_versions.py 1.3.0      # also assert the root is 1.3.0

Runs in CI and as part of the release gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import version_sites

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str], root: Path = REPO_ROOT) -> int:
    expected = version_sites.root_version(root)

    if argv:
        asked = argv[0].lstrip("v")
        if asked != expected:
            sys.stderr.write(f"root pyproject.toml says {expected}, you asked for {asked}\n")
            return 1

    mismatches, counts, silent = version_sites.scan(root)

    if silent:
        sys.stderr.write("files that declare no version at all:\n")
        for path in sorted(silent):
            sys.stderr.write(f"  {path}\n")
        sys.stderr.write("\nA missing declaration ships the previous release's number. Add one and re-run.\n")
        return 1

    if mismatches:
        sys.stderr.write(f"version disagreements (root pyproject.toml says {expected}):\n")
        for mismatch in sorted(mismatches, key=lambda item: (item.path, item.lineno, item.found, item.want)):
            where = f"{mismatch.path}:{mismatch.lineno}" if mismatch.lineno else mismatch.path
            sys.stderr.write(f"  {where}: {mismatch.found} should be {mismatch.want}\n")
        sys.stderr.write("\nOne release carries one version. Update the files above and re-run.\n")
        return 1

    total = sum(counts.values())
    summary = ", ".join(f"{count} {label}" for label, count in counts.items())
    sys.stderr.write(f"version {expected} agreed in {total} places ({summary})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
