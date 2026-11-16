"""Replay the recorded compare calls through today's adapters (plan item A7).

The benchmark corpus is replayed, never re-fetched: every test here runs the
committed synthetic recordings through the compare connector's own adapters and
`decide.build_response`, with one recording-backed stub in place of the
marketplace search calls. No marketplace is contacted, and the recordings carry
invented titles and prices only.

START_MODULE_CONTRACT
PURPOSE: Prove that a recorded compare call replays through today's adapters unchanged.
SCOPE: The recording stub, the replay of every committed recording, the unrecorded-request failure.
DEPENDS: compare_connector.server, compare_connector.decide; the connector output models; pytest.
LINKS: M-COMPARE, M-COMPARE-RECORDING, R-COMPARE-BENCHMARK-REPLAY.
START_MODULE_MAP
  recorded: Read one committed recording.
  test_*: Requirement-derived replays over the synthetic corpus.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import replay

RECORDINGS = Path(__file__).resolve().parent / "recordings"

pytestmark = pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY")


def recorded(name: str) -> dict:
    """One committed recording, as the benchmark corpus holds it."""
    return json.loads((RECORDINGS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(RECORDINGS.glob("*.json")), ids=lambda path: path.name)
async def test_every_recording_replays_through_todays_adapters(path: Path) -> None:
    """Every recording replays: the same query, the same sources, the same verdicts."""
    recording = json.loads(path.read_text(encoding="utf-8"))

    result = await replay.replay(recording)

    assert result.query == recording["query"]
    assert result.sources_queried == list(recording["sources"])
    for source, entry in recording["sources"].items():
        outcome = next(outcome for outcome in result.source_outcomes if outcome.source == source)
        if "error" in entry:
            assert outcome.status != "ok"
            assert result.complete is False
        else:
            assert outcome.status == "ok"


async def test_the_replay_ranks_the_recorded_everyday_prices() -> None:
    """The replayed answer ranks the everyday prices and leaves the conditional ones out."""
    recording = recorded("kettle-wb-yandex.json")

    result = await replay.replay(recording)

    assert result.cheapest is not None
    assert result.cheapest.source == "wildberries"
    assert result.cheapest.price_rub == 1990.0
    # The Wallet price (1790) and the Yandex Plus price (1799) are conditional:
    # they ride along, but they never win the ranking.
    assert result.cheapest.price_rub != 1790.0
    assert result.cheapest.price_rub != 1799.0


async def test_a_source_that_errored_replays_as_a_partial_answer() -> None:
    """An errored source stays an error: the replay reproduces the partial answer."""
    recording = recorded("kettle-partial.json")

    result = await replay.replay(recording)

    outcome = next(outcome for outcome in result.source_outcomes if outcome.source == "ozon")
    assert outcome.status == "blocked"
    assert outcome.error_code == "rate_limited"
    assert result.complete is False
    assert any("partial" in warning for warning in result.warnings)


async def test_recorded_regions_stay_two_placeholders_and_the_replay_adds_nothing() -> None:
    """Two regions in one recording stay two placeholders; the replay invents no note of its own."""
    recording = recorded("kettle-wb-yandex.json")
    regions = {entry["response"].get("region") for entry in recording["sources"].values() if "response" in entry}
    assert regions == {"R1", "R2"}
    assert replay.mixed_regions(recording) is True

    result = await replay.replay(recording)

    assert not any(warning.startswith("info:mixed_regions") for warning in result.warnings)


async def test_an_unrecorded_request_fails_the_replay() -> None:
    """A source no recording covers is an error, never a silent empty answer."""
    recording = recorded("kettle-partial.json")
    planted = {
        **recording,
        "sources": {
            **recording["sources"],
            "dns": {"response": {"items": [], "meta": {"healthy": True, "warnings": []}}},
        },
    }

    with pytest.raises(replay.UnrecordedRequest):
        await replay.replay(planted, stub=replay.RecordingStub([recording]))
