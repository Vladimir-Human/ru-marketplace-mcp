"""Golden shape for the trimmed MPStats parser fixtures."""

from __future__ import annotations

from mcp_core.resilience import shape_signature
from mpstats_connector import server

from .test_server import _item_payload, _warehouses_payload


def test_item_and_warehouse_normalization_match_shape_golden() -> None:
    item = server._parse_item_entry(_item_payload()["items"]["5107857210"], "ozon")
    warehouse = server._parse_warehouses_entry(_warehouses_payload()["data"]["5107857210"])
    assert item is not None and warehouse is not None

    assert shape_signature({"item": item.model_dump(), "warehouse": warehouse.model_dump()}) == [
        "item.brand:str",
        "item.count_graph[]:int",
        "item.days_on_stocks:int",
        "item.orders_graph[]:int",
        "item.orders_per_day:float",
        "item.place:str",
        "item.price_avg_rub:float",
        "item.prices_graph[]:int",
        "item.rubrics_graph[]:int",
        "item.seller:str",
        "item.seller_id:int",
        "item.sku:int",
        "item.stock_now:int",
        "item.totals.orders:int",
        "item.totals.sum:float",
        "item.totals.sum_prev:float",
        "warehouse.sku:null",
        "warehouse.stocks.fbo:null",
        "warehouse.stocks.fbo_warehouses:empty_array",
        "warehouse.stocks.fbs:int",
        "warehouse.stocks.last_update:str",
    ]
