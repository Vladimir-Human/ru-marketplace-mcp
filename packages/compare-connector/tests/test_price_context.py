"""Price observation context comes from answered native searches, never guesses."""
# START_MODULE_CONTRACT
# PURPOSE: Prove additive source context, receipt-time UTC and informational mixed-region notes.
# SCOPE: Committed captures, real parsers/typed answers/adapters and pure ranking; no source I/O.
# DEPENDS: compare_connector; native fixture helpers; pytest; stdlib datetime,json,urllib.parse.
# LINKS: M-COMPARE; M-COMPARE-DECIDE; R-COMPARE-PRICE-CONTEXT; R-OFFLINE; R-FIXTURES.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_context_fields_are_additive: Pin string defaults without retyping any existing field.
# test_avito_fixture_location_reaches_outcome: Preserve an already exposed queried location id.
# test_unknown_fixture_regions_stay_empty: Do not infer regions from settings, URLs or item origins.
# test_receipt_time_and_failed_source_context: Bound answered timestamps by the actual call; failures stay empty.
# test_mixed_regions_note: Name different known answered regions once; keep all ranking unchanged.
# END_MODULE_MAP

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from compare_connector import server
from compare_connector.decide import ResponseOptions, build_response
from compare_connector.models_output import MarketOffer, SourceOutcome
from mcp_core.errors import TransportDownError

from .test_image_url import _compare, _fixture, _native_response

UNKNOWN = [name for name in server.SEARCHABLE if name != "avito"]


# START_BLOCK_PRICE_CONTEXT_TESTS
@pytest.mark.req("R-COMPARE-PRICE-CONTEXT")
def test_context_fields_are_additive():
    fields = SourceOutcome.model_json_schema()["properties"]
    for name in ("observed_at", "region"):
        assert SourceOutcome().model_dump().get(name) == ""
        assert fields[name]["type"] == "string"
        assert fields[name]["default"] == ""


@pytest.mark.req("R-COMPARE-PRICE-CONTEXT")
async def test_avito_fixture_location_reaches_outcome(monkeypatch):
    tool, response = _native_response("avito")
    # The capture's queried location is a fact; no settings, city lookup or item-location inference.
    provenance = json.loads(_fixture("avito", "js_items_live.provenance.json").read_text(encoding="utf-8"))
    location_id = parse_qs(urlsplit(provenance["url"]).query)["locationId"][0]
    response = response.model_copy(update={"location_id": location_id})
    result = await _compare(monkeypatch, "avito", tool, response)

    assert len(result.offers) == 1
    assert result.source_outcomes[0].model_dump().get("region") == location_id
    assert result.source_outcomes[0].status == "ok"


@pytest.mark.req("R-COMPARE-PRICE-CONTEXT")
@pytest.mark.parametrize("source", UNKNOWN)
async def test_unknown_fixture_regions_stay_empty(monkeypatch, source):
    tool, response = _native_response(source)
    result = await _compare(monkeypatch, source, tool, response)

    assert len(result.offers) == 1
    assert result.source_outcomes[0].model_dump().get("region") == ""
    assert result.source_outcomes[0].model_dump().get("observed_at", "").endswith("Z")


@pytest.mark.req("R-COMPARE-PRICE-CONTEXT")
@pytest.mark.parametrize("healthy", [True, False])
async def test_receipt_time_and_failed_source_context(monkeypatch, healthy):
    from avito_connector.models_output import AvitoSearchResponse, MetaOut

    # An empty answer still answered, including one its own parser declares unhealthy.
    response = AvitoSearchResponse(location_id="R1", meta=MetaOut(healthy=healthy))

    async def answered(**kwargs):
        return response

    async def failed(**kwargs):
        raise TransportDownError("fixture transport unavailable", provider="yandex")

    monkeypatch.setitem(server.SOURCES, "avito", SimpleNamespace(avito_search=answered))
    monkeypatch.setitem(server.SOURCES, "yandex_market", SimpleNamespace(yandex_search=failed))
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    before = datetime.now(UTC)
    result = await server.compare_prices("product", sources=["avito", "yandex_market"])
    after = datetime.now(UTC)
    answer, failure = result.source_outcomes
    observed_at = answer.model_dump().get("observed_at", "")

    assert observed_at.endswith("Z")
    observed = datetime.fromisoformat(observed_at)
    assert observed.tzinfo == UTC
    assert before <= observed <= after
    assert answer.status == ("ok" if healthy else "unhealthy")
    assert answer.model_dump().get("region") == "R1"
    assert failure.status == "blocked"
    assert failure.model_dump().get("observed_at") == ""
    assert failure.model_dump().get("region") == ""


