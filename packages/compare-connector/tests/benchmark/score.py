"""Score compare_prices on the labelled recordings and the constructed traps (plan item A7).

Run ``python packages/compare-connector/tests/benchmark/score.py`` for the report. Every
answer comes from replaying a committed recording through today's adapters and
`decide.build_response`; no marketplace is contacted.

START_MODULE_CONTRACT
PURPOSE: Count, separately for cheapest and cheapest_comparable, exact winners, confident-wrong winners and
         honest unknowns over the labelled recordings and the constructed traps (R-COMPARE-BENCHMARK-SCORE).
SCOPE: Label and trap loading, the trap edit, judging one winner, the report.
DEPENDS: replay (this folder); stdlib asyncio, copy, json, pathlib.
LINKS: M-COMPARE-RECORDING; R-COMPARE-BENCHMARK-SCORE; R-COMPARE-BENCHMARK-REPLAY.
START_MODULE_MAP
  LABELS / TRAPS: The committed label sets and constructed traps.
  recording: One committed recording by stem.
  apply_trap: A copy of a recording with one offer's title replaced.
  judge: exact, confident_wrong, honest_unknown or abstain for one winner against one label set.
  expected: The right verdict for a label set (exact, or abstain for an empty one).
  run: Replay every labelled recording and every trap and return the report.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import replay  # noqa: E402

RECORDINGS = HERE / "recordings"
LABELS: dict[str, dict[str, Any]] = json.loads((HERE / "labels.json").read_text(encoding="utf-8"))["queries"]
TRAPS: list[dict[str, str]] = json.loads((HERE / "traps.json").read_text(encoding="utf-8"))["traps"]
TARGETS = ("cheapest", "cheapest_comparable")
# The field that holds a search item's id and its title, per source, in a recorded payload.
_ID_FIELD = {"aliexpress": "item_id", "dns": "product_id", "wildberries": "nm_id", "yandex_market": "product_id"}
_TITLE_FIELD = {"wildberries": "name"}


def recording(stem: str) -> dict[str, Any]:
    return json.loads((RECORDINGS / f"{stem}.json").read_text(encoding="utf-8"))


def apply_trap(recorded: dict[str, Any], trap: dict[str, str]) -> dict[str, Any]:
    """The recording with the trap's offer retitled; every other byte stays as recorded."""
    edited = copy.deepcopy(recorded)
    source = trap["source"]
    items = edited["sources"][source]["response"]["items"]
    hits = [item for item in items if str(item[_ID_FIELD[source]]) == trap["product_id"]]
    if not hits:
        raise LookupError(f"trap offer {source} {trap['product_id']} is not in {trap['recording']}")
    for item in hits:
        item[_TITLE_FIELD.get(source, "title")] = trap["title"]
    return edited


def judge(winner: tuple[str, str] | None, accepted: list[list[str]]) -> str:
    """exact: inside the set; confident_wrong: outside it; honest_unknown: no winner while the set has one."""
    accepted_ids = {tuple(pair) for pair in accepted}
    if winner is None:
        return "honest_unknown" if accepted_ids else "abstain"
    return "exact" if winner in accepted_ids else "confident_wrong"


def _winner(answer: Any, target: str) -> tuple[str, str] | None:
    offer = getattr(answer, target)
    return None if offer is None else (offer.source, str(offer.product_id))


def expected(accepted: list[list[str]]) -> str:
    """The right verdict for a label set: a hit when the set names offers, no winner when it is empty."""
    return "exact" if accepted else "abstain"


async def _case(stem: str, recorded: dict[str, Any], kind: str, trap: dict[str, Any] | None = None) -> dict[str, Any]:
    """A trap that retitles a labelled winner names the set that then stands in its own cheapest_comparable."""
    answer = await replay.replay(recorded)
    labels = dict(LABELS[stem])
    if trap is not None and "cheapest_comparable" in trap:
        labels["cheapest_comparable"] = trap["cheapest_comparable"]
    case: dict[str, Any] = {"recording": stem, "kind": kind}
    for target in TARGETS:
        winner = _winner(answer, target)
        verdict = judge(winner, labels[target])
        case[target] = {
            "winner": list(winner) if winner else None,
            "verdict": verdict,
            "expected": expected(labels[target]),
        }
    return case


async def run() -> dict[str, Any]:
    """Every labelled recording, then every trap; counts per target, traps also per class."""
    cases = [await _case(stem, recording(stem), "recorded") for stem in sorted(LABELS)]
    for trap in TRAPS:
        case = await _case(trap["recording"], apply_trap(recording(trap["recording"]), trap), trap["class"], trap)
        cases.append(case)
    report: dict[str, Any] = {"cases": cases}
    for group, chosen in (
        ("recorded", [c for c in cases if c["kind"] == "recorded"]),
        ("traps", [c for c in cases if c["kind"] != "recorded"]),
    ):
        report[group] = {target: dict(Counter(c[target]["verdict"] for c in chosen)) for target in TARGETS}
        report[group]["total"] = len(chosen)
    report["traps"]["confident_wrong_by_class"] = dict(
        Counter(
            c["kind"]
            for c in cases
            if c["kind"] != "recorded" and c["cheapest_comparable"]["verdict"] == "confident_wrong"
        )
    )
    return report


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=False, indent=2))
    for case in result["cases"]:
        if any(case[target]["verdict"] != case[target]["expected"] for target in TARGETS):
            print(case["recording"], case["kind"], case["cheapest"]["verdict"], case["cheapest_comparable"])
