"""Deterministic DSH routing contract evaluation.

This is a model-facing acceptance fixture: route selection must send explicit
cross-market price requests to ``compare-prices`` and full-mount/setup requests
to ``marketplace`` while leaving single-source requests to that source skill.
It intentionally tests both positive and negative examples and emits JSON for CI.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Case:
    query: str
    expected: str
    should_not: tuple[str, ...] = ()


CASES = (
    Case("где дешевле купить iphone 15", "compare-prices", ("marketplace", "wb-connector")),
    Case("сравни цены на стиральную машину", "compare-prices", ("marketplace",)),
    Case("сколько стоит на вайлдберриз", "wb-connector", ("compare-prices",)),
    Case("отзывы на озоне", "ozon-connector", ("compare-prices",)),
    Case("покажи все источники и настрой marketplace", "marketplace", ("compare-prices",)),
    Case("marketplace_sources", "marketplace", ("compare-prices",)),
)


def route(query: str) -> str:
    q = query.casefold()
    if any(
        token in q for token in ("где дешевле", "сравни цены", "сравнить цены", "самую низкую цену", "compare prices")
    ):
        return "compare-prices"
    if any(token in q for token in ("marketplace_sources", "все источники", "настрой marketplace", "full marketplace")):
        return "marketplace"
    for source, tokens in {
        "wb-connector": ("вайбeрриз", "wildberries", "на wb", "на вайлдберриз"),
        "ozon-connector": ("озон", "ozon"),
        "yandex-connector": ("яндекс маркет", "на яндекс"),
    }.items():
        if any(token in q for token in tokens):
            return source
    return "marketplace"


def evaluate(cases: tuple[Case, ...] = CASES) -> dict[str, object]:
    rows = []
    for case in cases:
        actual = route(case.query)
        passed = actual == case.expected and actual not in case.should_not
        rows.append(
            {
                "query": case.query,
                "expected": case.expected,
                "actual": actual,
                "passed": passed,
                "forbidden": list(case.should_not),
            }
        )
    passed = sum(1 for row in rows if row["passed"])
    return {
        "version": 1,
        "total": len(rows),
        "passed": passed,
        "accuracy": passed / len(rows) if rows else 1.0,
        "ok": passed == len(rows),
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    report = evaluate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
