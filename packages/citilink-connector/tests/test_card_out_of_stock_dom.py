"""Regression tests for the Citilink card extractor on a captured OUT-OF-STOCK card.

Captured 2026-08-06 from a live product page with no buy block at all
(provenance: ``card_out_of_stock.provenance.json``): the product is «Нет в
наличии», so the page renders neither ``PriceBlock`` nor any product price —
while the recommendation snippets around it still carry ``Snippet__price`` and
``data-meta-price`` attributes. An unscoped hunt reads a recommendation's
60 630 as the product's price — a plausible, wrong, publishable number. The
extractor must report no price instead.

Ground truth at capture time: title present, NO price, NO strikethrough,
is_available false.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from citilink_connector import server
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "card_out_of_stock.html"

PRODUCT_URL = (
    "https://www.citilink.ru/product/noutbuk-acer-gadget-erbook-air-ryzen-7-8745hs-16gb-ssd512gb-780m-14-ip-2147976/"
)


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(js_source, FIXTURE, page_url=PRODUCT_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_out_of_stock_card_reports_no_price() -> None:
    """The product has no price of its own: None, never a recommendation's."""
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["title"].startswith("Ноутбук Acer Gadget ERBook Air")
    price, old_price = prices_from_tile(payload)
    assert price is None
    assert old_price is None


def test_a_recommendation_price_is_never_the_products() -> None:
    """60 630 belongs to a recommendation snippet; it must not surface anywhere
    in the card payload."""
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["price_meta"] is None
    assert payload["price_text"] is None
    assert payload["old_price_text"] is None
    candidates = payload["price_texts"]["attached"] + payload["price_texts"]["other"]
    assert all("60 630" not in c for c in candidates), "a recommendation price leaked into the card payload"


def test_out_of_stock_is_read_from_the_page_text() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["is_available"] is False
