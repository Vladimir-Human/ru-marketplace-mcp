"""Golden shape checks for normalized Wildberries fixture payloads."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_core.resilience import shape_signature
from wb_connector import server

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_GOLDEN = [
    "products[].brand:str",
    "products[].feedbacks:int",
    "products[].in_stock:bool",
    "products[].name:str",
    "products[].nm_id:int",
    "products[].price_original_rub:float",
    "products[].price_rub:float",
    "products[].review_rating:float",
    "products[].review_rating:int",
    "products[].supplier:str",
    "products[].supplier_id:int",
    "products[].supplier_rating:float",
    "products[].total_quantity:int",
]


def test_search_normalization_matches_shape_golden() -> None:
    payload = json.loads((FIXTURES / "search_v9_live.json").read_text(encoding="utf-8"))
    normalized = [server._card_item_dict(item) for item in payload["products"]]

    assert shape_signature({"products": normalized}) == SEARCH_GOLDEN
