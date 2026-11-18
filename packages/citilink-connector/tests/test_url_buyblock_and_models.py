"""Regression tests for Citilink URL hygiene, buyBlock availability precedence, and output model descriptions."""

from __future__ import annotations

from pathlib import Path

import pytest
from citilink_connector import models_output, server
from mcp_core.domtest import JsdomUnavailable, run_extractor


@pytest.mark.req("R-OUTPUT-PAGE-URLS")
def test_search_item_normalizes_relative_and_drops_off_host_url() -> None:
    rel = server._search_item_from_tile(
        {
            "product_id": "noutbuk-lenovo-2169270",
            "title": "Lenovo",
            "price_meta": "52999",
            "url": "/product/noutbuk-lenovo-2169270/",
        }
    )
    foreign = server._search_item_from_tile(
        {
            "product_id": "noutbuk-lenovo-2169270",
            "title": "Lenovo",
            "price_meta": "52999",
            "url": "https://evil.example/product/noutbuk-lenovo-2169270/",
        }
    )
    assert rel.url == "https://www.citilink.ru/product/noutbuk-lenovo-2169270/"
    assert foreign.url == ""


@pytest.mark.req("R-MARKETPLACE-PRICE-WORDING")
def test_card_in_stock_buyblock_beats_body_oos_text(tmp_path: Path) -> None:
    html = """<!doctype html><html><head><title>Citilink</title></head><body>
      <h1>Ноутбук Lenovo LOQ 15IRX9</h1>
      <div data-meta-name="PriceBlock">
        <div data-meta-name="PriceBlock__price"><span data-meta-price="89990">89 990 ₽</span></div>
      </div>
      <div data-checkout-button="true"><button data-meta-name="BasketDesktopButton" type="button">В корзину</button></div>
      <div class="recommendations"><span>Аксессуар: Нет в наличии</span></div>
      <footer><a href="/order/">Товары под заказ</a></footer>
    </body></html>"""
    card_file = tmp_path / "in_stock_with_footer_oos.html"
    card_file.write_text(html, encoding="utf-8")
    try:
        payload = run_extractor(
            server._CARD_EXTRACT_JS,
            card_file,
            page_url="https://www.citilink.ru/product/noutbuk-lenovo-2169270/",
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))

    assert payload["is_available"] is True
    assert payload["price_meta"] == "89990"


@pytest.mark.req("R-NO-FABRICATION")
def test_card_without_buyblock_ignores_recommendation_basket_button(tmp_path: Path) -> None:
    html = """<!doctype html><html><head><title>Citilink</title></head><body>
      <h1>Ноутбук без блока покупки</h1>
      <aside class="recommendations">
        <button data-meta-name="BasketDesktopButton" type="button">В корзину</button>
      </aside>
    </body></html>"""
    card_file = tmp_path / "no_buyblock_rec_button.html"
    card_file.write_text(html, encoding="utf-8")
    try:
        payload = run_extractor(
            server._CARD_EXTRACT_JS,
            card_file,
            page_url="https://www.citilink.ru/product/noutbuk-lenovo-2169270/",
        )
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))

    assert payload["price_meta"] is None
    assert payload["is_available"] is None


@pytest.mark.req("R-MARKETPLACE-PRICE-WORDING")
def test_models_output_descriptions_name_citilink_not_dns() -> None:
    assert "DNS" not in (models_output.MetaOut.__doc__ or "")
    search_desc = models_output.CitilinkSearchItemOut.model_fields["product_id"].description or ""
    card_desc = models_output.CitilinkCardResponse.model_fields["product_id"].description or ""
    assert "DNS" not in search_desc and "Citilink" in search_desc
    assert "DNS" not in card_desc and "Citilink" in card_desc
