"""Offline tests for the Yandex price paths: the money parser and the price roles.

Requirement-derived (owner study 2026-11-02, defects R01 and R02):

* **R01 — one money parser.** Every money field of the search and card paths
  goes through the shared safe parser. An ambiguous amount ('1 999 2 999') is
  refused and becomes ``None`` WITH a diagnostic warning; it never becomes the
  fabricated 19992999.0. Ordinary groupings ('3 983') and fractional prices
  ('1 999,50') stay real prices.
* **R02 — the price role travels with the price.** The everyday price of a
  card comes from the explicit regular price and from nothing else. A card
  that carries only the subscriber (Yandex Plus) amount has an UNKNOWN
  everyday price: it is never borrowed from the Plus role, because a
  comparison would then certify a price most buyers cannot get.

The pages below are synthetic but shaped like the captured markup — a
first-screen ``data-zone-data`` snippet and a ``data-apiary`` product patch —
so the public tools meet the same shapes they meet live.
"""

from __future__ import annotations

import html
import json

from yandex_connector import server


def zone_page(price: str) -> str:
    """A first-screen SERP snippet carrying one offer, shaped like the capture."""
    item = {
        "type": "offer",
        "oskuId": "123456",
        "marketSku": "sku1",
        "title": "ACME X100",
        "price": price,
        "isAvailable": True,
        "rating": {"rating": 4.8, "gradesCount": 10},
    }
    data = html.escape(json.dumps(item, ensure_ascii=False), quote=True)
    return '<html><div data-zone-name="productSnippet" data-zone-data="' + data + '"></div></html>'


def card_page(regular: float | None = None, main: float = 900) -> str:
    """A product page whose price block carries the main (subscriber) price.

    ``regular`` adds the explicit everyday price the card ships in
    ``oldPrices``; without it the card names no everyday price at all.
    """
    price: dict[str, object] = {
        "mainPrice": {"price": {"value": main, "currency": "RUR"}},
        "oldPrices": [],
    }
    if regular is not None:
        price["oldPrices"] = [{"type": "regular", "price": {"value": regular, "currency": "RUR"}}]
    collections = {
        "price": {"p": price},
        "titleV2": {"t": {"title": "ACME X100"}},
        "allPrices": {"a": {"baobab": {"productId": "123456", "skuId": "sku1"}}},
        "productServiceSnippets": {
            "s": {"productSnippet": {"productPayload": {"rating": {"ratingValue": 4.8, "ratingCount": 10}}}}
        },
    }
    return '<html><noframes data-apiary="patch">' + json.dumps({"collections": collections}) + "</noframes></html>"


def _serve(monkeypatch, page: str) -> None:
    """Answer every page read with ``page``."""
    from yandex_connector import server as yandex_server

    async def fake_fetch(url: str, label: str, ctx=None) -> str:
        return page

    monkeypatch.setattr(yandex_server, "_fetch_html", fake_fetch)


# ------------------------------------------------------------------- R01 ------


async def test_search_refuses_an_ambiguous_price_and_names_it(monkeypatch):
    """R01: '1 999 2 999' is two numbers glued by the grouping space.

    The local coercion stripped the spaces and float()ed the rest, so the row
    quoted 19992999.0 — a price nobody charges — and the answer looked
    healthy. The shared parser refuses the blob: the price is null and the
    warning names the text that was refused.
    """
    _serve(monkeypatch, zone_page("1 999 2 999"))

    result = await server.yandex_search(query="ACME X100")

    assert result.returned == 1
    assert result.items[0].price_rub is None
    assert result.meta.healthy is False
    refused = [w for w in result.meta.warnings if w.startswith("price_unparseable")]
    assert refused, result.meta.warnings
    assert "1 999 2 999" in refused[0]


async def test_search_keeps_ordinary_groupings_and_fractional_prices(monkeypatch):
    """R01 positive control: the stricter parser must not eat real prices."""
    _serve(monkeypatch, zone_page("3 983"))

    result = await server.yandex_search(query="ACME X100")

    assert result.items[0].price_rub == 3983.0
    assert result.meta.healthy is True
    assert not [w for w in result.meta.warnings if w.startswith("price_unparseable")]


async def test_search_keeps_a_fractional_price(monkeypatch):
    """R01 positive control: a decimal price is a real price."""
    _serve(monkeypatch, zone_page("1 999,50"))

    result = await server.yandex_search(query="ACME X100")

    assert result.items[0].price_rub == 1999.5
    assert result.meta.healthy is True


async def test_card_refuses_an_ambiguous_regular_price(monkeypatch):
    """R01 on the card path: the regular price is money too."""
    collections = {
        "price": {
            "p": {
                "mainPrice": {"price": {"value": 900, "currency": "RUR"}},
                "oldPrices": [{"type": "regular", "price": {"value": "1 999 2 999", "currency": "RUR"}}],
            }
        },
        "titleV2": {"t": {"title": "ACME X100"}},
        "allPrices": {"a": {"baobab": {"productId": "123456", "skuId": "sku1"}}},
    }
    page = '<html><noframes data-apiary="patch">' + json.dumps({"collections": collections}) + "</noframes></html>"
    _serve(monkeypatch, page)

    card = await server.yandex_card(product_id="123456", include_reviews=False)

    assert card.price_rub is None
    assert card.price_with_plus == 900.0
    refused = [w for w in card.meta.warnings if w.startswith("price_unparseable")]
    assert refused, card.meta.warnings
    assert "1 999 2 999" in refused[0]


# ------------------------------------------------------------------- R02 ------


async def test_card_never_borrows_the_plus_price_as_the_everyday_price(monkeypatch):
    """R02: a card with only the subscriber price has no everyday price.

    The parser used to fall back from the Plus amount to ``price_rub``, so the
    card claimed a 900 rouble everyday price and a comparison certified it.
    The role is unknown now, and an unknown role stays unknown.
    """
    _serve(monkeypatch, card_page())

    card = await server.yandex_card(product_id="123456", include_reviews=False)

    assert card.price_with_plus == 900.0
    assert card.price_rub is None
    assert card.meta.healthy is False
    assert any(w.startswith("no_everyday_price") for w in card.meta.warnings), card.meta.warnings


async def test_card_with_an_explicit_regular_price_keeps_it(monkeypatch):
    """R02 positive control: the explicit everyday price still comes through."""
    _serve(monkeypatch, card_page(regular=1200))

    card = await server.yandex_card(product_id="123456", include_reviews=False)

    assert card.price_rub == 1200.0
    assert card.price_with_plus == 900.0
    assert card.meta.healthy is True
    assert card.meta.warnings == []
