"""Card verification uses typed identity evidence and abstains on missing data.

Synthetic source responses test the comparison boundary. They do not claim
that a live connector currently supplies manufacturer identifiers.
"""

from types import SimpleNamespace

import pytest
from compare_connector import server
from compare_connector.identity import ProductIdentity
from fastmcp import Client


@pytest.mark.parametrize(
    "card, expected, verdict",
    [
        ({"gtin": "4006381333931"}, {"gtin": "04006381333931"}, "exact"),
        ({"gtin": "1234567890128"}, {"gtin": "4006381333931"}, "mismatch"),
        ({"brand": "ACME", "mpn": "AB12"}, {"brand": "acme", "mpn": "AB-12"}, "exact"),
        ({"title": "ACME AB12", "vendor_code": "AB12"}, {"brand": "ACME", "mpn": "AB12"}, "unknown"),
    ],
)
async def test_identity_is_verified_through_mcp_tool(monkeypatch, card, expected, verdict):
    async def fake_card(*, sku_or_path):
        assert sku_or_path == "12345"
        return {**card, "sku": "12345", "price_rub": 1299}

    monkeypatch.setattr(server, "SOURCES", {"ozon": SimpleNamespace(ozon_card=fake_card)})
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_verify_offer",
            {"source": "ozon", "product_id_or_url": "12345", "expected_identity": expected},
        )
    response = result.structured_content
    assert response["card"]["price_rub"] == 1299
    verified = response["identity_verification"]
    assert verified["match"]["status"] == verdict
    assert verified["observed"]["source"] == "ozon"
    assert verified["observed"]["native_product_id"] == "12345"


@pytest.mark.parametrize("present", [True, False])
async def test_wb_verification_uses_requested_row_not_first(monkeypatch, present):
    async def fake_card(*, nm_ids):
        assert nm_ids == [123]
        rows = [{"nm_id": 999, "gtin": "4006381333931"}]
        if present:
            rows.append({"nm_id": 123, "gtin": "1234567890128"})
        return {"items": rows}

    monkeypatch.setattr(server, "SOURCES", {"wildberries": SimpleNamespace(wb_card=fake_card)})
    result = await server.compare_verify_offer(
        "wildberries", "123", expected_identity=ProductIdentity(gtin="4006381333931")
    )
    assert result["identity_verification"]["match"]["status"] == ("mismatch" if present else "unknown")
