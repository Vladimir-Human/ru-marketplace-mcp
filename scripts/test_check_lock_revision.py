"""The uv.lock revision gate must pass a current lockfile and fail a downgraded one."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_lock_revision as gate


def _lock(revision: str) -> str:
    return f'version = 1\n{revision}\nrequires-python = ">=3.12"\n\n[[package]]\nname = "x"\n'


def test_the_committed_lockfile_is_at_or_above_the_floor():
    """The repository's own lockfile must pass the gate it defines."""
    ok, message = gate.check_lock_revision(gate.LOCK_FILE)

    assert ok, message
    assert gate.MIN_LOCK_REVISION == 5


def test_revision_five_passes(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(_lock("revision = 5"), encoding="utf-8")

    ok, message = gate.check_lock_revision(lock)

    assert ok, message


def test_a_downgraded_revision_fails_with_the_relock_hint(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(_lock("revision = 3"), encoding="utf-8")

    ok, message = gate.check_lock_revision(lock)

    assert not ok
    assert "revision 3" in message
    assert "uvx --from uv==0.13.6 uv lock" in message


def test_a_lockfile_without_a_revision_line_fails(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(_lock(""), encoding="utf-8")

    ok, message = gate.check_lock_revision(lock)

    assert not ok
    assert "revision" in message


def test_a_missing_lockfile_fails(tmp_path):
    ok, message = gate.check_lock_revision(tmp_path / "absent.lock")

    assert not ok
    assert "not found" in message


def test_main_returns_one_on_a_downgraded_lockfile(tmp_path, capsys):
    lock = tmp_path / "uv.lock"
    lock.write_text(_lock("revision = 3"), encoding="utf-8")

    assert gate.main([str(lock)]) == 1
    assert "uvx --from uv==0.13.6 uv lock" in capsys.readouterr().err
