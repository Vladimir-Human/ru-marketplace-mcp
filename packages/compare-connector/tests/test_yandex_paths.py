"""Offline tests for the Yandex paths of the comparison connector.

Requirement-derived (owner study 2026-11-02, defects R01, R02 and R04). The
Yandex connector itself is exercised here — only its upstream page is
synthetic — so the comparison sees the same answers the connector's own tools
produce:

* **R01 — one money parser.** An ambiguous amount ('1 999 2 999') never
  reaches the comparison as a price: the offer carries no price at all, and
  the reason rides along on the response.
* **R02 — the price role travels with the price.** ``compare_verify_offer``
  must not certify a subscriber (Yandex Plus) amount as the everyday price;
  an explicit regular price is compared and its delta reported.
* **R04 — an honest empty answer is healthy.** The page's own "nothing found"
  banner makes the source an ok, complete answer; a damaged empty answer
  stays unhealthy and the comparison stays incomplete.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from compare_connector import server as compare_server
from yandex_connector import server as yandex_server

FIXTURES = Path(__file__).parents[2] / "yandex-connector/tests/fixtures"


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
    """A product page whose price block carries the main (subscriber) price."""
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


def _serve_yandex(monkeypatch, page: str) -> None:
    """Point the comparison at the real Yandex connector, serving ``page``."""

    async def fake_fetch(url: str, label: str, ctx=None) -> str:
        return page

    monkeypatch.setattr(yandex_server, "_fetch_html", fake_fetch)
    monkeypatch.setattr(compare_server, "SOURCES", {"yandex_market": yandex_server})


# ------------------------------------------------------------------- R01 ------


async def test_the_adapter_never_quotes_an_ambiguous_yandex_price(monkeypatch):
    """R01: '1 999 2 999' is not a price, and the comparison must not carry it.

    The connector used to hand the comparison a fabricated 19992999.0, which
    then won every ranking as the cheapest offer.
    """
    _serve_yandex(monkeypatch, zone_page("1 999 2 999"))

    result = await compare_server.compare_prices(query="ACME X100", sources=["yandex_market"])

    offer = result.offers[0]
    assert offer.price_rub is None
    assert offer.price_native is None
    assert offer.price_kind is None
    assert result.cheapest is None
    assert any("price_unparseable" in warning for warning in result.warnings), result.warnings


async def test_the_adapter_keeps_a_real_yandex_price(monkeypatch):
    """R01 positive control: an ordinary grouped price still ranks."""
    _serve_yandex(monkeypatch, zone_page("3 983"))

    result = await compare_server.compare_prices(query="ACME X100", sources=["yandex_market"])

    assert result.offers[0].price_rub == 3983.0
    assert result.cheapest is not None
    assert result.cheapest.price_rub == 3983.0


# ------------------------------------------------------------------- R02 ------


async def test_verify_offer_refuses_to_certify_a_plus_only_card(monkeypatch):
    """R02: a card with only the subscriber price certifies nothing.

    The verification used to compare the Plus amount with the expected price
    and report a match, which is a certification of a price most buyers
    cannot get.
    """
    _serve_yandex(monkeypatch, card_page())

    result = await compare_server.compare_verify_offer("yandex_market", "123456", expected_price_rub=900)

    verification = result["price_verification"]
    assert verification["matches"] is None
    assert verification["observed_price_rub"] is None
    assert verification["delta_rub"] is None
    assert verification["price_kind"] == "subscription"


async def test_verify_offer_measures_the_delta_against_the_explicit_regular_price(monkeypatch):
    """R02 positive control: an explicit everyday price is compared, and the
    subscriber amount is never the answer."""
    _serve_yandex(monkeypatch, card_page(regular=1200))

    same = await compare_server.compare_verify_offer("yandex_market", "123456", expected_price_rub=1200)
    assert same["price_verification"]["observed_price_rub"] == 1200.0
    assert same["price_verification"]["delta_rub"] == 0.0
    assert same["price_verification"]["matches"] is True

    # The expected price was the subscriber amount: the everyday price differs
    # by exactly that difference, and the comparison does not confirm it.
    plus = await compare_server.compare_verify_offer("yandex_market", "123456", expected_price_rub=900)
    assert plus["price_verification"]["observed_price_rub"] == 1200.0
    assert plus["price_verification"]["delta_rub"] == 300.0
    assert plus["price_verification"]["matches"] is False


# ------------------------------------------------------------------- R04 ------


async def test_an_explicit_empty_result_is_a_healthy_source(monkeypatch):
    """R04: the page's own "nothing found" banner is an honest answer.

    The connector reported it as a health warning, the comparison read that
    as "this source contributed nothing broken" and answered unhealthy with
    complete=false — a source that answered perfectly.
    """
    empty_page = (FIXTURES / "search_empty.html").read_text(encoding="utf-8")
    _serve_yandex(monkeypatch, empty_page)

    result = await compare_server.compare_prices(query="nothing-matches", sources=["yandex_market"])

    outcome = result.source_outcomes[0]
    assert outcome.status == "ok"
    assert result.complete is True
    assert result.sources_ok == ["yandex_market"]
    assert result.total_offers == 0
    assert result.cheapest is None
    assert not any(warning.startswith("unhealthy_empty") for warning in outcome.warnings), outcome.warnings


async def test_a_damaged_empty_answer_stays_unhealthy(monkeypatch):
    """R04: a page that is neither products nor the banner is still a failure.

    The honest-empty rule must not turn a broken page into a healthy empty
    answer: the drift check stays, and the comparison stays incomplete.
    """
    _serve_yandex(monkeypatch, "<html><body>redesigned</body></html>")

    result = await compare_server.compare_prices(query="nothing-matches", sources=["yandex_market"])

    outcome = result.source_outcomes[0]
    assert outcome.status != "ok"
    assert result.complete is False
    assert result.sources_ok == []