@pytest.mark.req("R-COMPARE-PRICE-CONTEXT")
async def test_timeout_has_no_observation_context(monkeypatch):
    async def no_answer(**kwargs):
        await asyncio.Future()

    monkeypatch.setitem(server.SOURCES, "avito", SimpleNamespace(avito_search=no_answer))
    monkeypatch.setattr(server, "SOURCE_TIMEOUT_S", 0.01)
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    result = await server.compare_prices("product", sources=["avito"])
    outcome = result.source_outcomes[0]

    assert outcome.status == "timeout"
    assert outcome.model_dump().get("observed_at") == ""
    assert outcome.model_dump().get("region") == ""


@pytest.mark.req("R-COMPARE-PRICE-CONTEXT")
@pytest.mark.parametrize(
    ("regions", "statuses", "expected"),
    [
        (("R1", "R2", ""), ("ok", "ok", "ok"), "info:mixed_regions: avito=R1, yandex_market=R2"),
        (("R1", "R2", "R1"), ("ok", "ok", "ok"), "info:mixed_regions: avito=R1, yandex_market=R2, ozon=R1"),
        (("R1", "R2", ""), ("ok", "unhealthy", "ok"), "info:mixed_regions: avito=R1, yandex_market=R2"),
        (("R1", "R1", ""), ("ok", "ok", "ok"), None),
        (("R1", "", ""), ("ok", "ok", "ok"), None),
        (("", "", ""), ("ok", "ok", "ok"), None),
        (("R1", "R2", ""), ("ok", "blocked", "ok"), None),
        (("R1", "R2", ""), ("ok", "timeout", "ok"), None),
        (("R1", "R2", ""), ("ok", "error", "ok"), None),
        (("R1", "R2", ""), ("ok", "not_installed", "ok"), None),
        # Source-native ids are not comparable across sources (Avito 637640 and WB dest -1257786 are both one city).
        (("637640", "-1257786", ""), ("ok", "ok", "ok"), None),
        (("637640", "R2", "R1"), ("ok", "ok", "ok"), "info:mixed_regions: yandex_market=R2, ozon=R1"),
    ],
)
def test_mixed_regions_note(regions, statuses, expected):
    names = ["avito", "yandex_market", "ozon"]
    results = [
        (
            SourceOutcome(source=name, status=status, region=region),
            [MarketOffer(source=name, product_id=str(index), title="product", price_rub=300 - index * 100)],
        )
        for index, (name, status, region) in enumerate(zip(names, statuses, regions, strict=True))
    ]
    options = ResponseOptions(active=names, missing=[])
    with_regions = build_response("product", results, options)
    without_regions = build_response(
        "product", [(outcome.model_copy(update={"region": ""}), offers) for outcome, offers in results], options
    )
    notes = [warning for warning in with_regions.warnings if warning.startswith("info:mixed_regions:")]

    assert notes == ([expected] if expected else [])
    assert with_regions.offers == without_regions.offers
    assert with_regions.cheapest == without_regions.cheapest
    assert with_regions.cheapest_comparable == without_regions.cheapest_comparable
    assert with_regions.price_spread_rub == without_regions.price_spread_rub
    assert with_regions.complete == without_regions.complete
    assert [warning for warning in with_regions.warnings if warning not in notes] == without_regions.warnings


# END_BLOCK_PRICE_CONTEXT_TESTS
