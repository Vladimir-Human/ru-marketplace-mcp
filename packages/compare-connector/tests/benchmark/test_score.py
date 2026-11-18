"""The labelled benchmark: every recorded query and every constructed trap picks a labelled winner (plan item A7).

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-BENCHMARK-SCORE: each live recording has labels; for each labelled recording and each
         constructed trap, cheapest and cheapest_comparable fall inside the labelled sets (a confident-wrong winner
         is a failing test); the judge and the trap edit behave as the score report relies on.
SCOPE: score.py over the committed recordings, labels.json and traps.json; no marketplace request.
DEPENDS: pytest; score and replay (this folder).
LINKS: M-COMPARE-RECORDING; M-COMPARE; R-COMPARE-BENCHMARK-SCORE; R-COMPARE-MODEL-TOKENS.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import re

import pytest
import replay
import score

# Live recordings are named after their audit cycle ("c1-..."); the others are synthetic replay fixtures.
LIVE = sorted(path.stem for path in score.RECORDINGS.glob("*.json") if re.match(r"c\d+-", path.stem))


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
def test_every_live_recording_has_labels_and_two_traps() -> None:
    assert LIVE and sorted(score.LABELS) == LIVE
    per_recording = {stem: sum(trap["recording"] == stem for trap in score.TRAPS) for stem in LIVE}
    assert per_recording == dict.fromkeys(LIVE, 2)


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
@pytest.mark.parametrize("stem", LIVE)
async def test_a_recorded_query_picks_labelled_winners(stem: str) -> None:
    case = await score._case(stem, score.recording(stem), "recorded")

    assert case["cheapest"]["verdict"] == "exact", case
    assert case["cheapest_comparable"]["verdict"] == score.expected(score.LABELS[stem]["cheapest_comparable"]), case


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
@pytest.mark.parametrize("trap", score.TRAPS, ids=lambda trap: f"{trap['recording']}-{trap['class']}")
async def test_a_constructed_trap_never_wins_cheapest_comparable(trap: dict[str, str]) -> None:
    case = await score._case(
        trap["recording"], score.apply_trap(score.recording(trap["recording"]), trap), trap["class"], trap
    )

    assert case["cheapest_comparable"]["winner"] != [trap["source"], trap["product_id"]]
    assert case["cheapest_comparable"]["verdict"] == case["cheapest_comparable"]["expected"], case
    assert case["cheapest"]["verdict"] == "exact", case


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
def test_an_empty_label_set_expects_no_winner_and_a_trap_may_name_its_own_set() -> None:
    assert score.expected([]) == "abstain"
    assert score.expected([["dns", "a"]]) == "exact"
    overriding = [trap for trap in score.TRAPS if "cheapest_comparable" in trap]
    assert overriding, "a trap that retitles a labelled winner names the set that then stands"
    for trap in overriding:
        assert [trap["source"], trap["product_id"]] not in trap["cheapest_comparable"]


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
def test_the_judge_names_each_outcome() -> None:
    accepted = [["dns", "a"], ["dns", "b"]]

    assert score.judge(("dns", "b"), accepted) == "exact"
    assert score.judge(("wildberries", "a"), accepted) == "confident_wrong"
    assert score.judge(None, accepted) == "honest_unknown"
    assert score.judge(None, []) == "abstain"
    assert score.judge(("dns", "a"), []) == "confident_wrong"


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
async def test_a_planted_wrong_label_makes_the_case_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: the same replay judged against a label set it does not hit is confident_wrong."""
    stem = LIVE[0]
    wrong = {**score.LABELS[stem], "cheapest_comparable": [["nowhere", "0"]]}
    monkeypatch.setitem(score.LABELS, stem, wrong)

    case = await score._case(stem, score.recording(stem), "recorded")

    assert case["cheapest_comparable"]["verdict"] == "confident_wrong"


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
def test_a_trap_retitles_exactly_its_offer() -> None:
    trap = score.TRAPS[0]
    recorded = score.recording(trap["recording"])

    edited = score.apply_trap(recorded, trap)

    assert edited != recorded and recorded == score.recording(trap["recording"])
    items = edited["sources"][trap["source"]]["response"]["items"]
    retitled = [item for item in items if item.get("title") == trap["title"] or item.get("name") == trap["title"]]
    assert [str(item[score._ID_FIELD[trap["source"]]]) for item in retitled] == [trap["product_id"]]
    with pytest.raises(LookupError):
        score.apply_trap(recorded, {**trap, "product_id": "not-recorded"})


@pytest.mark.req("R-COMPARE-BENCHMARK-SCORE")
async def test_a_trap_recording_still_replays_through_the_recording_stub() -> None:
    trap = score.TRAPS[0]
    answer = await replay.replay(score.apply_trap(score.recording(trap["recording"]), trap))

    assert any(offer.title == trap["title"] for offer in answer.offers)
