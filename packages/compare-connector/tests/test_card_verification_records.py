"""Verification reads the actual native card shape, rather than synthetic flat cards."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from compare_connector import server
from compare_connector.identity import ProductIdentity
from detmir_connector import server as detmir
from detmir_connector.models_output import DetmirCardResponse
from fastmcp import Client
from fastmcp.exceptions import ToolError
from ozon_connector.models_output import OzonCardResponse
from wb_connector import server as wb
from wb_connector.models_output import WbCardItem, WbCardResponse


async def test_wb_price_and_identity_use_the_same_requested_fixture_row(monkeypatch):
    fixture = Path(__file__).parents[2] / "wb-connector/tests/fixtures/card_v4_live.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))["products"][0]
    requested = WbCardItem(**wb._card_item_dict(raw))
    assert requested.price_rub is not None

    async def card(*, nm_ids):
        assert nm_ids == [raw["id"]]
        return WbCardResponse(count=2, items=[WbCardItem(nm_id=999, price_rub=1), requested])

    monkeypatch.setattr(server, "SOURCES", {"wildberries": SimpleNamespace(wb_card=card)})
    async with Client(server.mcp) as client:
        response = (
            await client.call_tool(
                "compare_verify_offer",
                {
                    "source": "wildberries",
                    "product_id_or_url": str(raw["id"]),
                    "expected_price_rub": requested.price_rub - 100,
                    "expected_identity": {"brand": "TECNO", "variant_attributes": {"color": "белый"}},
                },
            )
        ).structured_content
    assert response["price_verification"]["observed_price_rub"] == requested.price_rub
    assert response["price_verification"]["delta_rub"] == 100
    assert response["price_verification"]["matches"] is False
    assert response["identity_verification"]["observed"]["native_product_id"] == str(raw["id"])
    assert response["identity_verification"]["match"]["status"] == "mismatch"
    assert len(response["card"]["items"]) == 2  # preserve native evidence for inspection


async def test_detmir_fixture_product_is_unwrapped_for_price_and_identity(monkeypatch):
    fixture = Path(__file__).parents[2] / "detmir-connector/tests/fixtures/card_live.json"
    product = detmir._parse_product(json.loads(fixture.read_text(encoding="utf-8"))["item"])
    assert product.price_rub is not None and product.product_id is not None

    async def card(*, product_id):
        assert product_id == product.product_id
        return DetmirCardResponse(product=product, region="RU-MOW")

    monkeypatch.setattr(server, "SOURCES", {"detsky_mir": SimpleNamespace(detmir_card=card)})
    response = await server.compare_verify_offer(
        "detsky_mir", str(product.product_id), expected_price_rub=product.price_rub, expected_identity=ProductIdentity()
    )
    assert response["price_verification"]["matches"] is True
    assert response["price_verification"]["delta_rub"] == 0
    observed = response["identity_verification"]["observed"]
    assert observed["native_product_id"] == str(product.product_id)
    assert observed["brand"] == product.brand
    assert observed["mpn"] == ""  # a vendor article is not manufacturer evidence
    assert response["card"]["product"]["product_id"] == product.product_id


async def test_ozon_verifies_regular_price_not_card_discount(monkeypatch):
    async def card(*, sku_or_path):
        return OzonCardResponse(price=1200, card_price=900, price_original=1500)

    monkeypatch.setattr(server, "SOURCES", {"ozon": SimpleNamespace(ozon_card=card)})
    result = await server.compare_verify_offer("ozon", "12345", expected_price_rub=1000)
    assert result["price_verification"] == {
        "expected_price_rub": 1000,
        "observed_price_rub": 1200,
        "delta_rub": 200,
        "matches": False,
    }


async def test_missing_requested_wb_row_does_not_use_another_price(monkeypatch):
    async def card(*, nm_ids):
        return WbCardResponse(count=1, items=[WbCardItem(nm_id=999, price_rub=123)])

    monkeypatch.setattr(server, "SOURCES", {"wildberries": SimpleNamespace(wb_card=card)})
    result = await server.compare_verify_offer("wildberries", "123", expected_price_rub=123)
    assert result["price_verification"]["observed_price_rub"] is None
    assert result["price_verification"]["matches"] is None


@pytest.mark.parametrize("price", [None, True, 0, -1, float("nan"), float("inf")])
async def test_invalid_observed_price_stays_unknown(monkeypatch, price):
    async def card(*, sku_or_path):
        return {"price": price}

    monkeypatch.setattr(server, "SOURCES", {"ozon": SimpleNamespace(ozon_card=card)})
    result = await server.compare_verify_offer("ozon", "12345", expected_price_rub=100)
    assert result["price_verification"]["matches"] is None
    assert result["price_verification"]["observed_price_rub"] is None


async def test_mcp_rejects_nonfinite_expected_price_before_querying_source(monkeypatch):
    async def card(*, sku_or_path):
        pytest.fail("invalid expected price must be rejected before any source query")

    monkeypatch.setattr(server, "SOURCES", {"ozon": SimpleNamespace(ozon_card=card)})
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "compare_verify_offer",
                {"source": "ozon", "product_id_or_url": "12345", "expected_price_rub": "Infinity"},
            )


async def test_yandex_variant_mismatch_is_rejected_before_price_delta(monkeypatch):
    async def card(*, product_id, include_reviews):
        return {"product_id": product_id, "sku_id": "card-default", "price_rub": 4146}

    monkeypatch.setattr(server, "SOURCES", {"yandex_market": SimpleNamespace(yandex_card=card)})
    with pytest.raises(ToolError, match="variant mismatch"):
        await server.compare_verify_offer(
            "yandex_market", "198679568", expected_price_rub=2004, expected_variant_id="4668084807"
        )


async def test_yandex_matching_variant_can_verify_price(monkeypatch):
    async def card(*, product_id, include_reviews):
        return {"product_id": product_id, "sku_id": "4668084807", "price_rub": 2004}

    monkeypatch.setattr(server, "SOURCES", {"yandex_market": SimpleNamespace(yandex_card=card)})
    result = await server.compare_verify_offer(
        "yandex_market", "198679568", expected_price_rub=2004, expected_variant_id="4668084807"
    )
    assert result["price_verification"]["matches"] is True
