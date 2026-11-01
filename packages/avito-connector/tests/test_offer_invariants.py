"""Offer-invariant wiring tests for the Avito connector (R-ITEM-HONESTY).

R-ITEM-HONESTY: per-offer validation (mcp_core.resilience.validate_items /
validate_offer) must reach every connector tool, and any warning it raises
must flip meta.healthy. HEAD fe7efcc wired both into avito_search and
avito_card; before it only wb and ozon called them, so an Avito page whose
tiles lost their titles answered healthy=true with an empty warnings list.

Every test goes through the public tool entry with the upstream monkeypatched,
mirroring the stubbing style of test_server.py: server._fetch returns
(200, json.dumps(payload), "curl_cffi") for search, server._fetch_item_page
returns (200, html, "cdp") for cards, and an autouse fixture clears
server._cache.
"""

from __future__ import annotations

import json

import pytest
from avito_connector import server

# ---------------------------------------------------------------- fixtures ----

# Every parser-critical key stays PRESENT with a null value, so the id/title/
# price shape families survive and shape_drift stays quiet: only the new
# validate_items call can speak about this page.
TITLELESS_PAYLOAD = {
    "totalCount": 2,
    "items": [
        {"id": 1, "title": None, "price": None, "uri": "/moskva/x/x_1", "location": "Москва"},
        {"id": 2, "title": None, "price": None, "uri": "/moskva/y/y_2", "location": "Москва"},
    ],
}

PRICED_PAYLOAD = {
    "totalCount": 2,
    "items": [
        {
            "id": 4612345678,
            "title": "Ноутбук ThinkPad X1 Carbon",
            "price": 45500,
            "uri": "/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
            "location": "Москва",
        },
        {
            "id": 4612349999,
            "title": "Ноутбук ThinkPad T14",
            "price": 38900,
            "uri": "/moskva/noutbuki/noutbuk_thinkpad_t14_4612349999",
            "location": "Москва",
        },
    ],
}

# The card page keeps a valid RUB offer but lost its Product name: the page
# still parses (a Product with neither name nor RUB price raises
# parser_drift), so only validate_offer can flag the titleless result.
TITLELESS_CARD_HTML = (
    '<html><head><script type="application/ld+json">'
    + json.dumps(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "sku": "4612345678",
                    "description": "В хорошем состоянии, батарея держит 6 часов.",
                    "offers": {
                        "@type": "Offer",
                        "price": "45500",
                        "priceCurrency": "RUB",
                        "url": "https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
                    },
                }
            ],
        },
        ensure_ascii=False,
    )
    + "</script></head><body></body></html>"
)


def _ok(payload: dict):
    return 200, json.dumps(payload, ensure_ascii=False), "curl_cffi"


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """Every test starts with an empty cache: a cached body from a previous
    case would otherwise shadow its monkeypatched fetch."""
    server._cache._data.clear()


def _patch_fetch(monkeypatch, result):
    async def fake_fetch(url, ctx):
        return result

    monkeypatch.setattr(server, "_fetch", fake_fetch)


def _patch_item_page(monkeypatch, result):
    async def fake_fetch_item_page(url, ctx):
        return result

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)


# --------------------------------------------------------------- avito_search ----


async def test_search_flags_items_that_lost_their_titles_and_prices(monkeypatch):
    """Both tiles keep their title/price keys but as nulls, so the page still
    parses, no shape family dies and shape_drift stays quiet — only the new
    validate_items call sees the drift and flips healthy.

    Fails on the pre-HEAD code (fe7efcc~1): avito_search built `warnings`
    from empty_items_with_nonzero_total and shape_drift_warning alone;
    items_raw is non-empty and null members keep the id/title/price families
    alive, so ``R.attach_meta(result.model_dump(...), warnings,
    source="avito_search")`` received [] and meta.healthy stayed True — both
    the healthy and the title_missing asserts below failed there.
    ``items[0].price_rub is None`` only pins the payload; it passes either way.
    """
    _patch_fetch(monkeypatch, _ok(TITLELESS_PAYLOAD))

    result = await server.avito_search("ноутбук")

    assert result.meta.healthy is False
    assert any(w.startswith("title_missing: 2/2") for w in result.meta.warnings), result.meta.warnings
    assert result.items[0].price_rub is None


# ----------------------------------------------------------------- avito_card ----


async def test_card_warns_when_the_product_page_lost_its_title(monkeypatch):
    """The card's JSON-LD Product kept its RUB offer but lost its name, so
    parse_item_page succeeds and only validate_offer can flag the title.

    The title is the drifted field by necessity: a price-drifted Avito card
    (price None, title present) is silent in both the old and the new code —
    validate_offer has no absent-price code, and AvitoCardResponse carries no
    in_stock or rating to contradict a missing price.

    Fails on the pre-HEAD code (fe7efcc~1): avito_card closed with
    ``R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), [],
    source="avito_card")`` — a literal empty warnings list — so meta.healthy
    stayed True and title_missing never reached the response. Both asserts
    below failed there.
    """
    _patch_item_page(monkeypatch, (200, TITLELESS_CARD_HTML, "cdp"))

    result = await server.avito_card("4612345678")

    assert result.meta.healthy is False
    assert any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings


# ------------------------------------------------------------------- guard ----


async def test_search_a_priced_page_with_titles_stays_healthy(monkeypatch):
    """Guard against crying wolf: two titled, priced tiles must keep the
    per-item wiring silent, so the wiring cannot turn an ordinary healthy
    Avito page into a drift alarm."""
    _patch_fetch(monkeypatch, _ok(PRICED_PAYLOAD))

    result = await server.avito_search("ноутбук")

    assert result.meta.healthy is True
    assert not any(w.startswith("title_missing") for w in result.meta.warnings)
    assert not any("no_prices" in w for w in result.meta.warnings)
    assert result.items[0].price_rub == 45500


async def test_search_flags_a_page_whose_every_price_died(monkeypatch):
    """Titles intact, every price gone: the price selector drifted, not the market.

    Fails on the code before the absent-price wiring: titled tiles raise no
    per-item code, so warnings stayed empty and healthy True.
    """
    payload = {
        "totalCount": 2,
        "items": [{**item, "price": None} for item in PRICED_PAYLOAD["items"]],
    }
    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_search("ноутбук")

    assert result.meta.healthy is False
    assert "no_prices_on_page" in result.meta.warnings
