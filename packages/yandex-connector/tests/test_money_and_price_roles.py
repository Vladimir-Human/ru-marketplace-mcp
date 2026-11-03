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

import pytest
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
    # The card did carry an everyday price, so it is not a Plus-only card.
    assert not [w for w in card.meta.warnings if w.startswith(("no_everyday_price", "no_price"))]


def _card_with_reference(without_discount: str) -> str:
    """A card whose everyday (1200) and Plus (900) prices are fine and whose
    pre-discount reference is ``without_discount``."""
    collections = {
        "price": {
            "p": {
                "mainPrice": {"price": {"value": 900, "currency": "RUR"}},
                "oldPrices": [
                    {"type": "regular", "price": {"value": 1200, "currency": "RUR"}},
                    {"type": "withoutDiscount", "price": {"value": without_discount, "currency": "RUR"}},
                ],
            }
        },
        "titleV2": {"t": {"title": "ACME X100"}},
        "allPrices": {"a": {"baobab": {"productId": "123456", "skuId": "sku1"}}},
    }
    return '<html><noframes data-apiary="patch">' + json.dumps({"collections": collections}) + "</noframes></html>"


def _reference_refusals(warnings: list[str]) -> list[str]:
    return [w for w in warnings if w.startswith("price_unparseable") and "reference" in w]


async def test_card_reports_an_ambiguous_struck_through_price(monkeypatch):
    """R01: the pre-discount reference is money too, and refusing it is never quiet.

    With good everyday and Plus prices, a glued withoutDiscount figure used to
    become null with no warning, so a broken field looked like an absent one.
    """
    _serve(monkeypatch, _card_with_reference("1 999 2 999"))

    card = await server.yandex_card(product_id="123456", include_reviews=False)

    assert card.price_rub == 1200.0
    assert card.price_before_discount_rub is None
    refused = _reference_refusals(card.meta.warnings)
    assert refused, card.meta.warnings
    assert "1 999 2 999" in refused[0]


async def test_card_keeps_a_readable_struck_through_price(monkeypatch):
    """R01 positive control: a readable reference is kept and draws no warning."""
    _serve(monkeypatch, _card_with_reference("1 499"))

    card = await server.yandex_card(product_id="123456", include_reviews=False)

    assert card.price_before_discount_rub == 1499.0
    assert not [w for w in card.meta.warnings if w.startswith("price_unparseable")]


async def test_zone_search_reports_an_ambiguous_struck_through_price(monkeypatch):
    """R01 on the first-screen snippet: with a withDiscount entry the base
    price is the struck-through reference, and its refusal is reported."""
    item = {
        "type": "offer",
        "oskuId": "123456",
        "marketSku": "sku1",
        "title": "ACME X100",
        "price": "1 999 2 999",
        "additionalPrices": [{"priceType": "withDiscount", "priceValue": "900"}],
        "isAvailable": True,
        "rating": {"rating": 4.8, "gradesCount": 10},
    }
    data = html.escape(json.dumps(item, ensure_ascii=False), quote=True)
    _serve(monkeypatch, '<html><div data-zone-name="productSnippet" data-zone-data="' + data + '"></div></html>')

    result = await server.yandex_search(query="ACME X100")

    assert result.items[0].price_rub == 900.0
    assert result.items[0].price_old_rub is None
    refused = _reference_refusals(result.meta.warnings)
    assert refused, result.meta.warnings
    assert "1 999 2 999" in refused[0]


async def test_ssr_search_reports_an_ambiguous_struck_through_price(monkeypatch):
    """R01 on the SSR state: an unreadable oldPrice ends null with a warning."""
    collections = {
        "productShowPlace": {"sp1": {"id": "sp1", "productId": 123456, "productSnippetId": "s1"}},
        "productSnippet": {
            "s1": {
                "productPayload": {
                    "title": {"value": "ACME X100"},
                    "price": {
                        "actualPrice": {"amount": {"intPart": "900"}},
                        "oldPrice": {"amount": {"intPart": "1 999 2 999"}},
                    },
                },
                "baobabPayload": {"additionalPrices": [{"priceType": "withDiscount", "priceValue": "900"}]},
            }
        },
        "product": {"123456": {"titles": {"raw": "ACME X100"}}},
    }
    # The search widget ships its bundle under a per-render path key.
    patch = {"widgets": {"@search/Results": {"/content/results": {"options": {"collections": collections}}}}}
    page = '<html><noframes data-apiary="patch">' + json.dumps(patch) + "</noframes></html>"
    _serve(monkeypatch, page)

    result = await server.yandex_search(query="ACME X100")

    assert result.items[0].price_rub == 900.0
    assert result.items[0].price_old_rub is None
    refused = _reference_refusals(result.meta.warnings)
    assert refused, result.meta.warnings
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


# START_BLOCK_PREPUBLICATION_PREFERRED_PRICE


