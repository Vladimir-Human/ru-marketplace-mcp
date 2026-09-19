"""Native parser/model regressions for stock filtering and variant ranking."""

from __future__ import annotations

import html
import json
from types import SimpleNamespace

import pytest
from compare_connector import server
from ozon_connector import server as ozon
from ozon_connector.models_output import OzonSearchResponse
from wb_connector import server as wb
from wb_connector.models_output import WbSearchResponse
from yandex_connector import server as yandex


async def test_native_wb_unknown_quantity_stays_unknown_in_comparison(monkeypatch):
    raw_items = []
    for product_id, quantity in [(1, None), (2, 0), (3, 5)]:
        raw = {
            "id": product_id,
            "name": "Kettle",
            "sizes": [{"price": {"product": product_id * 100000}}],
        }
        if quantity is not None:
            raw["totalQuantity"] = quantity
        raw_items.append(wb._card_item_dict(raw))
    native = WbSearchResponse(items=raw_items)
    assert native.items[0].total_quantity is None
    assert native.items[0].in_stock is False  # documented legacy WB encoding

    async def search(query, page):
        return native

    monkeypatch.setattr(server, "SOURCES", {"wildberries": SimpleNamespace(wb_search=search)})
    result = await server.compare_prices(query="Kettle", sources=["wildberries"], in_stock_only=True)

    assert {item.product_id: item.in_stock for item in result.offers} == {"1": None, "2": False, "3": True}
    assert result.cheapest.product_id == "3"
    assert result.cheapest_comparable.product_id == "3"
    assert any("excluded 2 priced offer(s)" in warning for warning in result.warnings)


async def test_native_ozon_stock_labels_cannot_fabricate_available_winner(monkeypatch):
    # Generated native atoms, not claims that these exact strings were captured
    # live. They all pass through the upstream stock-label extraction path.
    labels = [
        "не осталось",
        "2 шт",
        "купили 3 шт",
        "осталось 1–3 шт",
        "осталось -3 шт",
        "осталось 1234 567 шт",
        "3 шт осталось",
    ]
    items = []
    for sku, label in enumerate(labels, start=1):
        items.append(
            ozon._parse_search_tile(
                {
                    "sku": sku,
                    "mainState": [
                        {"type": "textAtom", "textAtom": {"text": "Kettle"}},
                        {"type": "priceV2", "priceV2": {"price": [{"text": f"{sku * 1000} ₽"}]}},
                        {"type": "textDS", "textDS": {"text": label}},
                    ],
                }
            )
        )
    native = OzonSearchResponse(items=items)
    assert [item.stock for item in native.items] == labels

    async def search(query):
        return native

    monkeypatch.setattr(server, "SOURCES", {"ozon": SimpleNamespace(ozon_search=search)})
    result = await server.compare_prices(query="Kettle", sources=["ozon"], per_source_limit=10, in_stock_only=True)

    assert {item.product_id: item.in_stock for item in result.offers} == {
        "1": False,
        "2": None,
        "3": None,
        "4": None,
        "5": None,
        "6": None,
        "7": True,
    }
    assert result.cheapest.product_id == "7"
    assert result.cheapest_comparable.product_id == "7"
    assert result.cheapest.price_rub == 7000


@pytest.mark.parametrize("channel", ["textDS", "labelList"])
@pytest.mark.parametrize("label", ["нет в наличии", "Нет на складе", "Распродано!", "товар закончился"])
async def test_native_ozon_explicit_absence_survives_both_label_channels(monkeypatch, channel, label):
    def tile(sku, stock_label):
        stock_atom = (
            {"type": "textDS", "textDS": {"text": stock_label}}
            if channel == "textDS"
            else {"type": "labelList", "labelList": {"items": [{"title": stock_label}]}}
        )
        return ozon._parse_search_tile(
            {
                "sku": sku,
                "mainState": [
                    {"type": "textAtom", "textAtom": {"text": "Kettle"}},
                    {"type": "priceV2", "priceV2": {"price": [{"text": f"{sku * 1000} ₽"}]}},
                    stock_atom,
                ],
            }
        )

    native = OzonSearchResponse(items=[tile(1, label), tile(2, "В наличии")])
    assert [item.stock for item in native.items] == [label, "В наличии"]

    async def search(query):
        return native

    monkeypatch.setattr(server, "SOURCES", {"ozon": SimpleNamespace(ozon_search=search)})
    result = await server.compare_prices(query="Kettle", sources=["ozon"], in_stock_only=True)

    assert {item.product_id: item.in_stock for item in result.offers} == {"1": False, "2": True}
    assert result.cheapest.product_id == "2"
    assert result.cheapest_comparable.product_id == "2"


@pytest.mark.parametrize("with_ware_id", [True, False])
async def test_native_yandex_distinct_sku_reaches_cheapest_comparable(monkeypatch, with_ware_id):
    payloads = [
        {
            "type": "offer",
            "oskuId": "123",
            "marketSku": sku,
            "wareId": ware,
            "title": "Kettle",
            "price": price,
            "isAvailable": True,
        }
        for sku, ware, price in [("777", "offer-a", 3000), ("888", "offer-b", 2000)]
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

    monkeypatch.setattr(yandex, "_fetch_html", fetch)
    monkeypatch.setattr(server, "SOURCES", {"yandex_market": yandex})
    result = await server.compare_prices(query="Kettle", sources=["yandex_market"], in_stock_only=True)

    assert result.total_offers == 2
    assert result.cheapest.variant_id == "888"
    assert result.cheapest_comparable.price_rub == 2000
    assert result.price_spread_rub == 1000
