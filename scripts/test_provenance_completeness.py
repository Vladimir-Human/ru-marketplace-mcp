"""The provenance gate must fail on an incomplete provenance set.

R-PROVENANCE-COMPLETENESS: a sidecar that does not parse, carries no usable
sha256 pin or has no fixture to describe is a hole in the evidence, not a
neutral file -- the gate used to skip all three and report "checked 0: ok"
with exit 0. The same goes the other way round: a fixture in the covered
directories that no sidecar describes is evidence nobody captured.

The tests drive the gate's own ``main()`` on temporary trees, the way the gate
runs in CI, and keep the still-green paths as positive controls: a matching
pin, a pin written for a CRLF checkout and the repository itself.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_provenance as gate  # noqa: E402

SIDECAR = "packages/demo-connector/tests/fixtures/card.provenance.json"


def _fixtures(tmp_path: Path) -> Path:
    """A temporary fixture directory the gate can scan."""
    fixtures = tmp_path / "packages" / "demo-connector" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    return fixtures


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run the gate on a temporary tree and return its exit code and output."""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_EXCEPTIONS", {}, raising=False)
    code = gate.main()
    return code, capsys.readouterr().out


# --- the defects: each of these fails on the pre-fix script ---------------


def test_a_sidecar_without_a_sha256_pin_fails_the_gate(tmp_path, monkeypatch, capsys) -> None:
    """An unpinned sidecar describes nothing: the gate used to skip it."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.html").write_bytes(b"<html>live</html>")
    (fixtures / "card.provenance.json").write_text(json.dumps({"captured_at": "2026-11-01"}), encoding="utf-8")

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "card.provenance.json" in output


def test_a_broken_sidecar_fails_the_gate(tmp_path, monkeypatch, capsys) -> None:
    """A sidecar that does not parse is not evidence of anything."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.html").write_bytes(b"<html>live</html>")
    (fixtures / "card.provenance.json").write_text("{broken", encoding="utf-8")

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "card.provenance.json" in output


def test_a_sidecar_without_its_fixture_fails_the_gate(tmp_path, monkeypatch, capsys) -> None:
    """An orphan sidecar describes a file that is not there."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.provenance.json").write_text(
        json.dumps({"sha256": hashlib.sha256(b"<html>live</html>").hexdigest()}), encoding="utf-8"
    )

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "card.provenance.json" in output


def test_a_fixture_without_a_sidecar_fails_the_gate(tmp_path, monkeypatch, capsys) -> None:
    """A captured fixture with no sidecar is evidence nobody captured."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.html").write_bytes(b"<html>live</html>")

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "card.html" in output


def test_an_unusable_pin_fails_the_gate(tmp_path, monkeypatch, capsys) -> None:
    """A pin that is not a sha256 digest cannot describe a file."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.html").write_bytes(b"<html>live</html>")
    (fixtures / "card.provenance.json").write_text(json.dumps({"sha256": "not-a-digest"}), encoding="utf-8")

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "card.provenance.json" in output
    assert "unusable sha256 pin" in output


def test_an_exception_covers_only_its_own_defect(tmp_path, monkeypatch, capsys) -> None:
    """A dated exception for a missing pin must not hide a missing fixture."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.provenance.json").write_text(json.dumps({"captured_at": "2026-11-01"}), encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_EXCEPTIONS", {SIDECAR: "2026-11-02: pin missing since capture"}, raising=False)

    code = gate.main()

    assert code == 1


def test_a_resolved_exception_is_reported(tmp_path, monkeypatch, capsys) -> None:
    """An exception whose gap is gone must not outlive the problem."""
    fixtures = _fixtures(tmp_path)
    body = b"<html>live</html>"
    (fixtures / "card.html").write_bytes(body)
    (fixtures / "card.provenance.json").write_text(
        json.dumps({"sha256": hashlib.sha256(body).hexdigest()}), encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_EXCEPTIONS", {SIDECAR: "2026-11-02: pin missing since capture"}, raising=False)

    code = gate.main()

    assert code == 1
    assert "not needed any more" in capsys.readouterr().out


# --- the positive controls: the valid cases still work --------------------


def test_a_matching_pin_still_passes(tmp_path, monkeypatch, capsys) -> None:
    """The ordinary case: a sidecar with a pin that matches its fixture."""
    fixtures = _fixtures(tmp_path)
    body = b"<html>live</html>"
    (fixtures / "card.html").write_bytes(body)
    (fixtures / "card.provenance.json").write_text(
        json.dumps({"sha256": hashlib.sha256(body).hexdigest()}), encoding="utf-8"
    )

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 0
    assert "ok" in output


def test_a_pin_written_for_a_crlf_checkout_is_not_stale(tmp_path, monkeypatch, capsys) -> None:
    """The committed pins must not be reported stale because of line endings."""
    fixtures = _fixtures(tmp_path)
    body = b"<html>\nlive\n</html>\n"
    (fixtures / "card.html").write_bytes(body)
    crlf = body.replace(b"\n", b"\r\n")
    (fixtures / "card.provenance.json").write_text(
        json.dumps({"sha256": hashlib.sha256(crlf).hexdigest()}), encoding="utf-8"
    )

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 0
    assert "ok" in output


def test_a_dated_exception_silences_the_gap_it_covers(tmp_path, monkeypatch, capsys) -> None:
    """A listed gap is tolerated, with its reason on record."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.html").write_bytes(b"<html>live</html>")
    (fixtures / "card.provenance.json").write_text(json.dumps({"captured_at": "2026-11-01"}), encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_EXCEPTIONS", {SIDECAR: "2026-11-02: pin missing since capture"}, raising=False)

    code = gate.main()

    assert code == 0


def test_a_wrong_pin_still_fails_the_gate(tmp_path, monkeypatch, capsys) -> None:
    """The drift the gate was built for keeps failing."""
    fixtures = _fixtures(tmp_path)
    (fixtures / "card.html").write_bytes(b"<html>live</html>")
    (fixtures / "card.provenance.json").write_text(json.dumps({"sha256": "0" * 64}), encoding="utf-8")

    code, output = _run(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "card.provenance.json" in output


def test_the_repository_passes_the_gate() -> None:
    """The real repository: every committed pin still describes its fixture."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_provenance.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