def _preferred_discount_page(extraction: str, discount_entries: list[dict]) -> str:
    """Native zone/SSR offers with a 1499 reference and an independent Plus price."""
    additional = [*discount_entries, {"priceType": "yaBank", "priceValue": 500}]
    if extraction == "zone":
        item = {
            "type": "offer",
            "oskuId": "123456",
            "marketSku": "sku1",
            "title": "ACME X100",
            "price": 1499,
            "additionalPrices": additional,
            "isAvailable": True,
            "rating": {"rating": 4.8, "gradesCount": 10},
        }
        data = html.escape(json.dumps(item, ensure_ascii=False), quote=True)
        return '<html><div data-zone-name="productSnippet" data-zone-data="' + data + '"></div></html>'
    assert extraction == "ssr"
    collections = {
        "visibleSearchResult": {"v": {"text": "ACME X100", "total": 1}},
        "productShowPlace": {
            "sp": {"id": "sp", "productId": 123456, "productSnippetId": "s", "defaultOfferShowPlaceId": "op"}
        },
        "offerShowPlace": {"op": {"offerId": "o"}},
        "offer": {"o": {"skuId": "sku1", "price": {"value": 1499, "currency": "RUR"}, "delivery": {"inStock": True}}},
        "product": {"123456": {"titles": {"raw": "ACME X100"}}},
        "productSnippet": {
            "s": {
                "productPayload": {
                    "title": {"value": "ACME X100"},
                    "price": {"actualPrice": {"amount": {"intPart": "500"}}},
                    "rating": {"ratingValue": 4.8, "ratingCount": 10},
                },
                "baobabPayload": {"price": 1499, "additionalPrices": additional},
            }
        },
    }
    # No cart price: withDiscount is the preferred everyday-price candidate.
    patch = {"widgets": {"w": {"/path": {"options": {"collections": collections}}}}}
    return '<html><noframes data-apiary="patch">' + json.dumps(patch) + "</noframes></html>"


async def _public_native_and_comparison(monkeypatch, page: str):
    """Use both public MCP entries and the actual Yandex comparison adapter."""
    from compare_connector import server as compare_server
    from fastmcp import Client
    from mcp_core.cache import TTLCache

    async def native_get(client, url, **kwargs):
        assert "/search?" in url
        return 200, page

    monkeypatch.setattr(server, "get_text_with_retries", native_get)
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=300))
    monkeypatch.setattr(server._limiter, "min_gap_s", 0.0)
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    # Registry wiring uses the real module, never a fake source or adapter.
    monkeypatch.setitem(compare_server.SOURCES, "yandex_market", server)
    async with Client(server.mcp) as client:
        native = (await client.call_tool("yandex_search", {"query": "ACME X100"})).structured_content
    async with Client(compare_server.mcp) as client:
        comparison = (
            await client.call_tool("compare_prices", {"query": "ACME X100", "sources": ["yandex_market"]})
        ).structured_content
    return native, comparison


@pytest.mark.parametrize("extraction", ["zone", "ssr"])
@pytest.mark.parametrize(
    "discount",
    [
        pytest.param(False, id="false"),
        pytest.param(0, id="zero"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(True, id="true-type-poison"),
        pytest.param({"value": 900}, id="object-type-poison"),
        pytest.param([900], id="array-type-poison"),
    ],
)
async def test_native_preferred_discount_is_refused_without_reference_fallback(monkeypatch, extraction, discount):
    """R-YANDEX-MONEY-PARSING/R-YANDEX-PRICE-ROLE (owner, 2026-11-03).

    A present invalid preferred amount is null WITH a refusal warning. Neither
    the native public answer nor the real compare adapter may promote its
    otherwise-readable reference/base price into a certified everyday price.
    """
    page = _preferred_discount_page(extraction, [{"priceType": "withDiscount", "priceValue": discount}])
    native, comparison = await _public_native_and_comparison(monkeypatch, page)
    meta = native.get("meta", native.get("_meta", {}))

    assert native["returned"] == 1
    assert native["items"][0]["price_rub"] is None
    assert native["items"][0]["price_with_plus"] == 500.0
    assert meta["healthy"] is False
    assert any(w.startswith("price_unparseable") and "everyday" in w for w in meta["warnings"])
    assert comparison["total_offers"] == 1
    assert comparison["offers"][0]["price_rub"] is None
    assert comparison["cheapest"] is None
    assert comparison["cheapest_comparable"] is None
    assert any("source_warning:yandex_market:" in w and "price_unparseable" in w for w in comparison["warnings"])


@pytest.mark.parametrize("extraction", ["zone", "ssr"])
@pytest.mark.parametrize(
    "discount_entries,expected",
    [
        pytest.param([], 1499.0, id="discount-absent"),
        pytest.param([{"priceType": "withDiscount"}], 1499.0, id="value-absent"),
        pytest.param([{"priceType": "withDiscount", "priceValue": None}], 1499.0, id="optional-null"),
        pytest.param([{"priceType": "withDiscount", "priceValue": " \t"}], 1499.0, id="optional-blank"),
        pytest.param([{"priceType": "withDiscount", "priceValue": 900}], 900.0, id="valid-numeric"),
        pytest.param([{"priceType": "withDiscount", "priceValue": "899,50"}], 899.5, id="valid-fractional"),
    ],
)
async def test_native_optional_or_valid_discount_still_quotes_real_prices(
    monkeypatch, extraction, discount_entries, expected
):
    """R-YANDEX-MONEY-PARSING: absent/blank optional values and valid money survive."""
    native, comparison = await _public_native_and_comparison(
        monkeypatch, _preferred_discount_page(extraction, discount_entries)
    )
    meta = native.get("meta", native.get("_meta", {}))

    assert native["items"][0]["price_rub"] == expected
    assert native["items"][0]["price_with_plus"] == 500.0
    assert meta["healthy"] is True
    assert not any(w.startswith("price_unparseable") for w in meta["warnings"])
    assert comparison["total_offers"] == 1
    assert comparison["cheapest"]["price_rub"] == expected
    assert comparison["cheapest_comparable"]["price_rub"] == expected
    assert not any("price_unparseable" in w for w in comparison["warnings"])


# END_BLOCK_PREPUBLICATION_PREFERRED_PRICE
