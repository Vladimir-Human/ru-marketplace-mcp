"""Regression tests for Ozon OOS webPrice vs webSale, instock_no_price validation, and blank search queries."""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError
from ozon_connector import server


@pytest.fixture(autouse=True)
def _clear_ozon_cache() -> None:
    server._cache.clear()


@pytest.mark.req("R-OZON-SEARCH-NUMERIC")
def test_oos_webprice_records_unavailable_and_blocks_websale_fallback() -> None:
    payload = {
        "widgetStates": {
            "webPrice-1": json.dumps({"isAvailable": False, "price": None, "cardPrice": None}),
            "webSale-1": json.dumps({"cellTrackingInfo": {"product": {"finalPrice": 5990, "price": 5990}}}),
        }
    }
    parsed = server._parse_widgets(payload)
    assert parsed.get("is_available") is False
    assert parsed.get("price") is None


@pytest.mark.req("R-NO-FABRICATION")
def test_websale_before_webprice_clears_oos_fallback_and_yields_to_webprice() -> None:
    oos_sale_first = {
        "widgetStates": {
            "webSale-1": json.dumps({"cellTrackingInfo": {"product": {"finalPrice": 900, "price": 1000}}}),
            "webPrice-1": json.dumps({"isAvailable": False, "price": None, "cardPrice": None}),
        }
    }
    parsed_oos = server._parse_widgets(oos_sale_first)
    assert parsed_oos.get("is_available") is False
    assert parsed_oos.get("price") is None
    assert parsed_oos.get("price_original") is None

    instock_sale_first = {
        "widgetStates": {
            "webSale-1": json.dumps({"cellTrackingInfo": {"product": {"finalPrice": 900, "price": 1000}}}),
            "webPrice-1": json.dumps({"isAvailable": True, "price": "1 150 ₽", "cardPrice": "1 050 ₽"}),
        }
    }
    parsed_in = server._parse_widgets(instock_sale_first)
    assert parsed_in.get("is_available") is True
    assert parsed_in.get("price") == 1150.0
    assert parsed_in.get("card_price") == 1050.0


@pytest.mark.req("R-OZON-SEARCH-NUMERIC")
async def test_ozon_card_drops_equal_original_price_and_flags_instock_no_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_only_payload = {
        "widgetStates": {
            "webProductHeading-1": json.dumps({"title": "Кофемашина DeLonghi"}),
            "webPrice-1": json.dumps(
                {"isAvailable": True, "price": None, "cardPrice": "29 990 ₽", "originalPrice": None}
            ),
        }
    }

    async def fake_fetch(api_path: str, ctx: object) -> tuple[int, str, str]:
        return 200, json.dumps(card_only_payload), "curl_cffi"

    monkeypatch.setattr(server, "_fetch_composer", fake_fetch)
    card = await server.ozon_card("123456789")
    assert card.is_available is True
    assert card.price is None
    assert card.card_price == 29990.0
    assert any(w.startswith("instock_no_price") for w in card.meta.warnings)

    equal_orig_payload = {
        "widgetStates": {
            "webPrice-1": json.dumps(
                {"isAvailable": True, "price": "5 990 ₽", "cardPrice": "5 790 ₽", "originalPrice": "5 990 ₽"}
            )
        }
    }
    parsed_eq = server._parse_widgets(equal_orig_payload)
    assert parsed_eq["price"] == 5990.0
    assert parsed_eq["price_original"] is None


@pytest.mark.req("R-OZON-CDP-FAILURE-HINT")
async def test_ozon_search_blank_query_raises_bad_request_without_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("blank ozon_search query must not invoke network or CDP")

    monkeypatch.setattr(server, "_run_sync_bounded", forbidden)
    monkeypatch.setattr(server, "_cdp_fetch_json", forbidden)

    with pytest.raises(ToolError) as excinfo:
        await server.ozon_search("   ")

    err = json.loads(str(excinfo.value))
    assert err["error"] == "bad_request"
