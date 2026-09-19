"""Collection failures must not be converted into a successful documentation gate."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import check_test_count as gate


def test_partial_collection_with_errors_is_not_a_valid_count(monkeypatch, capsys):
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=2, stdout="ERROR tests/test_broken.py\n100 tests collected", stderr="import failed"
        ),
    )
    with pytest.raises(SystemExit) as exc:
        gate._collected()
    assert exc.value.code == 2
    assert "collection failed" in capsys.readouterr().err


@pytest.mark.parametrize("output,expected", [("100/103 tests collected (3 deselected)", 100), ("1 test collected", 1)])
def test_successful_collection_returns_selected_count(monkeypatch, output, expected):
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=output, stderr=""),
    )
    assert gate._collected() == expected
