#!/usr/bin/env python3
"""Rewrite every version declaration to NEW, then run the version gate.

``check_versions.py`` finds a version that was bumped in some files and missed
in others; this script removes the chance to miss one. Both read the site table
in ``scripts/version_sites.py``, so the set of files a release must touch has a
single definition.

Only the version substring is replaced. The rest of the line, the file's line
endings and a JSON file's indentation and key order are copied byte for byte,
so the diff is the release number and nothing else.

The script deliberately stops short of the steps that need judgment: the
lockfile relock (which needs uv 0.13.6 for the CI revision gate), moving the
changelog entries under the new version's heading, and the release checklist.
It prints those after a successful bump.

Usage:
    python scripts/bump_version.py 1.3.0
    python scripts/bump_version.py v1.3.0 --dry-run

START_MODULE_CONTRACT
PURPOSE: Rewrite every declared release version atomically per file and verify the result.
SCOPE: NEW validation, dry-run report, byte-preserving rewrite, post-write gate, next-step hints.
DEPENDS: scripts/version_sites.py, scripts/check_versions.py; stdlib sys, datetime, pathlib.
LINKS: M-BUMP-VERSION, R-RELEASE-BUMP, R-VERSION.
START_MODULE_MAP
  bump                 plan or apply one version across the repository
  main                 CLI wrapper with exit codes
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

import check_versions
import version_sites

REPO_ROOT = Path(__file__).resolve().parent.parent

RELOCK_COMMAND = "uvx --from uv==0.13.6 uv lock"


def _next_steps(new: str) -> str:
    today = dt.date.today().isoformat()
    return (
        "next steps this script does not do:\n"
        f"  1. relock the workspace (uv.lock carries the 17 workspace versions): {RELOCK_COMMAND}\n"
        f"  2. move CHANGELOG [Unreleased] under [{new}] - {today}\n"
        "  3. run the release checklist: docs/RELEASE_CHECKLIST.md\n"
    )


def bump(root: Path = REPO_ROOT, new: str = "", *, dry_run: bool = False) -> int:
    """Rewrite or report every declaration under ``root``.

    Returns 0 on success, 1 when no declaration was found or the post-write
    check failed, and 2 for a malformed NEW.
    """
    match = version_sites.version_pattern(new)
    if match is None:
        sys.stderr.write(f"NEW must look like X.Y.Z (optionally v-prefixed), got {new!r}\n")
        return 2
    version = match.group(1)

    declarations = version_sites.iter_declarations(root)
    if not declarations:
        sys.stderr.write(f"no version declarations found under {root}; nothing was changed\n")
        return 1

    ordered = sorted(
        declarations,
        key=lambda item: (version_sites.display_path(item.path, root), item.lineno, item.start),
    )
    if dry_run:
        for declaration in ordered:
            where = version_sites.display_path(declaration.path, root)
            print(f"{where}:{declaration.lineno} {declaration.old} -> {version}")
        print(f"dry run: {len(ordered)} declarations would be rewritten; nothing was written")
        return 0

    by_path: dict[Path, list[version_sites.Declaration]] = defaultdict(list)
    for declaration in declarations:
        by_path[declaration.path].append(declaration)

    rewritten = 0
    for path, group in by_path.items():
        text = version_sites.read_raw(path)
        updated = text
        for declaration in sorted(group, key=lambda item: item.start, reverse=True):
            if declaration.old == version:
                continue
            updated = updated[: declaration.start] + version + updated[declaration.end :]
            rewritten += 1
        if updated != text:
            path.write_bytes(updated.encode("utf-8"))

    code = check_versions.main([], root=root)
    if code != 0:
        return code

    print(f"rewrote {rewritten} version declarations to {version}")
    print(_next_steps(version), end="")
    return 0


def main(argv: list[str], root: Path = REPO_ROOT) -> int:
    dry_run = "--dry-run" in argv
    positional = [argument for argument in argv if argument != "--dry-run"]
    if len(positional) != 1:
        sys.stderr.write("usage: python scripts/bump_version.py NEW [--dry-run]\n")
        return 2
    return bump(root, positional[0], dry_run=dry_run)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
