"""Regression tests for the Citilink card extractor on a captured card page.

Captured 2026-08-06 from a live product page (provenance:
``card.provenance.json``) through the operator's Chrome over CDP, trimmed to
the product's price block and the blocks that try to steal the price. This
replaced a hand model that used element names the live page does not have
(``ProductHeader`` / ``ProductPrice__*``) and therefore could never show the
defect the real page shows immediately:

* the product's own prices live in ``data-meta-name="PriceBlock"`` —
  ``PriceBlock__price`` for 89 990 ₽ and ``PriceBlock__additional-price`` for
  the crossed-out 105 990 (with a "- 15%" badge inside the same node);
* the page ALSO carries recommendation snippets whose ``Snippet__old-price`` /
  ``Snippet__price`` elements win any document-wide ``querySelector`` — an
  unscoped hunt reads another product's strikethrough (97 990) as this
  product's old price;
* a credit block advertises «от 3 531 ₽ в месяц» and a bonus block 1800
  бонусов — numbers that are not prices.

Ground truth at capture time: 89 990 ₽ now, 105 990 crossed out, in stock
(«Добавить в корзину»).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from citilink_connector import server
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "card.html"

PRODUCT_URL = (
    "https://www.citilink.ru/product/noutbuk-lenovo-loq-15irx9-i7-13645hx-16gb-ssd512gb-rtx4050-6gb-15-6-ip-2166619/"
)


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(js_source, FIXTURE, page_url=PRODUCT_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_card_extractor_reads_the_product_card() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["title"].startswith("Ноутбук игровой Lenovo LOQ 15IRX9")
    assert payload["price_meta"] == "89 990"
    assert payload["is_available"] is True


def test_card_prices_are_the_products_own() -> None:
    """The crossed-out price must be the product's own 105 990, not the 97 990
    strikethrough of a recommended product elsewhere on the page."""
    payload = _extract(server._CARD_EXTRACT_JS)
    price, old_price = prices_from_tile(payload)
    assert price == 89990.0
    assert old_price == 105990.0


def test_recommendation_prices_are_never_the_products() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["old_price_text"] != "97 990", "a recommendation's strikethrough became the product's old price"
    candidates = payload["price_texts"]["attached"] + payload["price_texts"]["other"]
    assert all("97 990" not in c for c in candidates), "a recommendation price leaked into the buy-block scope"


def test_credit_and_bonus_amounts_are_not_price_candidates() -> None:
    """«от 3 531 ₽ в месяц» and «1800 бонусов» must not be candidates."""
    payload = _extract(server._CARD_EXTRACT_JS)
    candidates = payload["price_texts"]["attached"] + payload["price_texts"]["other"]
    assert all("3531" not in c.replace(" ", "") for c in candidates), "the credit instalment leaked into the candidates"
    assert all("1800" not in c for c in candidates), "the bonus amount leaked into the candidates"


def test_split_glyph_price_survives_the_meta_attribute_disappearing() -> None:
    """The day data-meta-price disappears, the glyph-attached display string
    inside the product's price block must carry the price."""
    payload = _extract(server._CARD_EXTRACT_JS)
    stripped = {**payload, "price_meta": None}
    price, old_price = prices_from_tile(stripped)
    assert price == 89990.0
    assert old_price == 105990.0


def test_availability_is_read_from_text_content_not_inner_text() -> None:
    code = "\n".join(line.split("//")[0] for line in server._CARD_EXTRACT_JS.splitlines())
    assert "innerText" not in code, "card extractor went back to innerText"
    assert "textContent" in code
