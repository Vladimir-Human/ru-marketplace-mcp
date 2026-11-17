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


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.replay")
@pytest.mark.parametrize("path", sorted(RECORDINGS.glob("*.json")), ids=lambda path: path.name)
def test_committed_payloads_contain_only_what_the_recorder_can_produce(path: Path) -> None:
    """Re-cut every typed payload: the corpus must not invent response/item fields."""
    document = json.loads(path.read_text(encoding="utf-8"))
    for source, entry in document["sources"].items():
        if "response" not in entry:
            continue
        payload = entry["response"]
        cut = replay.RECORDING.cut_response(source, replay.rebuild(source, payload))
        assert {key: value for key, value in cut.items() if key != "observed_at"} == {
            key: value for key, value in payload.items() if key != "observed_at"
        }


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


async def test_synthetic_regions_stay_two_placeholders_and_the_replay_adds_nothing() -> None:
    """Plant regions only for the helper check; neither corpus source reports one."""
    recording = recorded("kettle-wb-yandex.json")
    for index, entry in enumerate(recording["sources"].values(), 1):
        entry["response"]["region"] = f"R{index}"
    regions = {entry["response"].get("region") for entry in recording["sources"].values() if "response" in entry}
    assert regions == {"R1", "R2"}
    assert replay.mixed_regions(recording) is True

    result = await replay.replay(recording)

    assert not any(warning.startswith("info:mixed_regions") for warning in result.warnings)


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.replay")
@pytest.mark.parametrize("source", ["ozon", "megamarket", "dns", "aliexpress"])
async def test_a_recorded_image_replays_as_the_same_offer_image(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic typed search answers exercise the real adapters before and after recording."""
    from types import SimpleNamespace

    plan = replay.RECORDING.SOURCE_PLANS[source]
    image_field = "image" if source == "yandex_market" else "image_url"
    item = replay._model(plan.item_model)(
        **{image_field: "https://cdn.example/kettle.jpg", "title": "Synthetic kettle"}
    )
    response = replay._model(plan.response_model)(items=[item], meta={"healthy": True, "warnings": []})

    async def search(**kwargs):
        return response

    monkeypatch.setattr(replay.server, "SOURCES", {source: SimpleNamespace(**{plan.tool: search})})
    live = await replay.server.compare_prices("kettle", sources=[source])
    document = replay.RECORDING.build_recording(
        "kettle", "2026-11-16T10:00:00Z", {source: {"response": replay.RECORDING.cut_response(source, response)}}
    )
    result = await replay.replay(document)

    assert live.offers[0].image_url == "https://cdn.example/kettle.jpg"
    assert result.offers[0].image_url == live.offers[0].image_url


def _ozon_module(card_price: float | None) -> object:
    """A stand-in Ozon module: one tile (the Ozon Card amount) and, optionally, its card."""
    from types import SimpleNamespace

    plan = replay.RECORDING.SOURCE_PLANS["ozon"]
    item = replay._model(plan.item_model)(sku=2796072502, title="Synthetic kettle", price=32296.0, price_kind="card")
    response = replay._model(plan.response_model)(items=[item], meta={"healthy": True, "warnings": []})

    async def search(**kwargs):
        return response

    async def card(sku_or_path: str):
        return replay._model(plan.card_model)(price=34990.0, card_price=card_price)

    return SimpleNamespace(**{plan.tool: search, plan.card_tool: card})


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.replay")
async def test_a_recorded_ozon_search_and_card_read_replay_as_the_live_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Ozon plan records the card read after the search: the replay ranks what live ranked."""
    module = _ozon_module(32296.0)
    replay.RECORDING.capture_sources(tmp_path, {"ozon": module})
    monkeypatch.setattr(replay.server, "SOURCES", {"ozon": module})
    live = await replay.server.compare_prices("kettle", sources=["ozon"])
    captures = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(tmp_path.glob("*.json"))]
    document = replay.RECORDING.assemble_recording("kettle", "2026-11-16T10:00:00Z", captures)

    assert document["sources"]["ozon"]["cards"] == {
        "2796072502": {"response": {"price": 34990.0, "card_price": 32296.0}}
    }
    result = await replay.replay(document)

    assert (live.offers[0].price_rub, live.offers[0].price_kind) == (34990.0, None)
    assert (result.offers[0].price_kind, result.offers[0].price_rub, result.offers[0].price_native) == (
        live.offers[0].price_kind,
        live.offers[0].price_rub,
        live.offers[0].price_native,
    )
    assert result.warnings == live.warnings


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.replay")
async def test_a_recording_without_the_ozon_card_read_fails_the_replay() -> None:
    """A recording made before compare read Ozon cards does not cover today's adapter."""
    plan = replay.RECORDING.SOURCE_PLANS["ozon"]
    item = replay._model(plan.item_model)(sku=2796072502, title="Synthetic kettle", price=32296.0, price_kind="card")
    response = replay._model(plan.response_model)(items=[item], meta={"healthy": True, "warnings": []})
    document = replay.RECORDING.build_recording(
        "kettle", "2026-11-16T10:00:00Z", {"ozon": {"response": replay.RECORDING.cut_response("ozon", response)}}
    )

    with pytest.raises(replay.UnrecordedRequest, match="card 2796072502"):
        await replay.replay(document)


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.replay")
async def test_a_recorded_avito_region_reaches_the_replayed_outcome() -> None:
    from avito_connector.models_output import AvitoSearchResponse

    response = AvitoSearchResponse(items=[], location_id="650400", meta={"healthy": True, "warnings": []})
    document = replay.RECORDING.build_recording(
        "kettle", "2026-11-16T10:00:00Z", {"avito": {"response": replay.RECORDING.cut_response("avito", response)}}
    )
    result = await replay.replay(document)

    assert document["sources"]["avito"]["response"]["region"] == "R1"
    assert result.source_outcomes[0].region == "R1"


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.replay")
async def test_a_recorded_timeout_replays_with_the_timeout_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from types import SimpleNamespace

    async def search(**kwargs):
        await asyncio.Event().wait()

    module = SimpleNamespace(avito_search=search)
    replay.RECORDING.capture_sources(tmp_path, {"avito": module})
    monkeypatch.setattr(replay.server, "SOURCES", {"avito": module})
    monkeypatch.setattr(replay.server, "SOURCE_TIMEOUT_S", 0.01)
    live = await replay.server.compare_prices("kettle", sources=["avito"])
    captures = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    document = replay.RECORDING.assemble_recording(
        "kettle", "2026-11-16T10:00:00Z", captures, [outcome.model_dump() for outcome in live.source_outcomes]
    )
    result = await replay.replay(document)
    assert result.source_outcomes[0].status == "timeout"
    assert result.source_outcomes[0].error_code == "timeout"


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.replay")
@pytest.mark.parametrize(
    "version",
    [{"schema": 2}, {"schema": None}, {"schema": "1"}, {"schema": True}, {"schema": 1.0}, {}],
    ids=["unknown", "null", "string", "boolean", "float", "missing"],
)
async def test_replay_refuses_an_unknown_or_missing_recording_schema(version: dict) -> None:
    document = recorded("kettle-partial.json")
    del document["schema"]
    document.update(version)
    with pytest.raises(ValueError, match="recording schema"):
        await replay.replay(document)


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
