"""An Ozon search tile price is the Ozon Card price; compare ranks Ozon by one card's everyday price.

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-CONDITIONAL-PRICE.ozon-card: every Ozon tile is conditional-only, the card of the
         cheapest tile worth it is read once within the source's timeout, and that offer ranks by the card's
         everyday price; a failed, slow or priceless card leaves every tile unranked and says why.
SCOPE: The pure candidate rule and the compare Ozon adapter through stubbed ozon_search/ozon_card.
DEPENDS: compare_connector.decide/server; ozon_connector.models_output; pytest.
LINKS: M-COMPARE; M-COMPARE-DECIDE; R-COMPARE-CONDITIONAL-PRICE; audit cycle 1 finding F-C1 (2026-11-17).
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from compare_connector import server
from compare_connector.decide import pick_card_candidate
from fastmcp.exceptions import ToolError
from ozon_connector.models_output import OzonCardResponse, OzonSearchItemOut, OzonSearchResponse

# Audit cycle 1 (2026-11-17), query "iphone 16 128gb": a tile without the CARD_PRICE
# badge showed 64 532 ₽; the card of the same sku showed the everyday 68 093 ₽ and
# the Ozon Card price 64 532 ₽. Amounts only; titles and the other skus are made up.
AUDIT_SKU = "1980467719"


def _tiles() -> OzonSearchResponse:
    return OzonSearchResponse(
        items=[
            OzonSearchItemOut(sku=111, title="Чехол для iPhone 16", price=990.0),
            OzonSearchItemOut(sku=AUDIT_SKU, title="Apple iPhone 16 128GB", price=64532.0),
            OzonSearchItemOut(sku=333, title="Apple iPhone 16 128GB черный", price=65990.0),
        ]
    )


def _stub(monkeypatch: pytest.MonkeyPatch, card) -> list[str]:
    asked: list[str] = []

    async def ozon_search(**kwargs):
        return _tiles()

    async def ozon_card(sku_or_path: str):
        asked.append(sku_or_path)
        return await card(sku_or_path)

    monkeypatch.setitem(server.SOURCES, "ozon", SimpleNamespace(ozon_search=ozon_search, ozon_card=ozon_card))
    return asked


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
def test_the_card_read_goes_to_the_cheapest_tile_that_could_be_the_product() -> None:
    tiles = [
        ("1", "Чехол для iPhone 16", 990.0, None),  # an unrequested accessory
        ("2", "Apple iPhone 16 128GB", 60000.0, False),  # marked out of stock
        ("3", "Apple iPhone 16 128GB", None, None),  # no amount to rank
        ("", "Apple iPhone 16 128GB", 50000.0, None),  # no id to read
        ("4", "Apple iPhone 16 128GB", 64532.0, None),
        ("5", "Apple iPhone 16 128GB", 65990.0, True),
    ]
    assert pick_card_candidate("iphone 16 128gb", tiles) == "4"
    assert pick_card_candidate("iphone 16 128gb", tiles[:4]) is None


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
async def test_a_plain_ozon_tile_is_conditional_and_the_cheapest_ranks_by_its_cards_everyday_price(monkeypatch):
    async def card(sku: str) -> OzonCardResponse:
        return OzonCardResponse(price=68093.0, card_price=64532.0)

    asked = _stub(monkeypatch, card)

    result = await server.compare_prices("iphone 16 128gb", sources=["ozon"])

    assert asked == [AUDIT_SKU]
    offers = {offer.product_id: offer for offer in result.offers}
    ranked = offers[AUDIT_SKU]
    assert (ranked.price_rub, ranked.price_native, ranked.price_kind) == (68093.0, 68093.0, None)
    for other in ("111", "333"):
        assert offers[other].price_rub is None
        assert offers[other].price_kind == "card"
    assert offers["333"].price_native == 65990.0
    assert result.cheapest is not None and result.cheapest.product_id == AUDIT_SKU
    assert result.cheapest.price_rub == 68093.0
    assert f"info:conditional_price_available: ozon/{AUDIT_SKU}: 64532 RUB with card" in result.warnings
    assert "conditional_price: ozon/333: 65990 RUB requires Ozon card; not ranked" in result.warnings
    assert not [w for w in result.warnings if "ozon_card_unread" in w]


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
async def test_one_card_ranks_one_tile_even_when_its_sku_repeats(monkeypatch):
    async def ozon_search(**kwargs):
        tile = OzonSearchItemOut(sku=AUDIT_SKU, title="Apple iPhone 16 128GB", price=64532.0)
        return OzonSearchResponse(items=[tile, tile.model_copy()])

    async def ozon_card(sku_or_path: str) -> OzonCardResponse:
        return OzonCardResponse(price=68093.0, card_price=64532.0)

    monkeypatch.setitem(server.SOURCES, "ozon", SimpleNamespace(ozon_search=ozon_search, ozon_card=ozon_card))

    offers = await server._search_ozon("iphone 16 128gb", 5)

    assert [offer.price_rub for offer in offers] == [68093.0, None]
    assert [offer.price_kind for offer in offers] == [None, "card"]


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
async def test_a_refused_card_leaves_every_ozon_tile_unranked_and_says_why(monkeypatch):
    async def card(sku: str) -> OzonCardResponse:
        raise ToolError(json.dumps({"error": "rate_limited", "message": "slow down"}))

    _stub(monkeypatch, card)

    result = await server.compare_prices("iphone 16 128gb", sources=["ozon"])

    assert all(offer.price_rub is None for offer in result.offers)
    assert result.cheapest is None
    assert result.source_outcomes[0].status == "ok"
    outcome_warnings = result.source_outcomes[0].warnings
    assert any(w.startswith(f"ozon_card_unread: ozon/{AUDIT_SKU}: rate_limited;") for w in outcome_warnings)
    assert f"source_warning:ozon: {outcome_warnings[-1]}" in result.warnings
    assert f"conditional_price: ozon/{AUDIT_SKU}: 64532 RUB requires Ozon card; not ranked" in result.warnings


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
async def test_a_card_without_an_everyday_price_ranks_nothing(monkeypatch):
    async def card(sku: str) -> OzonCardResponse:
        return OzonCardResponse(price=None, card_price=64532.0)

    _stub(monkeypatch, card)

    result = await server.compare_prices("iphone 16 128gb", sources=["ozon"])

    assert result.cheapest is None
    assert any("ozon_card_unread" in w and "no everyday price" in w for w in result.warnings)


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
async def test_a_slow_card_never_costs_the_search_results(monkeypatch):
    async def card(sku: str) -> OzonCardResponse:
        await asyncio.sleep(30)
        return OzonCardResponse(price=68093.0)

    _stub(monkeypatch, card)
    monkeypatch.setattr(server, "SOURCE_TIMEOUT_S", 1.5)
    monkeypatch.setattr(server, "OZON_CARD_MARGIN_S", 0.5)
    monkeypatch.setattr(server, "OZON_CARD_MIN_S", 0.2)

    result = await server.compare_prices("iphone 16 128gb", sources=["ozon"])

    assert result.source_outcomes[0].status == "ok"
    assert len(result.offers) == 3
    assert result.cheapest is None
    assert any("ozon_card_unread" in w and "no answer within" in w for w in result.warnings)


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.ozon-card")
async def test_no_card_read_starts_when_the_source_timeout_is_nearly_spent(monkeypatch):
    async def card(sku: str) -> OzonCardResponse:
        return OzonCardResponse(price=68093.0)

    asked = _stub(monkeypatch, card)
    monkeypatch.setattr(server, "SOURCE_TIMEOUT_S", 4.0)

    result = await server.compare_prices("iphone 16 128gb", sources=["ozon"])

    assert asked == []
    assert result.cheapest is None
    assert any("ozon_card_unread" in w and "nearly spent" in w for w in result.warnings)
