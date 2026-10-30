#!/usr/bin/env python3
"""Fail if ``uv.lock`` was rewritten by an uv that downgrades its revision.

uv 0.11.x writes ``revision = 3`` into ``uv.lock``; uv 0.13.6 (the version the
Dockerfiles build with) writes ``revision = 5``. A contributor who relocks with
an older uv therefore commits a lockfile that is not the one the images build
from — and the difference is invisible until a release fails to install.

The fix is not a ``required-version`` floor in ``pyproject.toml``: that flag
applies to every uv command, so an operator whose PATH carries an older uv
could no longer start the server through the ``uv run --directory ...`` entry
that ``install --write`` writes. Users must keep working. This check asks the
contributor to relock with a current uv instead, and it runs in CI.

Usage:
    python scripts/check_lock_revision.py [path-to-uv.lock]

Runs in CI and as part of the release gate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

LOCK_FILE = REPO_ROOT / "uv.lock"

# The revision the Dockerfiles' uv (0.13.6) writes.
MIN_LOCK_REVISION = 5

# The header of uv.lock, before the first section, carries the revision.
REVISION_RE = re.compile(r"^revision\s*=\s*(\d+)\s*$", re.MULTILINE)

RELOCK_HINT = "Relock with a current uv: `uvx --from uv==0.13.6 uv lock` (any uv >= 0.13.6 will do)."


def _lock_revision(text: str) -> int | None:
    """The revision number in the lockfile header, or None when there is none."""
    header = text.split("\n[", 1)[0]
    match = REVISION_RE.search(header)
    if match is None:
        return None
    return int(match.group(1))


def check_lock_revision(lock_path: Path) -> tuple[bool, str]:
    """Check one lockfile. Returns ``(ok, message)``; the message says why."""
    if not lock_path.exists():
        return False, f"{lock_path} not found"
    try:
        text = lock_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"could not read {lock_path}: {exc}"

    revision = _lock_revision(text)
    if revision is None:
        return False, (
            f"{lock_path} has no 'revision = N' line in its header. Regenerate it with a current uv. {RELOCK_HINT}"
        )
    if revision < MIN_LOCK_REVISION:
        return False, (
            f"{lock_path} was written with revision {revision}, below the floor {MIN_LOCK_REVISION}. "
            f"An older uv downgraded it. {RELOCK_HINT}"
        )
    return True, f"uv.lock revision {revision} is at or above the floor {MIN_LOCK_REVISION}"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        sys.stderr.write("usage: check_lock_revision.py [path-to-uv.lock]\n")
        return 2
    lock_path = Path(args[0]) if args else LOCK_FILE

    ok, message = check_lock_revision(lock_path)
    if not ok:
        sys.stderr.write(f"uv.lock revision check failed: {message}\n")
        return 1
    sys.stderr.write(f"{message}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
