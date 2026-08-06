"""Regression tests for the DNS card extractor on a captured card page.

Captured 2026-08-06 from the live product page (provenance:
``card.provenance.json``) through the operator's Chrome over CDP from a
residential RU IP, trimmed to the h1, the ``.product-buy`` container and the
availability wrap. This replaced a hand model whose strikethrough and
recommendation blocks used markup the live page does not have.

Ground truth at capture time: 58 999 ₽ now, NO strikethrough on this product,
in stock (``В наличии``). The trap is live too: the buy block's
``.product-buy__sub`` sibling carries the instalment line «от 5 751 ₽/ мес.»,
the smallest number on the card — exactly the value a body-scan minimum once
returned as the price.

The availability path is pinned as well: the card extractor's fallback must
read ``textContent``, never ``innerText`` (layout-dependent and unavailable in
jsdom).
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
    assert payload["title"].startswith('16" Ноутбук HUAWEI MateBook D 16 2024 MCLF-X')
    assert payload["price_text"] == "58 999 ₽"
    assert payload["old_price_text"] is None
    assert payload["is_available"] is True


def test_card_mapping_produces_the_wire_shape() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    price = server.R.price_from_texts(payload.get("price_text"))
    old_price = server.R.price_from_texts(payload.get("old_price_text"))
    assert price == 58999.0
    assert old_price is None, "the capture carried no strikethrough; inventing one would be a drifted read"


def test_the_instalment_line_is_never_the_card_price() -> None:
    """«от 5 751 ₽/ мес.» is the smallest number on the card — a body-scan
    minimum returns it. The named-node hunt must not."""
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["price_text"] == "58 999 ₽"
    assert "5 751" not in payload["price_text"]


def test_availability_is_read_from_the_avail_wrap() -> None:
    payload = _extract(server._CARD_EXTRACT_JS)
    assert payload["availability_text"].startswith("В наличии")


def test_availability_fallback_reads_text_content_not_inner_text() -> None:
    code = "\n".join(line.split("//")[0] for line in server._CARD_EXTRACT_JS.splitlines())
    assert "innerText" not in code, "card extractor went back to innerText"
    assert "textContent" in code
