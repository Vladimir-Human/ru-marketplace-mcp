"""Validate the committed, evidence-backed model-routing evaluation matrix.

This gate checks route inventory and assignment coverage. The captured probe is
one-shot capability evidence; it deliberately does not claim a universal model
quality ranking. Live reruns should replace the artifact before making quality
or latency claims.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "work" / "evals" / "model-routing-eval.json"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT)
    args = parser.parse_args()
    report = evaluate(args.path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
