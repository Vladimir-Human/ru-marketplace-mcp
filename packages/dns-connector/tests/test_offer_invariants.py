"""R-ITEM-HONESTY wiring tests for the DNS-Shop connector's public tools.

Commit fe7efcc routed ``mcp_core.resilience.validate_items`` (with
``absent_price="no_prices_on_page"``) into ``dns_search`` and
``validate_offer`` into ``dns_card``. These three tests prove that wiring
through the public tool entries, with CDP rendering monkeypatched out exactly
as in ``test_server.py`` — fake extracted payloads via ``_patch_render``, no
Chrome, no network — so each test feeds one drifted page and reads the tool's
own ``meta``.

The drifted payloads keep every parser-critical key PRESENT but null/blank:
``shape_drift_warning`` treats ``price:null`` as a page without a price, not a
page that lost its price field, so the pre-existing structural check stays
quiet and these tests isolate the item-honesty wiring.
"""

from __future__ import annotations

import pytest
from dns_connector import server

# Two tiles whose title and price nodes stopped matching. The extractor emits
# null for a node that vanished, and the keys stay present — the same trick
# test_server.py's SEARCH_EXTRACTED uses for its priceless tile.
TITLELESS_SEARCH = {
    "title": "ноутбук — DNS",
    "items": [
        {
            "product_id": "b7a1667f9b19ed20",
            "title": None,
            "price_text": None,
            "old_price_text": None,
            "availability_text": "В наличии",
            "url": "https://www.dns-shop.ru/product/b7a1667f9b19ed20/noutbuk/",
        },
        {
            "product_id": "b7a1667f9b19ed21",
            "title": None,
            "price_text": None,
            "old_price_text": None,
            "availability_text": "В наличии",
            "url": "https://www.dns-shop.ru/product/b7a1667f9b19ed21/noutbuk/",
        },
    ],
}

# The card keeps its price so the parser_drift guard (title AND price both
# None) stays quiet — a titleless card that still parses is exactly the silent
# case validate_offer exists for.
TITLELESS_CARD = {
    "title": None,
    "price_text": "59 990 ₽",
    "old_price_text": None,
    "availability_text": "В наличии",
    "is_available": True,
    "page_title": "Ноутбук — DNS",
}

# Every tile priced and titled: the shape test_server.py asserts healthy on,
# mirrored here with both tiles carrying a price.
PRICED_SEARCH = {
    "title": "ноутбук — DNS",
    "items": [
        {
            "product_id": "5f4dcc3b5aa764d61d8327de",
            "title": "Ноутбук Lenovo IdeaPad 3",
            "price_text": "52 999 ₽",
            "old_price_text": "62 999",
            "availability_text": "В наличии",
            "url": "https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/",
        },
        {
            "product_id": "5f4dcc3b5aa764d61d8327df",
            "title": "Ноутбук Acer Aspire 5",
            "price_text": "47 999 ₽",
            "old_price_text": None,
            "availability_text": "В наличии",
            "url": "https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327df/noutbuk-acer/",
        },
    ],
}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()


def _patch_render(monkeypatch, payload):
    async def fake_render(url, extract_js, wait_ms, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render", fake_render)


# ------------------------------------------------------------------ dns_search ----


async def test_search_flags_tiles_that_lost_their_prices_and_titles(monkeypatch):
    """R-ITEM-HONESTY: a page of titleless, priceless tiles answers loud.

    Both halves of the drift must reach the caller: every price alias dying
    at once is ``no_prices_on_page``, and half-or-more titleless tiles
    aggregate into ``title_missing: 2/2 items affected``. The tiles stay
    present, so this is not parser_drift — it is a parsed page that must say
    it parsed badly.

    On the pre-HEAD code only the first half existed — dns_search hand-rolled
    ``if items and all(it.price_rub is None for it in items):
    warnings.append("no_prices_on_page")`` — so this page answered with
    warnings == ["no_prices_on_page"] and healthy already False; only the
    ``title_missing`` assertion below failed there, because the titleless
    half was silent.
    """
    _patch_render(monkeypatch, TITLELESS_SEARCH)

    result = await server.dns_search("ноутбук")

    assert result.count == 2
    assert result.meta.healthy is False
    assert "no_prices_on_page" in result.meta.warnings, result.meta.warnings
    assert any(w.startswith("title_missing: 2/2") for w in result.meta.warnings), result.meta.warnings
    assert result.items[0].price_rub is None


# -------------------------------------------------------------------- dns_card ----


async def test_card_warns_when_the_product_lost_its_title(monkeypatch):
    """R-ITEM-HONESTY: a card that kept its price but lost its title is drift.

    On the pre-HEAD code dns_card attached meta with a hardcoded empty list —
    ``attach_meta(..., [], source="dns_card")`` — so this card answered with
    warnings == [] and healthy True: BOTH assertions below failed there. The
    raise only covers a card with neither title nor price (the
    parser_drift guard), so a titleless card that still parses was a silent
    empty success before validate_offer landed.
    """
    _patch_render(monkeypatch, TITLELESS_CARD)

    result = await server.dns_card("https://www.dns-shop.ru/product/b7a1667f9b19ed20/noutbuk/")

    assert result.meta.healthy is False
    assert any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings


# --------------------------------------------------------------------- guard ----


async def test_search_a_priced_page_with_titles_stays_healthy(monkeypatch):
    """R-ITEM-HONESTY guard: the wiring must not cry wolf.

    A page whose tiles kept both title and price answers healthy with no
    item-honesty warnings, so the drift alarms above cannot fire on ordinary
    data.
    """
    _patch_render(monkeypatch, PRICED_SEARCH)

    result = await server.dns_search("ноутбук")

    assert result.meta.healthy is True, result.meta.warnings
    assert "no_prices_on_page" not in result.meta.warnings
    assert not any(w.startswith("title_missing") for w in result.meta.warnings)
