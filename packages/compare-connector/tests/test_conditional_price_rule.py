"""Offline regressions for the one conditional-price rule (R-COMPARE-CONDITIONAL-PRICE).

The rule is one decision shared by every source: an everyday price beside a
*lower* conditional one ranks by the everyday price and says that the cheaper
amount exists; a conditional price alone is not an everyday price, so the offer
does not rank and the amount is named as a real warning.

The helper is pure, so it is tested on its own. The adapters are tested through
the fake-transport pattern the rest of this package uses: the native search tool
is replaced, the real comparison adapter reads it.
"""
# START_MODULE_CONTRACT
# PURPOSE: Prove the shared conditional-price rule: an everyday price ranks, a conditional-only price never wins.
# SCOPE: The pure helper, the WB/Yandex/Ozon adapters through stubbed native searches, and build_response.
# DEPENDS: compare_connector.decide/models_output/server; native WB/Yandex/Ozon models; pytest.
# LINKS: M-COMPARE; M-COMPARE-DECIDE; R-COMPARE-CONDITIONAL-PRICE; R-NO-FABRICATION; R-OFFLINE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _stub_native_search: Replace a source tool while keeping the real comparison adapter.
# _r0_ozon_search_tile: The R0 Ozon pair's search payload, shaped like the committed capture.
# _captured_ozon_tile: One tile of the committed Ozon capture.
# test_the_helper_ranks_by_the_everyday_price: An everyday price beside a lower conditional amount.
# test_the_helper_refuses_to_rank_a_conditional_price_alone: A conditional amount alone never ranks.
# test_the_helper_says_nothing_about_a_conditional_price_that_is_not_lower: An equal or higher amount.
# test_the_helper_says_nothing_without_any_price: Missing amounts stay missing.
# test_wb_ranks_by_the_everyday_price_and_names_the_wallet_amount: The WB adapter.
# test_yandex_ranks_by_the_everyday_price_and_names_the_plus_amount: The Yandex adapter.
# test_ozon_card_price_tiles_are_conditional_only_and_never_rank: The Ozon adapter and a CARD_PRICE tile.
# test_build_response_ranks_by_the_everyday_price_and_never_the_conditional_one: The comparison's own ranking.
# END_MODULE_MAP

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from compare_connector import server
from compare_connector.decide import ResponseOptions, build_response, decide_conditional_price
from compare_connector.models_output import MarketOffer, SourceOutcome
from mcp_core import resilience as R
from ozon_connector import server as ozon
from ozon_connector.models_output import OzonSearchResponse
from wb_connector.models_output import WbCardItem, WbSearchResponse
from yandex_connector.models_output import YandexProduct, YandexSearchResponse

# The Ozon capture the Ozon connector parses (2026-08-07, provenance beside it).
OZON_CAPTURE = Path(__file__).resolve().parents[2] / "ozon-connector/tests/fixtures/search_composer_live.json"


# START_BLOCK_CONDITIONAL_PRICE_RULE_TESTS
def _stub_native_search(monkeypatch, source, tool, response):
    async def search(**kwargs):
        return response

    monkeypatch.setitem(server.SOURCES, source, SimpleNamespace(**{tool: search}))


def _r0_ozon_search_tile() -> dict:
    """The R0 Ozon pair's search tile: the Ozon Card price the tile shows.

    The shape is the committed capture's (a ``priceV2`` atom with a ``price``
    list and a ``priceStyle``); the amounts are the R0 pair's: the search
    reported 65 367 ₽ with a struck 117 982 ₽, and the card for the same sku
    reported the everyday 68 967 ₽ and the card price 65 367 ₽. So the search
    tile's price is the Ozon Card price — the conditional one.
    """
    return {
        "sku": 1688765035,
        "mainState": [
            {"type": "textAtom", "textAtom": {"text": "Kettle"}},
            {
                "type": "priceV2",
                "priceV2": {
                    "price": [
                        {"text": "65 367 ₽", "textStyle": "PRICE"},
                        {"text": "117 982 ₽", "textStyle": "ORIGINAL_PRICE"},
                    ],
                    "priceStyle": {"styleType": "CARD_PRICE"},
                },
            },
        ],
    }


