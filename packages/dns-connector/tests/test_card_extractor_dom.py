"""Regression tests for the DNS card extractor on a modeled fixture.

The fixture below is a hand-modelled card page, not a capture — the audit of
2026-08-06 established that only the search fixture in this package came from
a live page. The tests still execute the real extractor JS, so selector and
price-selection regressions are caught; what a model cannot protect against is
the live page's shape diverging from the model. Replacing this fixture with a
captured page is tracked as an operator task (a live dns-shop.ru fetch needs a
residential RU IP).

The card extractor's availability fallback read ``document.body.innerText``.
The page's *price* nodes are read via named selectors, but the availability
fallback scanned the whole body — and ``innerText`` depends on layout and CSS,
differs between a warm tab and a freshly navigated one, and does not exist in
jsdom. The shared helpers already read ``textContent`` for exactly that reason;
this test pins the card fallback to the same rule so the jsdom layer can
assert on it at all.

``fixtures/card.html`` models a rendered product card page: title in ``h1``,
current price in ``.product-buy__price`` with a strikethrough sibling and a
"кредит от 5 751 ₽/мес." instalment line, explicit ``В наличии`` availability,
and a stray "Дополнительные товары: сумка 1 499 ₽" block that must never be
read as the product price.

Ground truth on the fixture: 58 999 ₽ (was 62 999), in stock.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dns_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "card.html"


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(
            js_source, FIXTURE, page_url="https://www.dns-shop.ru/product/b7a1667f9b19ed20/noutbuk-huawei/"
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_card_extractor_reads_the_product_card() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["title"] == "Ноутбук HUAWEI MateBook D 16 2024 MCLF-X серый"
    assert payload["price_text"] == "58 999 ₽"
    assert payload["old_price_text"] == "62 999"
    assert payload["is_available"] is True


def test_card_mapping_produces_the_wire_shape() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    price = server.R.price_from_texts(payload.get("price_text"))
    old_price = server.R.price_from_texts(payload.get("old_price_text"))
    assert price == 58999.0
    assert old_price == 62999.0


def test_the_instalment_line_is_never_the_card_price() -> None:
    """«кредит от 5 751 ₽/мес.» must not become the price."""
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["price_text"] != "кредит от 5 751 ₽/мес."
    assert payload["price_text"] == "58 999 ₽"


def test_a_stray_price_block_is_not_picked_up() -> None:
    """«Дополнительные товары: сумка 1 499 ₽» is a recommendation, not the price."""
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["price_text"] != "1 499 ₽"


def test_availability_fallback_reads_text_content_not_inner_text() -> None:
    code = "\n".join(line.split("//")[0] for line in server._CARD_EXTRACT_JS.splitlines())
    assert "innerText" not in code, "card extractor went back to innerText"
    assert "textContent" in code
