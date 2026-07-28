"""Live smoke tests for the Wildberries connector.

These hit the real endpoint and are excluded from CI (``-m "not live"``): they
need network and a Russian-friendly IP. Run them on purpose after a drift
report, or from the nightly live job — never as part of the offline suite.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_wb_selfcheck_reaches_a_verdict():
    from wb_connector.server import wb_selfcheck

    result = await wb_selfcheck()

    assert result.status in ("success", "drift_detected", "inconclusive")
    assert result.checks, "selfcheck returned no sub-checks at all"


async def test_wb_search_returns_priced_items():
    from wb_connector.server import wb_search

    result = await wb_search(query="кроссовки мужские", page=1)

    items = getattr(result, "items", None) or []
    assert items, "live search returned zero items"
    priced = [it for it in items if getattr(it, "price_rub", None)]
    assert priced, "live search returned items but none had a price (the stale-index trap)"
