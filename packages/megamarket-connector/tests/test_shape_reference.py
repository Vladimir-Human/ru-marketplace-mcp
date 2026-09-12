"""Golden shape for the captured Megamarket search normalization."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_core.resilience import shape_signature
from megamarket_connector import server


def test_live_search_normalization_matches_shape_golden() -> None:
    payload = json.loads((Path(__file__).parent / "fixtures" / "search_live.json").read_text(encoding="utf-8"))
    items, total, container_found = server._parse_items(payload)

    assert shape_signature({"items": items, "total": total, "container_found": container_found}) == [
        "container_found:bool",
        "items[].is_available:bool",
        "items[].item_id:str",
        "items[].old_price_rub:null",
        "items[].price_rub:float",
        "items[].rating:float",
        "items[].rating_count:int",
        "items[].title:str",
        "items[].url:str",
        "total:int",
    ]