def _captured_ozon_tile(sku: str) -> dict:
    """One tile of the committed Ozon capture, exactly as Ozon served it."""
    payload = json.loads(OZON_CAPTURE.read_text(encoding="utf-8"))
    grid = next(value for key, value in payload["widgetStates"].items() if key.startswith("tileGridDesktop-"))
    for tile in json.loads(grid)["items"]:
        if str(tile.get("sku")) == sku:
            return tile
    raise AssertionError(f"the capture holds no tile {sku}")


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.rule")
def test_the_helper_ranks_by_the_everyday_price():
    """R-COMPARE-CONDITIONAL-PRICE.rule: the everyday price ranks; the cheaper amount is named."""
    decision = decide_conditional_price("wildberries", "1", 940.0, 900.0, "wallet")

    assert decision.rank_price_rub == 940.0
    assert decision.note == "info:conditional_price_available: wildberries/1: 900 RUB with wallet"


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.rule")
def test_the_helper_refuses_to_rank_a_conditional_price_alone():
    """R-COMPARE-CONDITIONAL-PRICE.rule: a conditional amount alone is not an everyday price."""
    decision = decide_conditional_price("yandex_market", "1", None, 900.0, "subscription")

    assert decision.rank_price_rub is None
    assert decision.note == ("conditional_price: yandex_market/1: 900 RUB requires paying with Yandex Pay; not ranked")


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.rule")
def test_the_helper_says_nothing_about_a_conditional_price_that_is_not_lower():
    """R-COMPARE-CONDITIONAL-PRICE.rule: an equal or higher conditional amount is no bargain."""
    equal = decide_conditional_price("wildberries", "1", 900.0, 900.0, "wallet")
    higher = decide_conditional_price("wildberries", "1", 900.0, 950.0, "wallet")

    assert (equal.rank_price_rub, equal.note) == (900.0, None)
    assert (higher.rank_price_rub, higher.note) == (900.0, None)


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.rule")
def test_the_helper_says_nothing_without_any_price():
    """R-COMPARE-CONDITIONAL-PRICE.rule: a missing price stays missing and invents nothing."""
    decision = decide_conditional_price("ozon", "1", None, None, "card")

    assert decision.rank_price_rub is None
    assert decision.note is None


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.adapters")
async def test_wb_ranks_by_the_everyday_price_and_names_the_wallet_amount(monkeypatch):
    """R-COMPARE-CONDITIONAL-PRICE.adapters: the WB Wallet amount does not become the rank price."""
    _stub_native_search(
        monkeypatch,
        "wildberries",
        "wb_search",
        WbSearchResponse(
            items=[
                WbCardItem(
                    nm_id=1,
                    name="Kettle",
                    price_rub=940.0,
                    price_kind="regular",
                    wallet_price_rub=900.0,
                )
            ]
        ),
    )

    result = await server.compare_prices("Kettle", sources=["wildberries"])

    offer = result.offers[0]
    assert offer.price_rub == 940.0
    assert offer.price_native == 940.0
    assert offer.price_kind == "regular"
    assert result.cheapest is not None and result.cheapest.price_rub == 940.0
    assert result.cheapest_comparable is not None and result.cheapest_comparable.price_rub == 940.0
    note = "info:conditional_price_available: wildberries/1: 900 RUB with wallet"
    assert note in result.warnings
    # A note, not a doubt: the source's own info: prefix survives the comparison.
    assert note not in R.health_warnings(result.warnings)


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.adapters")
async def test_yandex_ranks_by_the_everyday_price_and_names_the_plus_amount(monkeypatch):
    """R-COMPARE-CONDITIONAL-PRICE.adapters: the Yandex Plus amount does not become the rank price."""
    _stub_native_search(
        monkeypatch,
        "yandex_market",
        "yandex_search",
        YandexSearchResponse(
            items=[YandexProduct(product_id="1", title="Kettle", price_rub=940.0, price_with_plus=900.0)]
        ),
    )

    result = await server.compare_prices("Kettle", sources=["yandex_market"])

    offer = result.offers[0]
    assert offer.price_rub == 940.0
    assert offer.price_native == 940.0
    assert offer.price_kind == "regular"
    assert offer.price_with_subscription_rub == 900.0
    assert result.cheapest is not None and result.cheapest.price_rub == 940.0
    assert "info:conditional_price_available: yandex_market/1: 900 RUB with Yandex Pay" in result.warnings


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.adapters")
async def test_ozon_card_price_tiles_are_conditional_only_and_never_rank(monkeypatch):
    """R-COMPARE-CONDITIONAL-PRICE.adapters: an Ozon card price is never the everyday price.

    Two tiles carry ``priceStyle.styleType CARD_PRICE``: the capture's own tile
    2796072502 (32 296 ₽) and the R0 tile (65 367 ₽). Both amounts are the
    Ozon Card price, so both are conditional-only: no everyday price, no
    ranking, the amount in ``price_native`` and named once in the
    ``conditional_price`` warning.
    """
    native = OzonSearchResponse(
        items=[
            ozon._parse_search_tile(_captured_ozon_tile("2796072502")),
            ozon._parse_search_tile(_r0_ozon_search_tile()),
        ]
    )
    assert native.items[0].price == 32296.0
    assert native.items[0].price_kind == "card"
    _stub_native_search(monkeypatch, "ozon", "ozon_search", native)

    result = await server.compare_prices("Kettle", sources=["ozon"])

    offers = {offer.product_id: offer for offer in result.offers}
    captured = offers["2796072502"]
    r0 = offers["1688765035"]
    assert captured.price_rub is None
    assert captured.price_native == 32296.0
    assert captured.price_kind == "card"
    assert r0.price_rub is None
    assert r0.price_native == 65367.0
    assert r0.price_kind == "card"
    assert result.cheapest is None
    assert result.cheapest_comparable is None
    # One warning names the captured tile's amount, and the source's own note
    # says it once — the comparison never repeats it.
    assert len([w for w in result.warnings if "conditional_price: ozon/2796072502" in w]) == 1
    assert (
        len([w for w in result.source_outcomes[0].warnings if w.startswith("conditional_price: ozon/2796072502")]) == 1
    )


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE", "R-COMPARE-CONDITIONAL-PRICE.no-fabrication")
def test_build_response_ranks_by_the_everyday_price_and_never_the_conditional_one():
    """R-COMPARE-CONDITIONAL-PRICE.no-fabrication: the everyday price ranks, the conditional one never wins.

    The WB outcome already names the Wallet amount (the adapter's note), so the
    comparison's own warning must not say it a second time.
    """
    everyday = MarketOffer(
        source="yandex_market",
        product_id="1",
        title="Kettle",
        price_rub=940.0,
        price_kind="regular",
        price_with_subscription_rub=900.0,
    )
    conditional_only = MarketOffer(
        source="wildberries",
        product_id="2",
        title="Kettle",
        price_rub=None,
        price_native=900.0,
        price_kind="wallet",
    )
    wallet_note = "conditional_price: wildberries/2: 900 RUB requires WB Wallet; not ranked"

    response = build_response(
        "Kettle",
        [
            (
                SourceOutcome(source="yandex_market", status="ok", detail="1 results, 1 priced"),
                [everyday],
            ),
            (
                SourceOutcome(
                    source="wildberries",
                    status="ok",
                    detail="1 results, 0 priced",
                    warnings=[wallet_note],
                ),
                [conditional_only],
            ),
        ],
        ResponseOptions(active=["yandex_market", "wildberries"], missing=[]),
    )

    assert response.cheapest is not None and response.cheapest.price_rub == 940.0
    assert response.cheapest_comparable is not None and response.cheapest_comparable.price_rub == 940.0
    assert response.offers[0].price_rub == 940.0
    assert response.offers[1].price_rub is None
    assert response.offers[1].price_native == 900.0
    assert len([w for w in response.warnings if "conditional_price: wildberries/2" in w]) == 1


@pytest.mark.req("R-COMPARE-CONDITIONAL-PRICE.no-fabrication")
def test_an_adapter_conditional_price_warning_keeps_its_top_level_prefix():
    """v2.9.4 gave a wallet-only WB offer the top-level ``conditional_price: …`` warning the docs describe.

    The adapter now names it through the shared helper; the comparison must pass it through as is, not
    as ``source_warning:wildberries: conditional_price: …``.
    """
    note = "conditional_price: wildberries/2: 900 RUB requires WB Wallet; not ranked"
    offer = MarketOffer(
        source="wildberries", product_id="2", title="Kettle", price_rub=None, price_native=900.0, price_kind="wallet"
    )
    response = build_response(
        "Kettle",
        [(SourceOutcome(source="wildberries", status="ok", detail="1 results, 0 priced", warnings=[note]), [offer])],
        ResponseOptions(active=["wildberries"], missing=[]),
    )
    assert [w for w in response.warnings if "conditional_price" in w] == [note]


# END_BLOCK_CONDITIONAL_PRICE_RULE_TESTS
