"""Avito sale discounts: price_rub is what the buyer pays, the struck price and percent ride alongside.

Fixtures are trimmed, de-identified captures of 2026-11-06 taken signed out
(provenance sidecars): ``js_items_sale.json`` holds one hit on Avito's sale
and one without a discount, ``item_sale_9000000041.html`` the item page of the
discounted hit. Ids, titles, URLs and prices are fictional; the percent and
Avito's number formatting are as served. Contradictions are built in code from
those captures.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from avito_connector import item_state, server

FIXTURES = Path(__file__).parent / "fixtures"
SALE_HIT, PLAIN_HIT, SALE_CARD = 9000000042, 9000000043, 9000000041


def _payload() -> dict:
    return json.loads((FIXTURES / "js_items_sale.json").read_text(encoding="utf-8"))


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache._data.clear()
    yield
    server._cache._data.clear()


def _rows(payload: dict) -> dict[int, dict]:
    rows, _ = server._parse_search_items(payload)
    return {row["item_id"]: row for row in rows}


def _sale_step(payload: dict, component: str) -> dict:
    hit = next(h for h in payload["catalog"]["items"] if h["id"] == SALE_HIT)
    return next(s["payload"] for s in hit["iva"]["PriceStep"] if s["componentData"]["component"] == component)


def test_a_discounted_hit_is_priced_at_what_the_buyer_pays():
    """v2.9.0 read priceDetailed.value, the price before Avito's sale discount."""
    rows = _rows(_payload())

    assert rows[SALE_HIT]["price_rub"] == 2550
    assert rows[SALE_HIT]["old_price_rub"] == 3000
    assert rows[SALE_HIT]["discount_percent"] == 15


def test_a_hit_without_a_discount_is_unchanged():
    row = _rows(_payload())[PLAIN_HIT]

    assert row["price_rub"] == 1800
    assert row["old_price_rub"] is None
    assert row["discount_percent"] is None


def _percent_off(payload):
    _sale_step(payload, "discount")["percent"] = 40


def _struck_price_off(payload):
    _sale_step(payload, "discount")["old_string"] = "3 500"


def _shown_price_above(payload):
    _sale_step(payload, "price")["priceDetailed"]["value"] = 3100


def _shown_price_without_discount(payload):
    hit = next(h for h in payload["catalog"]["items"] if h["id"] == SALE_HIT)
    hit["iva"]["PriceStep"] = [s for s in hit["iva"]["PriceStep"] if s["componentData"]["component"] != "discount"]


@pytest.mark.parametrize("edit", [_percent_off, _struck_price_off, _shown_price_above, _shown_price_without_discount])
def test_a_discount_that_does_not_add_up_gives_no_price(edit):
    payload = copy.deepcopy(_payload())
    edit(payload)

    row = _rows(payload)[SALE_HIT]

    assert (row["price_rub"], row["old_price_rub"], row["discount_percent"]) == (None, None, None)
    assert row["price_conflict"] is True


@pytest.mark.parametrize("value", [True, "15", 0, 100, None, [15]])
def test_a_percent_that_is_not_a_number_is_computed_from_the_prices(value):
    payload = copy.deepcopy(_payload())
    _sale_step(payload, "discount")["percent"] = value

    sale, conflict = item_state.search_sale_price(next(h for h in payload["catalog"]["items"] if h["id"] == SALE_HIT))

    assert conflict is None
    assert sale == {"price_rub": 2550, "old_price_rub": 3000, "discount_percent": 15}


def _serve_search(monkeypatch, payload: dict) -> None:
    async def fake_fetch(url, ctx):
        return 200, json.dumps(payload, ensure_ascii=False), "http"

    monkeypatch.setattr(server, "_fetch", fake_fetch)


async def test_search_reports_the_sale_and_warns_about_a_discount_that_does_not_add_up(monkeypatch):
    payload = copy.deepcopy(_payload())
    _percent_off(payload)
    _serve_search(monkeypatch, payload)

    result = await server.avito_search("робот пылесос")

    by_id = {item.item_id: item for item in result.items}
    assert by_id[SALE_HIT].price_rub is None
    assert by_id[PLAIN_HIT].price_rub == 1800
    assert any(w.startswith("price_conflict: 1 hit(s)") for w in result.meta.warnings)
    assert "price_conflict" not in by_id[PLAIN_HIT].model_dump()


