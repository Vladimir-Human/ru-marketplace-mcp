"""Offline regressions for R-COMPARE-CONDITIONAL-PRICE through compare_prices."""
# START_MODULE_CONTRACT
# PURPOSE: Prove conditional amounts cannot win and remain visible to the agent.
# SCOPE: Native response stubs and a marked normalized offer; no network or browser.
# DEPENDS: compare_connector.server/models_output; native WB/Yandex/Citilink models; pytest.
# LINKS: M-COMPARE; R-COMPARE-CONDITIONAL-PRICE; R-NO-FABRICATION; R-OFFLINE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _stub_native_search: Replace a source tool while retaining the real comparison adapter.
# _stub_regular_rival: Supply a 950-RUB everyday competitor through its native model.
# test_wallet_only_price_cannot_win: Keep Wallet-only amounts out of both winners.
# test_regular_price_ranks_and_wallet_amount_is_warned: Rank regular prices and name the Wallet alternative.
# test_marked_conditional_price_has_no_winner: Apply the ranking guard to any marked condition.
# test_subscription_amount_is_named_without_ranking_it: Preserve Plus prices and warn with their amount.
# END_MODULE_MAP

from __future__ import annotations

from types import SimpleNamespace

import pytest
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from compare_connector.models_output import MarketOffer
from wb_connector.models_output import WbCardItem, WbSearchResponse
from yandex_connector.models_output import YandexProduct, YandexSearchResponse


# START_BLOCK_CONDITIONAL_PRICE_TESTS
def _stub_native_search(monkeypatch, source, tool, response):
    async def search(**kwargs):
        return response

    monkeypatch.setitem(server.SOURCES, source, SimpleNamespace(**{tool: search}))


def _stub_regular_rival(monkeypatch):
    _stub_native_search(
        monkeypatch,
        "citilink",
        "citilink_search",
        CitilinkSearchResponse(items=[CitilinkSearchItemOut(product_id="2", title="Kettle", price_rub=950)]),
    )


@pytest.mark.parametrize("wallet_field", [None, 900])
async def test_wallet_only_price_cannot_win(monkeypatch, wallet_field):
    """R-COMPARE-CONDITIONAL-PRICE: old code crowns the 900-RUB Wallet row, not the 950-RUB rival."""
    _stub_native_search(
        monkeypatch,
        "wildberries",
        "wb_search",
        WbSearchResponse(
            items=[
                WbCardItem(
                    nm_id=1,
                    name="Kettle",
                    price_rub=900,
                    price_kind="wallet",
                    wallet_price_rub=wallet_field,
                    price_original_rub=1200,  # Struck/reference price is not a regular fallback.
                )
            ]
        ),
    )
    _stub_regular_rival(monkeypatch)

    result = await server.compare_prices("Kettle", sources=["wildberries", "citilink"])

    assert result.cheapest.source == "citilink"
    assert result.cheapest_comparable.source == "citilink"
    assert result.cheapest.price_rub == 950
    assert result.price_spread_rub is None
    assert result.complete is True and result.total_offers == 2
    assert [offer.source for offer in result.offers] == ["citilink", "wildberries"]
    wallet = result.model_dump(mode="json")["offers"][1]
    assert wallet["price_kind"] == "wallet"
    assert wallet["price_rub"] is None
    assert wallet["price_native"] == 900
    assert any("wildberries/1" in w and "900 RUB" in w and "WB Wallet" in w for w in result.warnings)


@pytest.mark.parametrize("regular_price", [940, 1000])
async def test_regular_price_ranks_and_wallet_amount_is_warned(monkeypatch, regular_price):
    """R-COMPARE-CONDITIONAL-PRICE: old code ranks correctly here but loses the 900-RUB Wallet warning."""
    _stub_native_search(
        monkeypatch,
        "wildberries",
        "wb_search",
        WbSearchResponse(
            items=[
                WbCardItem(nm_id=1, name="Kettle", price_rub=regular_price, price_kind="regular", wallet_price_rub=900)
            ]
        ),
    )
    _stub_regular_rival(monkeypatch)

    result = await server.compare_prices("Kettle", sources=["wildberries", "citilink"])

    assert any("900 RUB" in w and "WB Wallet" in w for w in result.warnings)
    winner = "wildberries" if regular_price < 950 else "citilink"
    assert result.cheapest.source == winner
    assert result.cheapest_comparable.source == winner
    assert result.cheapest.price_rub == min(regular_price, 950)
    assert result.price_spread_rub == abs(regular_price - 950)
    regular = next(offer for offer in result.offers if offer.source == "wildberries")
    assert regular.price_kind == "regular"
    assert regular.price_rub == regular.price_native == regular_price


@pytest.mark.parametrize("kind", ["wallet", "subscription", "coupon", "membership"])
async def test_marked_conditional_price_has_no_winner(monkeypatch, kind):
    """R-COMPARE-CONDITIONAL-PRICE: old code drops the kind and selects 900 even with no everyday price.

    The normalized stub deliberately supplies a marked price in price_rub to
    prove the ranking guard is independent of WB's adapter, not a source-name rule.
    """

    async def search(query, limit):
        return [MarketOffer(source="citilink", product_id="1", title="Kettle", price_rub=900, price_kind=kind)]

    monkeypatch.setattr(server, "_SEARCH_IMPLS", {"citilink": search})
    monkeypatch.setattr(server, "SOURCES", {"citilink": object()})
    result = await server.compare_prices("Kettle", sources=["citilink"])

    assert result.cheapest is None
    assert result.cheapest_comparable is None
    assert result.price_spread_rub is None
    assert result.complete is True and result.total_offers == 1
    assert result.offers[0].price_kind == kind
    assert result.offers[0].price_native == 900
    assert any("no_prices:" in w for w in result.warnings)
    assert any("conditional_price:" in w and "900 RUB" in w and "not ranked" in w for w in result.warnings)


@pytest.mark.parametrize("regular_price", [940, 1000, None])
async def test_subscription_amount_is_named_without_ranking_it(monkeypatch, regular_price):
    """R-COMPARE-CONDITIONAL-PRICE: old code keeps Plus unranked but emits no warning naming its 900 RUB."""
    _stub_native_search(
        monkeypatch,
        "yandex_market",
        "yandex_search",
        YandexSearchResponse(
            items=[YandexProduct(product_id="1", title="Kettle", price_rub=regular_price, price_with_plus=900)]
        ),
    )
    _stub_regular_rival(monkeypatch)

    result = await server.compare_prices("Kettle", sources=["yandex_market", "citilink"])

    named = [w for w in result.warnings if "yandex_market/1" in w and "900 RUB" in w and "Yandex Plus" in w]
    # Plus-only: the warning is the agent's only sign the 900 is not ranked. Beside a
    # ranked everyday price the amount is already in price_with_subscription_rub.
    assert len(named) == (1 if regular_price is None else 0)
    winner = "yandex_market" if regular_price is not None and regular_price < 950 else "citilink"
    assert result.cheapest.source == winner
    assert result.cheapest_comparable.source == winner
    yandex = next(offer for offer in result.offers if offer.source == "yandex_market")
    assert yandex.price_rub == regular_price
    assert yandex.price_with_subscription_rub == 900
    assert yandex.price_native == (regular_price if regular_price is not None else 900)
    assert yandex.price_kind == ("regular" if regular_price is not None else "subscription")


# END_BLOCK_CONDITIONAL_PRICE_TESTS
