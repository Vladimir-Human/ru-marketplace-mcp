"""R-ITEM-HONESTY wiring tests for the Detsky Mir connector tools.

HEAD fe7efcc routed mcp_core.resilience.validate_offer into detmir_card and
validate_items into detmir_category, so per-offer validation now reaches every
connector tool and flips meta.healthy when it fires. These tests drive the
public tool entries with _fetch_json monkeypatched (the offline style of
test_server.py) and pin the two drift alarms plus the guard that a clean page
stays healthy.
"""

from __future__ import annotations

import pytest
from detmir_connector import server

# ---------------------------------------------------------------- fixtures ----

# A category page whose tiles lost every field the parser reads: no title/name
# (DetmirProduct.title defaults ""), and none of the price shapes _parse_product
# tries (price, prices.sale/current, final_price), so price_rub is None.
TITLELESS_PAGE = {
    "items": [
        {
            "id": 4100001,
            "availability": "AVAILABLE",
            "link": {"web_url": "https://www.detmir.ru/product/index/id/4100001/"},
        },
        {
            "id": 4100002,
            "availability": "AVAILABLE",
        },
    ],
    "meta": {"length": 2, "title": "Пупсы", "limit": 20, "offset": 0},
}

# A card with a title but no price and an off-scale rating. DetmirProduct.rating
# is parsed as coerce_price, so a raw 7.9 survives as 7.9 — exactly the value
# validate_offer's coerce_rating rejects.
OFF_SCALE_CARD = {
    "item": {
        "id": "6673570",
        "title": "Кукла пупс Demi Star в синем комбинезоне",
        "rating": 7.9,
        "review_count": 19,
        "brands": [{"id": "112", "title": "Demi Star"}],
        "availability": "AVAILABLE",
        "link": {"web_url": "https://www.detmir.ru/product/index/id/6673570/"},
    }
}

# A normal, fully priced page: titles, prices and in-range ratings on every tile.
PRICED_PAGE = {
    "items": [
        {
            "id": 4100003,
            "title": "Кукла пупс Demi Star высота 35 см",
            "price": {"price": 1999, "currency": "RUB"},
            "rating": 4.8,
            "review_count": 196,
            "availability": "AVAILABLE",
            "link": {"web_url": "https://www.detmir.ru/product/index/id/4100003/"},
        },
        {
            "id": 4100004,
            "title": "Кукла пупс CRY BABIES Кэти",
            "price": {"price": 4999, "currency": "RUB"},
            "rating": 4.4,
            "review_count": 12,
            "availability": "AVAILABLE",
        },
    ],
    "meta": {"length": 708, "title": "Пупсы", "limit": 20, "offset": 0},
}


@pytest.fixture(autouse=True)
def clear_cache():
    """Keep cached payloads from leaking between tests."""
    server._cache.clear()
    yield
    server._cache.clear()


@pytest.fixture
def no_delay(monkeypatch):
    """Remove the politeness gap so tests do not actually wait."""
    monkeypatch.setattr(server._limiter, "min_gap_s", 0.0)


def stub_json(monkeypatch, mapping: dict[str, object]):
    """Route ``_fetch_json`` to canned payloads, matched by URL substring."""

    async def fake_fetch(url: str, label: str, ctx=None):
        for needle, payload in mapping.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(server, "_fetch_json", fake_fetch)


# ------------------------------------------------------------------- tests ----


async def test_category_flags_products_that_lost_their_prices_and_titles(monkeypatch, no_delay):
    """A page of titleless, priceless tiles is drift, not a healthy listing.

    On the pre-HEAD detmir_category this page passed as healthy: products was
    non-empty, so the lone ``if not products:`` warning never fired and the meta
    line read ``MetaOut(source="detmir_category", healthy=not warnings,
    warnings=warnings)`` — warnings == [] and healthy == True. The wiring's
    validate_items now aggregates the per-item invariants, so both the healthy
    flag and the title_missing assertion below fail on the pre-HEAD code.
    """
    stub_json(monkeypatch, {"/v4/products": TITLELESS_PAGE})

    result = await server.detmir_category(alias="pups")

    assert result.returned == 2
    assert result.meta.healthy is False
    assert any(w.startswith("title_missing: 2/2") for w in result.meta.warnings), result.meta.warnings
    assert result.items[0].price_rub is None


async def test_card_flags_an_off_scale_rating_alongside_the_missing_price(monkeypatch, no_delay):
    """A price-drifted card already warned no_price before HEAD; the wiring's new
    voice here is the off-scale rating invariant.

    On the pre-HEAD card the warnings list held only no_price — there was no
    validate_offer call — so the rating_out_of_range assertion fails on the
    pre-HEAD code while healthy is already False for the missing price.
    """
    stub_json(monkeypatch, {"/v2/products/6673570": OFF_SCALE_CARD})

    result = await server.detmir_card(product_id=6673570)

    assert result.meta.healthy is False
    assert any(w.startswith("rating_out_of_range") for w in result.meta.warnings), result.meta.warnings
    assert any(w.startswith("no_price") for w in result.meta.warnings), result.meta.warnings


async def test_category_a_priced_page_with_titles_stays_healthy(monkeypatch, no_delay):
    """The invariants are guards, not tripwires: a normal page stays healthy.

    Titles, prices and in-range ratings on every tile must leave warnings empty,
    so detmir_category keeps answering healthy=True for the good payload it has
    always served.
    """
    stub_json(monkeypatch, {"/v4/products": PRICED_PAGE})

    result = await server.detmir_category(alias="pups")

    assert result.meta.healthy is True
    assert not any(w.startswith("title_missing") for w in result.meta.warnings)
    assert not any(w.startswith("rating_out_of_range") for w in result.meta.warnings)
    assert result.items[0].price_rub is not None


async def test_category_flags_a_page_whose_every_price_died(monkeypatch, no_delay):
    """Titled products with no price: the price selector drifted.

    Fails on the code before the absent-price wiring: titled products raise no
    per-item code, so warnings stayed empty and healthy True.
    """
    page = {
        **TITLELESS_PAGE,
        "items": [{**item, "title": f"Пупс {n}"} for n, item in enumerate(TITLELESS_PAGE["items"], 1)],
    }
    stub_json(monkeypatch, {"/v4/products": page})

    result = await server.detmir_category(alias="pups")

    assert result.items[0].title
    assert result.meta.healthy is False
    assert "no_prices_on_page" in result.meta.warnings
