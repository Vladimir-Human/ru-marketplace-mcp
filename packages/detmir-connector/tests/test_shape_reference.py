"""Golden shape checks for normalized Detsky Mir fixture payloads.

The goldens live in ``detmir_connector.shape_reference`` — measured by running
the real ``_parse_product`` parser over the captured category and card
fixtures — and this file asserts the registry still agrees with the fixtures,
so it cannot go stale silently. The drift half proves the runtime
shape-drift warning fires through the public ``detmir_category`` entry when a
parser-critical alias family vanishes from the raw items.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from detmir_connector import server, shape_reference
from mcp_core.resilience import shape_signature

FIXTURES = Path(__file__).parent / "fixtures"

CATEGORY_PAYLOAD = json.loads((FIXTURES / "category_live.json").read_text(encoding="utf-8"))


def test_card_normalization_matches_shape_golden() -> None:
    payload = json.loads((FIXTURES / "card_live.json").read_text(encoding="utf-8"))
    node = server._product_node(payload)
    assert node is not None
    product = server._parse_product(node)

    assert shape_signature(product.model_dump()) == list(shape_reference.CARD_SHAPE_REFERENCE)


def test_category_normalization_matches_shape_golden() -> None:
    products = [server._parse_product(item) for item in CATEGORY_PAYLOAD["items"]]

    assert shape_signature({"items": [p.model_dump() for p in products]}) == list(
        shape_reference.SEARCH_SHAPE_REFERENCE
    )


def _stub_json(monkeypatch, payload: object) -> None:
    """Route every ``_fetch_json`` read to one canned payload."""

    async def fake_fetch(url: str, label: str, ctx=None):
        return payload

    monkeypatch.setattr(server, "_fetch_json", fake_fetch)


async def test_category_warns_when_a_required_key_family_vanishes(monkeypatch):
    """Products that still parse after losing a whole alias family are
    structural drift the agent must see in the tool's own response."""
    drifted = copy.deepcopy(CATEGORY_PAYLOAD)
    for item in drifted["items"]:
        item.pop("price", None)
        item.pop("prices", None)
        item.pop("final_price", None)
    _stub_json(monkeypatch, drifted)

    result = await server.detmir_category(alias="pups")

    assert result.returned == 3
    assert result.meta.healthy is False
    shape_warnings = [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert shape_warnings == [
        "shape_drift: detmir_category: missing "
        "items[].price/items[].prices.sale/items[].prices.current/items[].final_price"
    ]


async def test_category_a_complete_payload_carries_no_shape_drift_warning(monkeypatch):
    _stub_json(monkeypatch, copy.deepcopy(CATEGORY_PAYLOAD))

    result = await server.detmir_category(alias="pups")

    assert result.meta.healthy is True
    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_selfcheck_cries_drift_when_a_required_family_vanishes(monkeypatch):
    """The canary must fail the same way the tool does when a family is lost."""
    drifted = copy.deepcopy(CATEGORY_PAYLOAD)
    for item in drifted["items"]:
        item.pop("price", None)
        item.pop("prices", None)
        item.pop("final_price", None)
    card_payload = json.loads((FIXTURES / "card_live.json").read_text(encoding="utf-8"))
    categories_payload = {
        "data": [
            {
                "id": 45909,
                "alias": "pups",
                "title": "Пупсы",
                "level": 1,
                "products_count": 213009,
                "parentId": None,
            }
        ],
        "meta": {"total": 1, "offset": 0, "limit": 30},
    }

    async def fake_fetch(url: str, label: str, ctx=None):
        if "/v2/products/" in url:
            return card_payload
        if "/v4/products" in url:
            return drifted
        return categories_payload

    monkeypatch.setattr(server, "_fetch_json", fake_fetch)

    result = await server.detmir_selfcheck()

    assert result.status == "drift_detected"
    assert result.checks["category"].state == "drift"
    assert result.checks["card"].state == "healthy"
    assert result.checks["categories"].state == "healthy"
