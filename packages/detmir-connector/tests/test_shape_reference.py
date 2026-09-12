"""Golden shape checks for normalized Detsky Mir fixture payloads."""

from __future__ import annotations

import json
from pathlib import Path

from detmir_connector import server
from mcp_core.resilience import shape_signature

FIXTURES = Path(__file__).parent / "fixtures"


def test_card_normalization_matches_shape_golden() -> None:
    payload = json.loads((FIXTURES / "card_live.json").read_text(encoding="utf-8"))
    node = server._product_node(payload)
    assert node is not None
    product = server._parse_product(node)

    assert shape_signature(product.model_dump()) == [
        "article:str",
        "availability:str",
        "available_offline:bool",
        "available_online:bool",
        "brand:str",
        "discount_percent:int",
        "is_marketplace:bool",
        "old_price_rub:null",
        "picture:str",
        "price_rub:float",
        "product_id:int",
        "questions_count:int",
        "rating:float",
        "review_count:int",
        "store_count:int",
        "title:str",
        "url:str",
        "vendor:str",
    ]
