"""Wiring tests for the R-ITEM-HONESTY offer validation in citilink tools.

validate_items / validate_offer have their own unit tests in mcp-core; these
prove the citilink tool entries actually call them on the data path, through
the public tools with CDP rendering monkeypatched out (same idiom as
test_server.py). A regression that unwires the helpers keeps those unit tests
green and fails only here.
"""

from __future__ import annotations

import pytest
from citilink_connector import server

# A page where every tile kept its shape keys (price_text present but blank,
# title present but None — a null member keeps the shape family alive) while
# losing both the price and the title text. The pre-existing shape_drift
# check stays quiet on purpose, so the only signal under test is the wiring.
DRIFTED_SEARCH_EXTRACTED = {
    "title": "ноутбук — Citilink",
    "items": [
        {
            "product_id": "noutbuk-lenovo-2169270",
            "title": None,
            "price_meta": None,
            "price_text": "",
            "old_price_text": None,
            "price_texts": {"attached": [], "other": []},
            "url": "",
        },
        {
            "product_id": "noutbuk-hp-2169271",
            "title": None,
            "price_meta": None,
            "price_text": "",
            "old_price_text": None,
            "price_texts": {"attached": [], "other": []},
            "url": "",
        },
    ],
}

HEALTHY_SEARCH_EXTRACTED = {
    "title": "ноутбук — Citilink",
    "items": [
        {
            "product_id": "noutbuk-lenovo-2169270",
            "title": "Ноутбук Lenovo IdeaPad 3",
            "price_meta": "52999",
            "price_text": "52 999₽",
            "old_price_text": "62 999",
            "price_texts": {"attached": ["52 999₽"], "other": ["62 999"]},
            "url": "https://www.citilink.ru/product/noutbuk-lenovo-2169270/",
        },
        {
            "product_id": "noutbuk-hp-2169271",
            "title": "Ноутбук без цены",
            "price_meta": None,
            "price_text": None,
            "old_price_text": None,
            "price_texts": {"attached": [], "other": []},
            "url": "",
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


# ------------------------------------------------------------------ citilink_search ----


async def test_search_flags_tiles_that_lost_their_prices_and_titles(monkeypatch):
    """A page where every tile lost both its price and its title is systemic
    selector drift, and validate_items must say so in meta.warnings.

    On the pre-wiring code (HEAD~1) citilink_search only had the hand-rolled
    guard `if all(it.price_rub is None for it in items): warnings.append(
    "no_prices_on_page")` — identical for this all-priceless page, so
    no_prices_on_page and healthy=False were already correct there. Only the
    title_missing assertion below is new: the old code could not see that the
    tiles also lost their titles.
    """
    _patch_render(monkeypatch, DRIFTED_SEARCH_EXTRACTED)

    result = await server.citilink_search("ноутбук")

    assert result.count == 2
    assert result.items[0].price_rub is None
    assert result.meta.healthy is False
    assert "no_prices_on_page" in result.meta.warnings
    assert any(w.startswith("title_missing: 2/2") for w in result.meta.warnings), result.meta.warnings


# -------------------------------------------------------------------- citilink_card ----


async def test_card_warns_when_the_product_lost_its_title(monkeypatch):
    """A card with a price but no title is not parser_drift (that raise needs
    title AND price both None), so the loss must surface as a warning.

    On the pre-wiring code (HEAD~1) the price kept card_warnings empty, so the
    card was attached with `attach_meta(card, card_warnings, ...)` — warnings
    [] and healthy True. Both assertions below fail there.
    """
    payload = {
        "title": None,
        "price_meta": "59990",
        "price_text": None,
        "old_price_text": None,
        "price_texts": {"attached": [], "other": []},
        "is_available": True,
        "page_title": "Ноутбук — Citilink",
    }
    _patch_render(monkeypatch, payload)

    result = await server.citilink_card("https://www.citilink.ru/product/noutbuk-lenovo-123456/")

    assert result.price_rub == 59990.0
    assert result.meta.healthy is False
    assert any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings


# ------------------------------------------------------------------ false positives ----


async def test_search_a_priced_page_with_titles_stays_healthy(monkeypatch):
    """Guard: the wiring must not cry wolf. A page where the tiles kept their
    prices and titles (one priceless tile among priced ones is not systemic)
    must stay healthy with neither warning raised."""
    _patch_render(monkeypatch, HEALTHY_SEARCH_EXTRACTED)

    result = await server.citilink_search("ноутбук")

    assert result.meta.healthy is True
    assert "no_prices_on_page" not in result.meta.warnings
    assert not any(w.startswith("title_missing") for w in result.meta.warnings)
