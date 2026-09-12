"""Golden shape for the normalized Ozon composer search tiles."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_core.resilience import shape_signature
from ozon_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "search_composer_live.json"


def test_live_composer_normalization_matches_shape_golden() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items = server._search_items_from_payload(payload)

    assert shape_signature({"items": items}) == [
        "items[].canonical_path:str",
        "items[].card_input:str",
        "items[].price:str",
        "items[].price_original:null",
        "items[].price_original:str",
        "items[].rating:str",
        "items[].rating_count:int",
        "items[].sku:int",
        "items[].stock:null",
        "items[].stock:str",
        "items[].title:str",
        "items[].url:str",
    ]
