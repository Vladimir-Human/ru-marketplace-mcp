"""The provenance gate must catch a pin that no longer describes its file."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import check_provenance as gate  # noqa: E402


def test_the_repository_passes_the_gate() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_provenance.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_a_stale_pin_is_reported(tmp_path, monkeypatch) -> None:
    """A mismatched pin must fail, and name both hashes."""
    fixtures = tmp_path / "packages" / "demo-connector" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "page.html").write_text("<html>live</html>", encoding="utf-8")
    (fixtures / "page.provenance.json").write_text(json.dumps({"sha256": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_STALE", {})

    problems = gate.check()

    assert len(problems) == 1
    assert "page.provenance.json" in problems[0]
    assert hashlib.sha256(b"<html>live</html>").hexdigest()[:16] in problems[0]


def test_a_matching_pin_is_silent(tmp_path, monkeypatch) -> None:
    fixtures = tmp_path / "packages" / "demo-connector" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    body = b"<html>live</html>"
    (fixtures / "page.html").write_bytes(body)
    (fixtures / "page.provenance.json").write_text(
        json.dumps({"sha256": hashlib.sha256(body).hexdigest()}), encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_STALE", {})

    assert gate.check() == []


def test_a_quarantined_pin_that_starts_matching_is_reported(tmp_path, monkeypatch) -> None:
    """The allowlist must not outlive the problem it tolerates."""
    fixtures = tmp_path / "packages" / "demo-connector" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    body = b"<html>live</html>"
    (fixtures / "page.html").write_bytes(body)
    (fixtures / "page.provenance.json").write_text(
        json.dumps({"sha256": hashlib.sha256(body).hexdigest()}), encoding="utf-8"
    )
    rel = "packages/demo-connector/tests/fixtures/page.provenance.json"
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "KNOWN_STALE", {rel: "dated reason"})

    problems = gate.check()

    assert len(problems) == 1
    assert "remove the KNOWN_STALE entry" in problems[0]
