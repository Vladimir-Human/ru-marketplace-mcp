"""A comparison answer stays within its size budget by leaving whole offers out.

Offers average about 680 characters of JSON on the committed fixtures, so twenty
offers from each of ten marketplaces would make an answer of about 140,000
characters, far past what an agent's client shows. These tests build such
answers from synthetic offers (invented titles and prices, no marketplace) and
check the cut through the pure decision and through the MCP wire.
"""
# START_MODULE_CONTRACT
# PURPOSE: Prove R-INTERFACE-GUARDS.output-size for compare_prices: whole offers leave from the end, the rest is kept.
# SCOPE: decide.build_response on synthetic gathered results; compare_prices over the MCP client with the gather stubbed.
# DEPENDS: compare_connector.decide, compare_connector.server, compare_connector.models_output; fastmcp Client; pytest.
# LINKS: M-COMPARE-DECIDE; R-INTERFACE-GUARDS.output-size; R-NO-FABRICATION.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _offers/_results: Synthetic offers of fixture-like size, cheapest first per source.
# _size: The answer's JSON size as the guard measures it.
# test_*: A big answer is cut to whole offers within the limit; a small one is unchanged; the wire agrees.
# END_MODULE_MAP

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from compare_connector import decide, server
from compare_connector.decide import ResponseOptions, build_response
from compare_connector.models_output import CompareResponse, MarketOffer, SourceOutcome
from fastmcp import Client

SOURCES = list(server.SEARCHABLE)[:10]
# The requirement's number, written out so the test does not borrow it from the code under test.
ANSWER_CHAR_LIMIT = 40_000


def _offers(source: str, count: int) -> list[MarketOffer]:
    return [
        MarketOffer(
            source=source,
            product_id=f"{source}-{index}",
            title=f"Синтетический электрочайник модель {index} с длинным названием, 1,7 л, 2200 Вт, нержавеющая сталь",
            brand="Синтетика",
            seller=f"Продавец {index}",
            price_rub=1000.0 + 37 * index + len(source),
            url=f"https://example.invalid/{source}/product/{index}/sinteticheskiy-elektrochaynik-model-{index}",
            image_url=f"https://cdn.example.invalid/{source}/{index}/main-photo-large.jpg",
            rating=4.5,
            rating_count=100 + index,
            in_stock=True,
        )
        for index in range(count)
    ]


def _results(per_source: int) -> list[tuple[SourceOutcome, list[MarketOffer]]]:
    return [(SourceOutcome(source=name, status="ok"), _offers(name, per_source)) for name in SOURCES]


def _size(answer: CompareResponse | dict) -> int:
    data = answer.model_dump(mode="json") if isinstance(answer, CompareResponse) else answer
    return len(json.dumps(data, ensure_ascii=False))


def _build(per_source: int) -> CompareResponse:
    options = ResponseOptions(active=SOURCES, missing=[], server_version="t")
    return build_response("электрочайник", _results(per_source), options)


@pytest.mark.req("R-INTERFACE-GUARDS.output-size")
def test_a_big_answer_keeps_whole_offers_from_the_top_within_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(decide, "_fit_answer", lambda response: response, raising=False)
        uncut = _build(20)
    assert _size(uncut) > ANSWER_CHAR_LIMIT, "the synthetic answer must be over the limit to test the cut"

    answer = _build(20)

    kept = len(answer.offers)
    assert _size(answer) <= ANSWER_CHAR_LIMIT
    assert 0 < kept < len(uncut.offers)
    # Whole offers, unchanged, in the ranked order: the kept list is the head of the uncut one.
    assert answer.offers == uncut.offers[:kept]
    # Not cut deeper than needed: one more offer would not fit.
    assert _size(answer.model_copy(update={"offers": uncut.offers[: kept + 1]})) > ANSWER_CHAR_LIMIT
    assert answer.total_offers == uncut.total_offers == len(SOURCES) * 20
    assert (answer.cheapest, answer.cheapest_comparable, answer.price_spread_rub) == (
        uncut.cheapest,
        uncut.cheapest_comparable,
        uncut.price_spread_rub,
    )
    notes = [warning for warning in answer.warnings if warning.startswith("info:offers_omitted:")]
    assert notes == [
        f"info:offers_omitted: {answer.total_offers - kept} of {answer.total_offers} offers left out from the end "
        f"of the ranked list to keep the answer under {ANSWER_CHAR_LIMIT} characters; for more, ask fewer sources "
        "or a lower per_source_limit"
    ]
    assert answer.warnings[:-1] == uncut.warnings


@pytest.mark.req("R-INTERFACE-GUARDS.output-size")
def test_the_code_uses_the_requirement_limit() -> None:
    assert decide.ANSWER_CHAR_LIMIT == ANSWER_CHAR_LIMIT


@pytest.mark.req("R-INTERFACE-GUARDS.output-size")
def test_an_answer_within_the_limit_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(decide, "_fit_answer", lambda response: response, raising=False)
        uncut = _build(3)
    assert _size(uncut) <= ANSWER_CHAR_LIMIT

    assert _build(3) == uncut


@pytest.mark.req("R-INTERFACE-GUARDS.output-size")
async def test_the_tool_answer_on_the_wire_stays_within_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def gather_one(name: str, query: str, limit: int) -> tuple[SourceOutcome, list[MarketOffer]]:
        # The sole fake: the gather. Ranking, the cut and the MCP serialization are real.
        return SourceOutcome(source=name, status="ok"), _offers(name, limit)

    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    monkeypatch.setattr(server, "SOURCES", {name: SimpleNamespace() for name in SOURCES})
    monkeypatch.setattr(server, "_run_source", gather_one)

    async with Client(server.mcp) as client:
        reply = await client.call_tool("compare_prices", {"query": "электрочайник", "per_source_limit": 20})

    data = reply.structured_content
    assert data["total_offers"] == len(SOURCES) * 20
    assert 0 < len(data["offers"]) < data["total_offers"]
    assert _size(data) <= ANSWER_CHAR_LIMIT
