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
from yandex_connector import ssr as yandex_ssr
from yandex_connector.models_output import YandexProduct, YandexSearchResponse


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


async def test_yandex_live_fixture_variant_survives_comparison(monkeypatch):
    fixture = Path(__file__).parents[2] / "yandex-connector/tests/fixtures/search_kettle.html"
    items = yandex_ssr.parse_search(fixture.read_text(encoding="utf-8"))["items"]
    row = next(item for item in items if item["product_id"] == "198679568")

    async def search(**kwargs):
        return YandexSearchResponse(items=[YandexProduct(**row)])

    monkeypatch.setattr(server, "SOURCES", {"yandex_market": SimpleNamespace(yandex_search=search)})
    result = await server.compare_prices(query="чайник", sources=["yandex_market"])
    assert result.cheapest.variant_id == "4668084807"
    assert result.cheapest.price_rub == 2004


async def test_yandex_empty_shell_never_becomes_a_verified_card(monkeypatch):
    """The 2026-09-13 hollow frame, end to end through the real yandex server.

    Verification must surface the connector's retryable transport_down
    (empty_product_shell) as-is — never a card with invented prices, and never
    a silent pass with matches=None.
    """
    from yandex_connector import server as yandex_server

    fixture = Path(__file__).parents[2] / "yandex-connector/tests/fixtures/card_empty_shell.html"
    html = fixture.read_text(encoding="utf-8")

    async def fake_fetch(url, label, ctx=None):
        return html

    monkeypatch.setattr(yandex_server, "_fetch_html", fake_fetch)
    monkeypatch.setattr(
        server,
        "SOURCES",
        {"yandex_market": SimpleNamespace(yandex_card=yandex_server.yandex_card)},
    )

    with pytest.raises(ToolError) as excinfo:
        await server.compare_verify_offer(
            "yandex_market",
            "4315891968",
            expected_price_rub=11329.0,
            expected_variant_id="103796664836",
        )

    payload = json.loads(str(excinfo.value))
    assert payload["error"] == "transport_down"
    assert payload["retryable"] is True
    assert "empty_product_shell" in payload["message"]


def test_dedupe_keeps_distinct_known_variants_of_same_product():
    from compare_connector.models_output import MarketOffer

    variants = [
        MarketOffer(source="yandex_market", product_id="198679568", variant_id=sku) for sku in ("243", "245", "243")
    ]
    assert [offer.variant_id for offer in server._dedupe(variants)] == ["243", "245"]


@pytest.mark.parametrize(
    "source,value",
    [
        ("wildberries", "https://123.example/catalog/456/detail.aspx"),
        ("wildberries", "https://www.wildberries.ru/catalog/x/detail.aspx?nm=123"),
        ("wildberries", "item-123"),
        ("wildberries", "-123"),
        ("detsky_mir", "https://detmir.ru.evil.invalid/product/index/id/123/"),
        ("detsky_mir", "0"),
    ],
)
def test_numeric_card_identifier_does_not_pick_unrelated_digits(source, value):
    with pytest.raises(ToolError, match="positive numeric id"):
        server._numeric_card_id(source, value)


async def test_detmir_canonical_url_dispatches_requested_id(monkeypatch):
    async def card(*, product_id):
        assert product_id == 123
        return {"product": {"product_id": product_id, "price_rub": 500}}

    monkeypatch.setattr(server, "SOURCES", {"detsky_mir": SimpleNamespace(detmir_card=card)})
    result = await server.compare_verify_offer(
        "detsky_mir", "https://www.detmir.ru/product/index/id/123/?tracking=9", 500
    )
    assert result["price_verification"]["matches"] is True


@pytest.mark.parametrize(
    "source,record",
    [
        ("wildberries", {"items": [{"nm_id": 123, "price_rub": 100}, {"nm_id": 123, "price_rub": 200}]}),
        ("detsky_mir", {"product": {"product_id": 999, "price_rub": 100}}),
    ],
)
async def test_ambiguous_or_wrong_record_never_verifies_price(monkeypatch, source, record):
    async def card(**kwargs):
        return record

    tool_name = server._CARD_TOOL_NAMES[source]
    monkeypatch.setattr(server, "SOURCES", {source: SimpleNamespace(**{tool_name: card})})
    result = await server.compare_verify_offer(source, "123", 100, ProductIdentity())
    assert result["price_verification"]["matches"] is None
    assert result["identity_verification"]["match"]["status"] == "unknown"
