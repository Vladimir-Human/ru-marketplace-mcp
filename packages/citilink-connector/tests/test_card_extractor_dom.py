"""Regression tests for the Citilink card extractor on a modeled fixture.

The search extractor got a fixture when the July-2026 audit found the split
currency glyph; the card extractor reads the same ``data-meta-*`` contract and
scopes its price hunt to the buy block, but had no DOM-level test — markup
drift on the product page would have surfaced only live, from the operator's
Chrome. The fixture is a hand model of the card page, not a capture (no
provenance exists for it); replacing it with a captured page is an operator
task — citilink.ru is reachable from the audit machine.

``fixtures/card.html`` models the rendered product card: title in ``h1``, the
current price as digits and glyph in *separate sibling spans* (Citilink's
signature shape), an exact ``data-meta-price="79990"`` attribute, a bare
strikethrough ``89 990``, an instalment line, a bonus line, and a recommended
product priced ``1 999 ₽`` *outside* the buy block — three numbers that must
never be read as the price.

Ground truth on the fixture: 79 990 ₽ now (meta exact), 89 990 crossed out,
in stock.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from citilink_connector import server
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "card.html"


def _extract(js_source: str) -> dict:
    try:
        return run_extractor(
            js_source, FIXTURE, page_url="https://www.citilink.ru/product/smartfon-apple-iphone-16-128gb-2038477/"
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_card_extractor_reads_the_product_card() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["title"] == "Смартфон Apple iPhone 16 128Gb чёрный"
    assert payload["price_meta"] == "79990"
    # The digits and the glyph are adjacent sibling spans, so textContent glues
    # them with no space — that glued shape is exactly what coerce_price handles.
    assert payload["price_text"] == "79 990₽"
    assert payload["old_price_text"] == "89 990"
    assert payload["is_available"] is True


def test_exact_meta_price_attribute_is_preferred() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    price, old_price = prices_from_tile(payload)
    assert price == 79990.0
    assert old_price == 89990.0


def test_split_glyph_price_still_parses_when_the_meta_attribute_goes_away() -> None:
    """The day data-meta-price disappears, the sibling-span pair must carry the price."""
    payload = _extract(server._CARD_EXTRACT_JS)
    stripped = {**payload, "price_meta": None, "price_text": None}
    price, old_price = prices_from_tile(stripped)
    assert price == 79990.0
    assert old_price == 89990.0


def test_instalment_bonuses_and_recommended_products_are_never_prices() -> None:
    """«от 6 665 ₽/мес.», «798 бонусов» and the 1 999 ₽ add-on must not be candidates."""
    payload = _extract(server._CARD_EXTRACT_JS)
    candidates = payload["price_texts"]["attached"] + payload["price_texts"]["other"]
    assert all("6 665" not in c for c in candidates), "the instalment leaked into the candidates"
    assert all("798" not in c for c in candidates), "the bonus amount leaked into the candidates"
    assert all("1 999" not in c for c in candidates), "a recommended product leaked into the buy-block scope"


def test_availability_is_read_from_text_content_not_inner_text() -> None:
    code = "\n".join(line.split("//")[0] for line in server._CARD_EXTRACT_JS.splitlines())
    assert "innerText" not in code, "card extractor went back to innerText"
    assert "textContent" in code
