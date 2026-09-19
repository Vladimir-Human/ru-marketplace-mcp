"""Search limits apply to distinct sellable variants, not product families."""

import html
import json

import pytest
from yandex_connector import server


@pytest.mark.parametrize("with_ware_id", [True, False])
async def test_zone_search_keeps_distinct_skus_and_dedupes_repeated_variant_before_limit(monkeypatch, with_ware_id):
    payloads = [
        {
            "type": "offer",
            "oskuId": product_id,
            "marketSku": sku,
            "wareId": ware,
            "title": "Kettle",
            "price": price,
            "isAvailable": True,
        }
        for product_id, sku, ware, price in [
            ("123", "777", "a", 3000),
            ("123", "777", "repeat", 3000),
            ("123", "888", "b", 2000),
            ("456", "999", "c", 4000),
        ]
    ]
    if not with_ware_id:
        for payload in payloads:
            payload.pop("wareId")
    document = "".join(
        '<div data-zone-name="productSnippet" data-zone-data="'
        + html.escape(json.dumps(payload), quote=True)
        + '"></div>'
        for payload in payloads
    )

    async def fetch(url, label, ctx=None):
        return document

    monkeypatch.setattr(server, "_fetch_html", fetch)
    result = await server.yandex_search(query="Kettle", limit=3)

    assert [(item.product_id, item.sku_id, item.price_rub) for item in result.items] == [
        ("123", "777", 3000),
        ("123", "888", 2000),
        ("456", "999", 4000),
    ]
    assert result.returned == 3
