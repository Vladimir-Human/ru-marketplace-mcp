"""Offline gate for the model-level routing eval bundle and its answer runner.

The runner (``scripts/model_routing_eval.py``) never calls a model: it scores
already-collected answers deterministically. These tests pin the two halves of
that promise —

1. the committed bundle ``work/evals/routing-cases-v1.json`` is structurally
   valid and unambiguous (every expected answer exists in the catalog the model
   is shown; forbidden sets never overlap accepted ones; both slices meet the
   minimum size), and
2. the scoring arithmetic is exact on synthetic answers (all-correct = 1.0,
   half-correct = 0.5, alternatives pass, forbidden fails, missing answers are
   reported separately and never inflate ``accuracy_answered``).

The legacy matrix mode (``evaluate()`` with no arguments) stays covered by
``scripts/test_ops_gates.py``; it is re-checked here because this file owns the
runner's compatibility surface.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "model_routing_eval.py"
BUNDLE = REPO_ROOT / "work" / "evals" / "routing-cases-v1.json"


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("model_routing_eval_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mre = _load_runner()


def _bundle() -> dict[str, Any]:
    return json.loads(BUNDLE.read_text(encoding="utf-8"))


def test_committed_bundle_passes_structural_validation() -> None:
    problems = mre.validate_bundle(_bundle())
    assert problems == []


def test_bundle_meets_size_and_slice_minimums() -> None:
    bundle = mre.load_bundle(BUNDLE)
    cases = bundle["cases"]
    assert len(cases) >= 30
    for slice_name in mre.SLICES:
        assert sum(1 for case in cases if case["slice"] == slice_name) >= 15


def test_bundle_expected_answers_are_unique_and_catalogued() -> None:
    bundle = _bundle()
    skills = {s["id"] for s in bundle["system_context"]["skills"]}
    codes = {c["id"] for c in bundle["system_context"]["failure_vocabulary"]}
    ids: set[str] = set()
    for case in bundle["cases"]:
        assert case["id"] not in ids
        ids.add(case["id"])
        catalog = skills if case["slice"] == "routing" else codes
        assert case["expected"] in catalog, case["id"]
        assert all(alt in catalog for alt in case["acceptable_alternatives"]), case["id"]
        assert all(f in catalog for f in case["forbidden"]), case["id"]


def test_all_correct_answers_score_one() -> None:
    bundle = mre.load_bundle(BUNDLE)
    answers = {case["id"]: case["expected"] for case in bundle["cases"]}
    report = mre.evaluate_answers(bundle, answers, model="synthetic-perfect")
    assert report["accuracy"] == 1.0
    assert report["complete"] is True
    assert all(stats["accuracy"] == 1.0 for stats in report["per_slice"].values())


def test_half_correct_answers_score_half() -> None:
    bundle = mre.load_bundle(BUNDLE)
    cases = bundle["cases"]
    assert len(cases) % 2 == 0, "the bundle keeps an even case count so the half-score check is exact"
    half = len(cases) // 2
    answers = {
        case["id"]: (case["expected"] if index < half else "definitely-not-in-the-catalog")
        for index, case in enumerate(cases)
    }
    report = mre.evaluate_answers(bundle, answers)
    assert report["accuracy"] == 0.5
    assert report["passed"] == half


def test_alternative_answer_passes_and_forbidden_answer_fails() -> None:
    bundle = mre.load_bundle(BUNDLE)
    with_alt = next(c for c in bundle["cases"] if c["acceptable_alternatives"])
    with_forbidden = next(c for c in bundle["cases"] if c["forbidden"])
    report = mre.evaluate_answers(
        bundle,
        {with_alt["id"]: with_alt["acceptable_alternatives"][0], with_forbidden["id"]: with_forbidden["forbidden"][0]},
    )
    by_id = {row["id"]: row for row in report["cases"]}
    assert by_id[with_alt["id"]]["status"] == "pass"
    assert by_id[with_forbidden["id"]]["status"] == "forbidden"
    assert by_id[with_forbidden["id"]]["passed"] is False


def test_missing_answers_are_reported_not_inflated() -> None:
    bundle = mre.load_bundle(BUNDLE)
    cases = bundle["cases"]
    answers = {case["id"]: case["expected"] for case in cases[:5]}
    report = mre.evaluate_answers(bundle, answers)
    assert report["complete"] is False
    assert report["answered"] == 5
    assert report["missing_ids"] == [case["id"] for case in cases[5:]]
    assert report["accuracy_answered"] == 1.0
    assert report["accuracy"] == round(5 / len(cases), 4)
    assert report["unknown_answer_ids"] == []


def test_wrapper_form_and_unknown_ids_are_handled() -> None:
    bundle = mre.load_bundle(BUNDLE)
    first = bundle["cases"][0]
    report = mre.evaluate_answers(
        bundle,
        {"model": "wrapped-model", "answers": {first["id"]: first["expected"], "no-such-case": "compare-prices"}},
    )
    assert report["model"] == "wrapped-model"
    assert report["unknown_answer_ids"] == ["no-such-case"]


def test_render_prompt_shows_catalog_and_query_but_not_the_case_id() -> None:
    bundle = mre.load_bundle(BUNDLE)
    prompt = mre.render_prompt(bundle, 0)
    first = bundle["cases"][0]
    assert first["query"] in prompt
    assert bundle["system_context"]["answer_contract"] in prompt
    for skill in bundle["system_context"]["skills"]:
        assert skill["id"] in prompt
    # The id leaks the slice ("routing-" vs "error-"), so it must not appear.
    assert first["id"] not in prompt


def test_legacy_matrix_mode_is_untouched() -> None:
    report = mre.evaluate()
    assert report["ok"] is True
