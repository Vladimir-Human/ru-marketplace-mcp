"""Model-level routing evaluation: evidence-matrix validator + offline answer runner.

Two independent modes share this script:

1. ``matrix`` (default, no subcommand) — validate the committed, evidence-backed
   model-routing matrix (``work/evals/model-routing-eval.json``). This is the
   original gate from commit cec34a2: route inventory and assignment coverage.
   The captured probe is one-shot capability evidence; it deliberately does not
   claim a universal model quality ranking. Live reruns should replace the
   artifact before making quality or latency claims.

2. ``run`` — score ALREADY-COLLECTED model answers against a case bundle
   (``work/evals/routing-cases-v1.json``). This runner never calls a model: the
   orchestrator collects answers (a JSON object mapping case id -> answer id)
   through a DSH workflow and hands them over as a file or on stdin. Scoring is
   deterministic and offline: case-insensitive exact match against ``expected``
   or ``acceptable_alternatives``; ``forbidden`` answers always fail; missing
   answers are reported separately and count as failed for overall accuracy.

   The report keeps the ``routing`` and ``error-classification`` slices apart
   (work/v2-research/eval-matrix.md: "Do not collapse these into one score") —
   ``accuracy`` over the full bundle is reported for convenience, but the
   per-slice figures are the ones to read.

Usage:
    python scripts/model_routing_eval.py                  # matrix gate
    python scripts/model_routing_eval.py run --answers model-a.json
    python scripts/model_routing_eval.py run --cases work/evals/routing-cases-v1.json \
        --answers - --json-out work/evals/run-2026-09-13.json   # answers on stdin
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT = REPO_ROOT / "work" / "evals" / "model-routing-eval.json"
DEFAULT_CASES = REPO_ROOT / "work" / "evals" / "routing-cases-v1.json"

SLICES = ("routing", "error-classification")


# ---------------------------------------------------------------------------
# Mode 1: the committed evidence matrix (unchanged contract since cec34a2).
# ---------------------------------------------------------------------------


def evaluate(path: Path = DEFAULT) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    models = data.get("models", [])
    cases = data.get("cases", [])
    ids = {m.get("id") for m in models if isinstance(m, dict)}
    roles = {m.get("role") for m in models if isinstance(m, dict)}
    case_roles = {c.get("expected_role") for c in cases if isinstance(c, dict)}
    probe_ok = all(m.get("text") == "ok" and m.get("structured") == "ok" for m in models)
    result = {
        "version": data.get("version"),
        "model_count": len(models),
        "case_count": len(cases),
        "all_models_have_text_and_structured_probe": probe_ok,
        "all_case_roles_are_available": case_roles <= roles,
        "unique_model_ids": len(ids) == len(models),
        "ok": len(models) == 6 and probe_ok and case_roles <= roles and len(ids) == len(models),
    }
    return result


# ---------------------------------------------------------------------------
# Mode 2: the offline answer runner.
# ---------------------------------------------------------------------------


def _norm(value: object) -> str:
    """Normalise an id for comparison: strip whitespace, casefold."""
    if value is None:
        return ""
    return str(value).strip().casefold()


def load_bundle(path: Path = DEFAULT_CASES) -> dict[str, Any]:
    """Load and validate a case bundle; raise ValueError when it is unusable."""
    bundle = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_bundle(bundle)
    if problems:
        raise ValueError(f"invalid case bundle {path}:\n  " + "\n  ".join(problems))
    return bundle


def validate_bundle(bundle: Any) -> list[str]:
    """Structural checks that make the bundle safe to score against."""
    problems: list[str] = []
    if not isinstance(bundle, dict):
        return ["bundle is not a JSON object"]

    context = bundle.get("system_context")
    if not isinstance(context, dict):
        problems.append("system_context is missing or not an object")
        context = {}
    skills = {_norm(s.get("id")) for s in context.get("skills", []) if isinstance(s, dict)}
    codes = {_norm(c.get("id")) for c in context.get("failure_vocabulary", []) if isinstance(c, dict)}
    if not skills:
        problems.append("system_context.skills is empty")
    if not codes:
        problems.append("system_context.failure_vocabulary is empty")

    cases = bundle.get("cases")
    if not isinstance(cases, list) or not cases:
        return [*problems, "cases is missing or empty"]

    seen: set[str] = set()
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, dict):
            problems.append(f"{where}: not an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            problems.append(f"{where}: missing id")
            where = f"cases[{index}]({case_id})"
        elif case_id in seen:
            problems.append(f"{where}: duplicate id")
        else:
            seen.add(case_id)

        slice_name = case.get("slice")
        if slice_name not in SLICES:
            problems.append(f"{where}: slice {slice_name!r} not in {SLICES}")

        query = case.get("query")
        if not isinstance(query, str) or not query.strip():
            problems.append(f"{where}: missing query")

        expected = _norm(case.get("expected"))
        if not expected:
            problems.append(f"{where}: missing expected")

        for field in ("acceptable_alternatives", "forbidden"):
            value = case.get(field, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                problems.append(f"{where}: {field} must be a list of strings")

        alternatives = {_norm(a) for a in case.get("acceptable_alternatives", []) if isinstance(a, str)}
        forbidden = {_norm(f) for f in case.get("forbidden", []) if isinstance(f, str)}
        accepted = {expected} | alternatives
        if accepted & forbidden:
            problems.append(f"{where}: forbidden overlaps expected/alternatives: {sorted(accepted & forbidden)}")

        catalog = skills if slice_name == "routing" else codes
        if catalog:
            unknown = accepted - catalog
            if unknown:
                problems.append(f"{where}: expected/alternatives not in the {slice_name} catalog: {sorted(unknown)}")

        if not isinstance(case.get("evidence"), str) or not case.get("evidence", "").strip():
            problems.append(f"{where}: missing evidence citation")

    return problems


def render_prompt(bundle: dict[str, Any], index: int) -> str:
    """Render one case exactly as an evaluated model should see it.

    The case id is deliberately NOT part of the prompt (ids leak the slice);
    cases are presented by their 1-based position instead.
    """
    context = bundle["system_context"]
    cases = bundle["cases"]
    lines: list[str] = [context["role"], "", context["answer_contract"], "", "Skill catalog:"]
    for skill in context["skills"]:
        lines.append(f"- {skill['id']}: {skill['trigger']}")
    lines += ["", "Failure vocabulary:"]
    for code in context["failure_vocabulary"]:
        lines.append(f"- {code['id']}: {code['meaning']}")
    lines += ["", f"Case {index + 1} of {len(cases)}:", cases[index]["query"]]
    return "\n".join(lines)


def render_prompts(bundle: dict[str, Any]) -> dict[str, str]:
    """Map every case id to its rendered prompt (for orchestrators)."""
    return {case["id"]: render_prompt(bundle, index) for index, case in enumerate(bundle["cases"])}


def _slice_stats(rows: list[dict[str, Any]], slice_name: str) -> dict[str, Any]:
    subset = [row for row in rows if row["slice"] == slice_name]
    answered = [row for row in subset if row["status"] != "missing"]
    passed = [row for row in subset if row["passed"]]
    return {
        "total": len(subset),
        "answered": len(answered),
        "passed": len(passed),
        "accuracy": round(len(passed) / len(subset), 4) if subset else None,
        "accuracy_answered": round(len(passed) / len(answered), 4) if answered else None,
    }


def score_case(case: dict[str, Any], answer: object) -> dict[str, Any]:
    """Deterministic per-case verdict: pass / wrong / forbidden / missing."""
    expected = _norm(case["expected"])
    alternatives = {_norm(a) for a in case.get("acceptable_alternatives", [])}
    forbidden = {_norm(f) for f in case.get("forbidden", [])}

    given = _norm(answer) if answer is not None else ""
    if not given:
        status, passed = "missing", False
    elif given in forbidden:
        status, passed = "forbidden", False
    elif given == expected or given in alternatives:
        status, passed = "pass", True
    else:
        status, passed = "wrong", False

    return {
        "id": case["id"],
        "slice": case["slice"],
        "expected": expected,
        "alternatives": sorted(alternatives),
        "forbidden": sorted(forbidden),
        "answer": given or None,
        "status": status,
        "passed": passed,
    }


def evaluate_answers(bundle: dict[str, Any], answers: Any, model: str | None = None) -> dict[str, Any]:
    """Score an answers mapping (case id -> answer id) against the bundle."""
    if isinstance(answers, dict) and isinstance(answers.get("answers"), dict):
        model = model or answers.get("model")  # wrapper form carries metadata
        answers = answers["answers"]
    if not isinstance(answers, dict):
        raise ValueError("answers must be a JSON object mapping case id -> answer id")

    cases = bundle["cases"]
    case_ids = {case["id"] for case in cases}
    rows = [score_case(case, answers.get(case["id"])) for case in cases]

    passed = sum(1 for row in rows if row["passed"])
    answered = sum(1 for row in rows if row["status"] != "missing")
    missing_ids = [row["id"] for row in rows if row["status"] == "missing"]
    unknown = sorted(_norm(key) for key in answers if key not in case_ids)

    # The verdict has to be machine-checkable, or the exit code means nothing. It
    # says "this run is clean under the protocol" — every case answered and every
    # supplied id matched a case — not "the model scored well", which is a
    # measurement, not a protocol question. A partial run is the case that matters:
    # section 8 of the protocol says a partial route is not usable for assignment,
    # so it must not report success. (Found by an independent review, 2026-09-18.)
    problems: list[str] = []
    if missing_ids:
        shown = ", ".join(missing_ids[:3]) + (" …" if len(missing_ids) > 3 else "")
        problems.append(f"{len(missing_ids)} case(s) have no answer: {shown}")
    if unknown:
        shown = ", ".join(unknown[:3]) + (" …" if len(unknown) > 3 else "")
        problems.append(f"{len(unknown)} answer id(s) match no case in the bundle: {shown}")

    return {
        "version": 1,
        "mode": "case-run",
        "bundle": bundle.get("id"),
        "model": model,
        "total": len(rows),
        "answered": answered,
        "complete": not missing_ids,
        "missing_ids": missing_ids,
        "unknown_answer_ids": unknown,
        "passed": passed,
        # Missing answers count against the run, exactly as this module's docstring
        # promises and as the accuracy denominator already assumed.
        "failed": len(rows) - passed,
        "accuracy": round(passed / len(rows), 4) if rows else None,
        "accuracy_answered": round(passed / answered, 4) if answered else None,
        "per_slice": {name: _slice_stats(rows, name) for name in SLICES},
        "problems": problems,
        "ok": not problems,
        "cases": rows,
    }


def _load_answers(source: str) -> Any:
    if source == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    matrix = sub.add_parser("matrix", help="validate the committed evidence matrix (default when no subcommand)")
    matrix.add_argument("--path", type=Path, default=DEFAULT)

    run = sub.add_parser("run", help="score collected model answers against a case bundle (offline, deterministic)")
    run.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="case bundle JSON (default: %(default)s)")
    run.add_argument(
        "--answers",
        default=None,
        help="answers JSON file, or '-' for stdin ({case_id: answer_id}); required unless --prompts-out",
    )
    run.add_argument("--model", default=None, help="model label recorded in the report")
    run.add_argument("--json-out", type=Path, help="also write the report to this path")
    run.add_argument(
        "--prompts-out",
        type=Path,
        help="write rendered per-case prompts ({case_id: prompt}) to this path and exit — for orchestrators",
    )

    args = parser.parse_args()

    if args.command in (None, "matrix"):
        report: dict[str, Any] = evaluate(args.path if args.command == "matrix" else DEFAULT)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    try:
        bundle = load_bundle(args.cases)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"case bundle error: {exc}", file=sys.stderr)
        return 2

    if args.prompts_out:
        args.prompts_out.parent.mkdir(parents=True, exist_ok=True)
        args.prompts_out.write_text(
            json.dumps(render_prompts(bundle), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0

    if args.answers is None:
        parser.error("run requires --answers (file or '-') unless --prompts-out is given")

    try:
        answers = _load_answers(args.answers)
        report = evaluate_answers(bundle, answers, model=args.model)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"answers error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The verdict has to reach the shell: an incomplete run (a throttled provider,
    # a dropped answer file) used to exit 0 and read as success in any wrapper.
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
