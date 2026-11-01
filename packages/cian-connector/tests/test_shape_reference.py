"""Variant-aware shape goldens for Cian sale, rent and daily offers.

The sale golden lives in ``cian_connector.shape_reference`` (measured on the
captured sale payload through the real ``_parse_offers`` parser); this file
asserts the registry still agrees with the fixture, pins the captured card
row's shape through ``_parse_card``, and proves the runtime shape-drift
warning fires through the public ``cian_search`` entry when a parser-critical
alias family vanishes from the raw envelope.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from cian_connector import server, shape_reference
from mcp_core.resilience import shape_signature

FIXTURES = Path(__file__).parent / "fixtures"
COMMON = {
    "items[].offer_id:int",
    "items[].price_rub:float",
    "items[].price_unit:str",
    "items[].title:str",
    "items[].url:str",
    "total_count:int",
}


def _signature(name: str) -> set[str]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    rows, total = server._parse_offers(payload)
    return set(shape_signature({"items": rows, "total_count": total}))


def test_sale_rent_daily_fixtures_preserve_common_offer_shape() -> None:
    for name in ("search_sale_live.json", "search_rent_live.json", "search_daily_live.json"):
        signature = _signature(name)
        assert signature >= COMMON, f"{name} lost common offer paths: {sorted(COMMON - signature)}"


def test_rent_golden_keeps_period_and_deposit_variants() -> None:
    signature = _signature("search_rent_live.json")

    assert signature >= {"items[].price_period:str", "items[].lease_term:str", "items[].deposit_rub:float"}


def test_daily_golden_keeps_explicit_day_unit() -> None:
    signature = _signature("search_daily_live.json")

    assert "items[].price_unit:str" in signature


def test_sale_rows_match_the_captured_reference() -> None:
    payload = json.loads((FIXTURES / "search_sale_live.json").read_text(encoding="utf-8"))
    rows, total = server._parse_offers(payload)

    assert shape_signature({"items": rows, "total_count": total}) == list(shape_reference.SEARCH_SHAPE_REFERENCE)


def test_card_row_matches_the_captured_reference() -> None:
    payload = json.loads((FIXTURES / "card_sale_live.json").read_text(encoding="utf-8"))
    row = server._parse_card(payload["offerData"])

    assert shape_signature(row) == list(shape_reference.CARD_SHAPE_REFERENCE)


def _patch_search(monkeypatch, result):
    async def fake(query, ctx):
        return result

    monkeypatch.setattr(server, "_fetch_search", fake)


def _patch_card(monkeypatch, result):
    async def fake(offer_id, ctx):
        return result

    monkeypatch.setattr(server, "_fetch_card", fake)


async def test_search_warns_when_a_required_key_family_vanishes(monkeypatch):
    """Offers that still parse after losing a whole alias family are structural
    drift the agent must see in the tool's own response, not only via doctor."""
    payload = json.loads((FIXTURES / "search_sale_live.json").read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    for offer in drifted["data"]["offersSerialized"]:
        terms = offer.get("bargainTerms") or {}
        terms.pop("priceRur", None)
        terms.pop("price", None)
        offer.pop("priceTotalRur", None)
        offer.pop("priceTotal", None)
    _patch_search(monkeypatch, (200, json.dumps(drifted, ensure_ascii=False), "cdp"))

    result = await server.cian_search(deal="sale", offer_type="flat")

    assert result.count == 3
    assert result.meta.healthy is False
    shape_warnings = [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert shape_warnings == [
        "shape_drift: cian_search: missing "
        "data.offersSerialized[].bargainTerms.priceRur/"
        "data.offersSerialized[].bargainTerms.price/"
        "data.offersSerialized[].priceTotalRur/"
        "data.offersSerialized[].priceTotal"
    ]


async def test_search_a_complete_payload_carries_no_shape_drift_warning(monkeypatch):
    payload = json.loads((FIXTURES / "search_sale_live.json").read_text(encoding="utf-8"))
    _patch_search(monkeypatch, (200, json.dumps(payload, ensure_ascii=False), "cdp"))

    result = await server.cian_search(deal="sale", offer_type="flat")

    assert result.meta.healthy is True
    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_selfcheck_cries_drift_when_a_required_family_vanishes(monkeypatch):
    """The canary must fail the same way the tool does when a family is lost."""
    payload = json.loads((FIXTURES / "search_sale_live.json").read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    for offer in drifted["data"]["offersSerialized"]:
        terms = offer.get("bargainTerms") or {}
        terms.pop("priceRur", None)
        terms.pop("price", None)
        offer.pop("priceTotalRur", None)
        offer.pop("priceTotal", None)
    card = json.loads((FIXTURES / "card_sale_live.json").read_text(encoding="utf-8"))
    card_body = json.dumps({"kind": "ok", "title": "t", "url": "u", "offerData": card["offerData"]})

    _patch_search(monkeypatch, (200, json.dumps(drifted, ensure_ascii=False), "cdp"))
    _patch_card(monkeypatch, (200, card_body, "cdp"))

    result = await server.cian_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search"]
    assert entry.state == "drift"
    assert "bargainTerms" in (entry.notes[0] if entry.notes else "")
