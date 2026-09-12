"""Variant-aware shape goldens for Cian sale, rent and daily offers."""

from __future__ import annotations

import json
from pathlib import Path

from cian_connector import server
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
