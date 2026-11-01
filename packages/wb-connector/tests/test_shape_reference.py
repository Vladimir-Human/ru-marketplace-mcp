"""Golden shape checks for normalized Wildberries fixture payloads.

The goldens live in ``wb_connector.shape_reference`` — measured by running the
real ``_card_item_dict`` flattener over the captured v9 search and card/v4
payloads — and this file asserts the registry still agrees with the fixtures,
so it cannot go stale silently. The drift half proves the runtime shape-drift
warning fires through the public ``wb_search`` entry when a parser-critical
key family vanishes from the raw product objects.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from mcp_core.resilience import shape_signature
from wb_connector import server, shape_reference

FIXTURES = Path(__file__).parent / "fixtures"


def _patch_get_text(monkeypatch, body_by_needle: dict[str, str]) -> None:
    async def fake_safe_get_text(client, url):
        for needle, body in body_by_needle.items():
            if needle in url:
                return 200, body, None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
    monkeypatch.setattr(server, "_polite_wait", no_wait)


def test_search_normalization_matches_shape_golden() -> None:
    payload = json.loads((FIXTURES / "search_v9_live.json").read_text(encoding="utf-8"))
    normalized = [server._card_item_dict(item) for item in payload["products"]]

    assert shape_signature({"products": normalized}) == list(shape_reference.SEARCH_SHAPE_REFERENCE)


def test_card_normalization_matches_shape_golden() -> None:
    payload = json.loads((FIXTURES / "card_v4_live.json").read_text(encoding="utf-8"))
    normalized = [server._card_item_dict(item) for item in payload["products"]]

    assert shape_signature({"products": normalized}) == list(shape_reference.CARD_SHAPE_REFERENCE)


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


async def test_search_warns_when_a_required_key_family_vanishes(monkeypatch):
    """Products that still flatten after losing the whole price family are
    structural drift the agent must see in the tool's own response."""
    payload = json.loads((FIXTURES / "search_v9_live.json").read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    for product in drifted["products"]:
        product.pop("sizes", None)
        product.pop("salePriceU", None)
        product.pop("priceU", None)
    _patch_get_text(monkeypatch, {"v9/search": json.dumps(drifted)})

    result = await server.wb_search("ноутбук")

    assert result.count > 0
    assert result.meta.healthy is False
    shape_warnings = [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert shape_warnings == [
        "shape_drift: wb_search: missing products[].sizes[].price/products[].salePriceU/products[].priceU"
    ]


async def test_search_a_complete_payload_carries_no_shape_drift_warning(monkeypatch):
    payload = json.loads((FIXTURES / "search_v9_live.json").read_text(encoding="utf-8"))
    _patch_get_text(monkeypatch, {"v9/search": json.dumps(payload)})

    result = await server.wb_search("ноутбук")

    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_search_still_cries_drift_when_sizes_lost_their_price_keys(monkeypatch):
    """The boundary of the delisted page below: sizes entries that EXIST but
    carry no price key are a rename inside the array, so the price family is
    gone and the guard must stay loud."""
    payload = json.loads((FIXTURES / "search_v9_live.json").read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    for product in drifted["products"]:
        for size in product["sizes"]:
            size.pop("price", None)
    _patch_get_text(monkeypatch, {"v9/search": json.dumps(drifted)})

    result = await server.wb_search("ноутбук")

    assert result.count > 0
    shape_warnings = [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert shape_warnings == [
        "shape_drift: wb_search: missing products[].sizes[].price/products[].salePriceU/products[].priceU"
    ]


async def test_search_a_delisted_page_carries_no_shape_drift_warning(monkeypatch):
    """card/v4 answers an all-delisted page with "sizes": [] on every product —
    the state the legacy-fallback docstring describes. The sizes array is
    present but empty, so no element could have lost its price key: that is a
    data fact, and the no_prices warning — not a drift alarm — speaks for it."""
    payload = json.loads((FIXTURES / "search_v9_live.json").read_text(encoding="utf-8"))
    delisted = copy.deepcopy(payload)
    for product in delisted["products"]:
        product["sizes"] = []
        product.pop("salePriceU", None)
        product.pop("priceU", None)
    _patch_get_text(monkeypatch, {"v9/search": json.dumps(delisted)})

    result = await server.wb_search("ноутбук")

    assert result.count > 0
    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert any("no_prices" in w for w in result.meta.warnings)


async def test_selfcheck_cries_drift_when_a_required_family_vanishes(monkeypatch):
    """The canary must fail the same way the tool does when a family is lost."""
    payload = json.loads((FIXTURES / "search_v9_live.json").read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    for product in drifted["products"]:
        product.pop("sizes", None)
        product.pop("salePriceU", None)
        product.pop("priceU", None)
    card_body = json.dumps(
        {
            "products": [
                {
                    "id": server._SELFCHECK_NM,
                    "name": "x",
                    "sizes": [{"price": {"product": 10000}}],
                    "reviewRating": 4.8,
                    "feedbacks": 1,
                    "totalQuantity": 1,
                }
            ]
        }
    )
    reviews_body = json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]})
    _patch_get_text(
        monkeypatch,
        {
            "v9/search": json.dumps(drifted),
            "card.wb.ru": card_body,
            "feedbacks2.wb.ru": reviews_body,
            "search-goods.wildberries.ru": json.dumps([server._SELFCHECK_NM]),
            "wbbasket.ru": json.dumps({"imt_id": server._SELFCHECK_IMT}),
        },
    )

    result = await server.wb_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search_v9"]
    assert entry.state == "drift"
    assert entry.reason == "shape_drift"
    assert any(w.startswith("shape_drift: wb_search: missing products[].sizes[].price") for w in entry.notes)
