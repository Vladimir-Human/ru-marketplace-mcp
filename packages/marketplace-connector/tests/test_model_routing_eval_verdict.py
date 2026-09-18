"""The case-run verdict must be machine-checkable (independent review, 2026-09-18).

Before this, ``ok`` was the constant ``True`` and ``failed`` excluded missing
answers, so the runner's exit code could never signal anything and a throttled,
half-collected route looked as successful as a complete one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import model_routing_eval as mre

BUNDLE = Path(__file__).resolve().parents[3] / "work" / "evals" / "routing-cases-v1.json"


def test_a_complete_clean_run_is_ok() -> None:
    bundle = mre.load_bundle(BUNDLE)
    answers = {case["id"]: case["expected"] for case in bundle["cases"]}

    report = mre.evaluate_answers(bundle, answers)

    assert report["complete"] is True
    assert report["problems"] == []
    assert report["ok"] is True
    assert report["failed"] == 0


def test_a_partial_run_is_not_ok_and_missing_counts_as_failed() -> None:
    """A partial route is not usable for assignment (protocol section 8)."""
    bundle = mre.load_bundle(BUNDLE)
    cases = bundle["cases"]
    answers = {case["id"]: case["expected"] for case in cases[:5]}

    report = mre.evaluate_answers(bundle, answers)

    assert report["ok"] is False
    assert report["problems"], "the verdict must say why"
    assert "no answer" in report["problems"][0]
    assert report["failed"] == len(cases) - 5, "missing answers count against the run"
    assert report["passed"] + report["failed"] == report["total"]


def test_a_typo_in_an_answer_id_cannot_pass_silently() -> None:
    bundle = mre.load_bundle(BUNDLE)
    answers = {case["id"]: case["expected"] for case in bundle["cases"]}
    answers["routing-01 "] = "compare-prices"

    report = mre.evaluate_answers(bundle, answers)

    assert report["ok"] is False
    assert report["unknown_answer_ids"] == ["routing-01"]
    assert "match no case" in report["problems"][0]


def test_the_runner_exit_code_follows_the_verdict(tmp_path: Path) -> None:
    """main() returns 0 only for a clean run — that is the whole point of ok."""
    bundle = mre.load_bundle(BUNDLE)
    cases = bundle["cases"]
    partial = {case["id"]: case["expected"] for case in cases[:3]}
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(partial), encoding="utf-8")
    runner = Path(__file__).resolve().parents[3] / "scripts" / "model_routing_eval.py"

    result = subprocess.run(
        [sys.executable, str(runner), "run", "--cases", str(BUNDLE), "--answers", str(answers)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, "a partial run must not exit successfully"


@pytest.mark.parametrize("field", ["failed", "ok", "problems"])
def test_the_report_exposes_the_new_fields(field: str) -> None:
    bundle = mre.load_bundle(BUNDLE)
    report = mre.evaluate_answers(bundle, {})

    assert field in report
