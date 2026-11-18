"""compare_prices does not rank an Ozon offer whose price leaves out a customs payment (GitHub issue #329).

START_MODULE_CONTRACT
PURPOSE: Prove R-OZON-CUSTOMS-DUTY.compare: the Ozon card read with a customs duty leaves that offer unranked, says
         the duty and the estimated total, and lets a domestic offer that is cheaper than the total win.
SCOPE: compare_prices over fake ozon/wb sources (the same fakes as test_search_offer_integrity.py).
DEPENDS: pytest; compare_connector.server; ozon_connector; wb_connector.
LINKS: M-COMPARE; M-OZON; R-OZON-CUSTOMS-DUTY.
END_MODULE_CONTRACT
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from compare_connector import server
from ozon_connector import server as ozon
from ozon_connector.models_output import MetaOut, OzonCardResponse, OzonSearchResponse
from wb_connector.models_output import WbSearchResponse

QUERY = "смартфон example x 256 гб"
TITLE = "Смартфон Example X 256 ГБ"


def _ozon_tile(sku: int, price: int):
    return ozon._parse_search_tile(
        {
            "sku": sku,
            "mainState": [
                {"type": "textAtom", "textAtom": {"text": TITLE}},
                {"type": "priceV2", "priceV2": {"price": [{"text": f"{price} ₽"}]}},
            ],
        }
    )


def _sources(card: OzonCardResponse) -> dict:
    async def ozon_search(query):
        return OzonSearchResponse(items=[_ozon_tile(1980467719, 64503)])

    async def ozon_card(sku_or_path):
        return card

    async def wb_search(query, **kwargs):
        return WbSearchResponse.model_validate(
            {"items": [{"nm_id": 77, "name": TITLE, "brand": "Example", "price_rub": 72000.0, "in_stock": True}]}
        )

    return {
        "ozon": SimpleNamespace(ozon_search=ozon_search, ozon_card=ozon_card),
        "wildberries": SimpleNamespace(wb_search=wb_search),
    }


async def _compare(monkeypatch: pytest.MonkeyPatch, card: OzonCardResponse):
    monkeypatch.setattr(server, "SOURCES", _sources(card))
    return await server.compare_prices(query=QUERY, sources=["ozon", "wildberries"], per_source_limit=5)


@pytest.mark.req("R-OZON-CUSTOMS-DUTY")
async def test_an_offer_with_a_customs_duty_is_not_ranked_and_says_the_total(monkeypatch) -> None:
    card = OzonCardResponse(
        price=68070.0,
        card_price=64503.0,
        is_available=True,
        customs_duty_rub=8167.0,
        _meta=MetaOut(warnings=["customs_duty: ~8 167 ₽ customs payment due on delivery, not in price"]),
    )

    result = await _compare(monkeypatch, card)

    ozon_rows = [offer for offer in result.offers if offer.source == "ozon"]
    assert [offer.price_rub for offer in ozon_rows] == [None]
    assert result.cheapest is not None and result.cheapest.source == "wildberries"
    # Source warnings reach the agent as "source_warning:ozon: <warning>".
    [note] = [warning for warning in result.warnings if "customs_duty:" in warning]
    assert "ozon/1980467719" in note and "8 167" in note and "76 237" in note


@pytest.mark.req("R-OZON-CUSTOMS-DUTY")
async def test_a_duty_of_unknown_amount_also_leaves_the_offer_unranked(monkeypatch) -> None:
    card = OzonCardResponse(
        price=68070.0,
        is_available=True,
        _meta=MetaOut(warnings=["customs_duty: a customs payment is due on delivery, amount not stated"]),
    )

    result = await _compare(monkeypatch, card)

    assert [offer.price_rub for offer in result.offers if offer.source == "ozon"] == [None]
    assert any("customs_duty:" in warning and "amount not stated" in warning for warning in result.warnings)


@pytest.mark.req("R-OZON-CUSTOMS-DUTY")
async def test_a_domestic_card_still_ranks_by_its_everyday_price(monkeypatch) -> None:
    result = await _compare(monkeypatch, OzonCardResponse(price=68070.0, card_price=64503.0, is_available=True))

    assert [offer.price_rub for offer in result.offers if offer.source == "ozon"] == [68070.0]
    assert result.cheapest is not None and result.cheapest.source == "ozon"
    assert not any("customs_duty:" in warning for warning in result.warnings)
