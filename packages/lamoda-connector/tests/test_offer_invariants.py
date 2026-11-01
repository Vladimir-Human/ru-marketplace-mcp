"""Offline wiring tests for R-ITEM-HONESTY in the Lamoda connector.

fe7efcc routed lamoda_search through ``mcp_core.resilience.validate_items``
(``absent_price="no_prices_on_page"``) and lamoda_card through
``validate_offer``, so an offer that parses but breaks an invariant (a page
where no tile kept a price, a card that lost its title) now says so in
``meta.warnings`` and flips ``meta.healthy``. These tests prove the wiring
through the public tool entries only, with the transports monkeypatched out
exactly like test_server.py: search through the CDP render seam, cards
through the mocked GraphQL tier.
"""

from __future__ import annotations

import pytest
from lamoda_connector import server


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()
    # The pacer is real, and its default gap is seconds. An offline suite that
    # actually sleeps it turns a 3-second run into a 90-second one and teaches
    # everyone to distrust the timings.
    monkeypatch.setattr(server, "_min_gap", 0.0)
    server._pacer.reset()


def _patch_graphql(monkeypatch, product):
    async def fake_graphql(sku, ctx):
        return product

    monkeypatch.setattr(server, "_graphql_card", fake_graphql)


def _patch_render(monkeypatch, payload):
    async def fake_render(query, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render_search", fake_render)


# ------------------------------------------------------------- lamoda_search ----


async def test_search_flags_tiles_that_lost_their_prices_and_titles(monkeypatch):
    """A page where every tile lost both its price and its title is drift.

    The tiles keep their required shape keys (null ``title``, null
    ``price_rub``), so the pre-existing family check stays quiet and
    ``validate_items`` is the only alarm: ``no_prices_on_page`` plus a
    ``title_missing`` tally over the whole page. On the pre-change code
    (HEAD~1) the search path hand-rolled only ``if all(it.price_rub is None
    for it in items): warnings.append("no_prices_on_page")`` — identical for
    an all-priceless page — so the page still answered unhealthy with
    ``["no_prices_on_page"]`` and the ``title_missing`` assertion below is
    the one that fails there: a titleless grid read as merely priceless.
    """
    payload = {
        "title": "кроссовки — Lamoda",
        "items": [
            {
                "sku": "MP002XM1RMM3",
                "title": None,
                "brand": None,
                "price_rub": None,
                "old_price_rub": None,
                "url": "https://www.lamoda.ru/p/mp002xm1rmm3/",
            },
            {
                "sku": "MP002XM1RMM4",
                "title": None,
                "brand": None,
                "price_rub": None,
                "old_price_rub": None,
                "url": "",
            },
        ],
    }
    _patch_render(monkeypatch, payload)

    result = await server.lamoda_search("кроссовки")

    assert result.meta.healthy is False
    assert "no_prices_on_page" in result.meta.warnings
    assert any(w.startswith("title_missing: 2/2") for w in result.meta.warnings), result.meta.warnings
    assert result.items[0].price_rub is None


async def test_search_a_priced_page_with_titles_stays_healthy(monkeypatch):
    """The guard is one-way: a page where every tile kept its price and its
    title must pick up neither warning, and ``healthy`` must stay true."""
    payload = {
        "title": "кроссовки — Lamoda",
        "items": [
            {
                "sku": "MP002XM1RMM3",
                "title": "Кроссовки Nike Air Max",
                "brand": None,
                "price_rub": 12990.0,
                "old_price_rub": 16990.0,
                "url": "https://www.lamoda.ru/p/mp002xm1rmm3/",
            },
            {
                "sku": "MP002XM1RMM4",
                "title": "Кроссовки Adidas Runfalcon",
                "brand": None,
                "price_rub": 5990.0,
                "old_price_rub": None,
                "url": "https://www.lamoda.ru/p/mp002xm1rmm4/",
            },
        ],
    }
    _patch_render(monkeypatch, payload)

    result = await server.lamoda_search("кроссовки")

    assert result.meta.healthy is True
    assert "no_prices_on_page" not in result.meta.warnings
    assert not any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings


# -------------------------------------------------------------- lamoda_card ----


async def test_card_warns_when_the_product_lost_its_title(monkeypatch):
    """A card that kept its price but lost its name is drift, not silence.

    The card flow parses a nameless product without raising (the GraphQL
    envelope is valid, the price is present, sizes may be empty), so the only
    alarm is ``validate_offer``'s ``title_missing``. On the pre-change code
    (HEAD~1) the card attached an EMPTY warnings list — ``attach_meta(..., [],
    source="lamoda_card")`` — and answered healthy=true, so BOTH assertions
    below fail there.
    """
    product = {
        "sku": "MP002XM1RMM3",
        "brand_name": "Nike",
        "price_amount": 12990.0,
        "old_price": 16990.0,
        "is_available": True,
        "sizes": [],
    }
    _patch_graphql(monkeypatch, product)

    result = await server.lamoda_card("MP002XM1RMM3")

    assert result.meta.healthy is False
    assert any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings
