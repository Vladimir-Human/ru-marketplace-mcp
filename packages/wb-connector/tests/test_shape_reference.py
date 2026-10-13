"""Golden shape checks for normalized Wildberries fixture payloads."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_core.resilience import shape_signature
from wb_connector import server

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_GOLDEN = [
    "products[].brand:str",
    "products[].color:str",
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


def test_no_results_region_warning_matches_shape_golden() -> None:
    response = server.WbNoResultsResponse(
        query="nothing",
        page=1,
        total_ids=0,
        meta=server.MetaOut(source="wb_search", healthy=True, warnings=server._dom_dest_warning("-1029256")),
    )
    assert shape_signature(response.model_dump(mode="json")) == [
        "meta.healthy:bool",
        "meta.source:str",
        "meta.warnings[]:str",
        "page:int",
        "query:str",
        "status:str",
        "total_ids:int",
    ]


def test_no_results_without_warning_matches_compact_shape_golden() -> None:
    response = server.WbNoResultsResponse(query="nothing", page=1, total_ids=0)
    assert shape_signature(response.model_dump(mode="json")) == [
        "meta:null",
        "page:int",
        "query:str",
        "status:str",
        "total_ids:int",
    ]


def test_no_results_output_schema_keeps_its_fields() -> None:
    """MCP clients read the serialization schema; it must list the fields, not a bare object."""
    schema = server.WbNoResultsResponse.model_json_schema(mode="serialization")
    assert {"status", "query", "page", "total_ids", "meta"} <= set(schema["properties"])