def _serve_page(monkeypatch, html: str) -> None:
    async def fake_fetch_item_page(url, ctx):
        return 200, html, "cdp"

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)


async def test_a_discounted_card_names_the_struck_price_and_the_percent(monkeypatch):
    """v2.9.0 showed the sale price from the DOM but said nothing of the sale."""
    _serve_page(monkeypatch, _page(f"item_sale_{SALE_CARD}.html"))

    result = await server.avito_card(str(SALE_CARD))

    assert (result.price_rub, result.old_price_rub, result.discount_percent) == (2550, 3000, 15)
    assert not any(w.startswith("price_conflict") for w in result.meta.warnings)


async def test_a_card_without_a_discount_is_unchanged(monkeypatch):
    _serve_page(monkeypatch, _page("item_state_active_9000000012.html"))

    result = await server.avito_card("9000000012")

    assert result.price_rub == 2790
    assert (result.old_price_rub, result.discount_percent) == (None, None)


def _card_state_edited(edit) -> str:
    html = _page(f"item_sale_{SALE_CARD}.html")
    match = re.search(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)', html)
    assert match
    state = json.loads(json.loads('"' + match.group(1) + '"'))
    edit(state["loaderData"]["catalog-or-main-or-item"]["buyerItem"]["item"])
    encoded = json.dumps(json.dumps(state, ensure_ascii=False), ensure_ascii=False)[1:-1]
    return html[: match.start(1)] + encoded + html[match.end(1) :]


async def test_a_card_discount_that_does_not_add_up_gives_no_price_and_says_so(monkeypatch):
    def struck_price_off(item):
        item["formattedPrice"]["oldString"] = "3 500"

    _serve_page(monkeypatch, _card_state_edited(struck_price_off))

    result = await server.avito_card(str(SALE_CARD))

    assert (result.price_rub, result.old_price_rub, result.discount_percent) == (None, None, None)
    assert any(w.startswith("price_conflict: struck price 3500") for w in result.meta.warnings)


async def test_a_page_price_that_is_neither_card_price_is_a_conflict(monkeypatch):
    html = _page(f"item_sale_{SALE_CARD}.html").replace("2 550<span>", "2 100<span>", 1)
    assert "2 100<span>" in html
    _serve_page(monkeypatch, html)

    result = await server.avito_card(str(SALE_CARD))

    assert result.price_rub is None
    assert any("page price 2100" in w for w in result.meta.warnings)


def test_a_product_offer_at_the_struck_price_takes_the_sale_price():
    """A schema.org offer may carry the listing price; the card state says what the buyer pays."""
    item = {"price_rub": 3000.0}

    warnings = server._apply_sale(item, {"price_rub": 2550, "old_price_rub": 3000, "discount_percent": 15}, None)

    assert warnings == []
    assert item == {"price_rub": 2550.0, "old_price_rub": 3000.0, "discount_percent": 15}


def test_a_card_percent_missing_is_computed_and_a_shown_price_without_discount_is_a_conflict():
    def no_percent(item):
        del item["formattedPrice"]["saleDiscount"]

    def no_discount(item):
        del item["formattedPrice"]["saleDiscount"]
        del item["formattedPrice"]["oldString"]

    assert item_state.card_sale_price(_card_state_edited(no_percent), SALE_CARD) == (
        {"price_rub": 2550, "old_price_rub": 3000, "discount_percent": 15},
        None,
    )
    sale, conflict = item_state.card_sale_price(_card_state_edited(no_discount), SALE_CARD)
    assert sale is None
    assert conflict == "shown price 2550 differs from the listing price 3000 without a discount"


def test_the_sale_of_another_item_is_not_read():
    assert item_state.card_sale_price(_page(f"item_sale_{SALE_CARD}.html"), 9000000099) == (None, None)
