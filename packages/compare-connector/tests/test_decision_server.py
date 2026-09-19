"""Contract tests for the middle DSH profile."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from compare_connector import decision_server as server
from compare_connector import server as compare


@pytest.mark.asyncio
async def test_decision_profile_mounts_comparison_and_inspector():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert {"compare_prices", "compare_sources", "compare_verify_offer", "decision_inspect"} <= names


@pytest.mark.asyncio
async def test_decision_inspect_rejects_unknown_source():
    with pytest.raises(Exception, match="no supported card inspector"):
        await server.decision_inspect("unknown", "123")


async def test_decision_inspect_accepts_canonical_detmir_url(monkeypatch):
    async def card(*, product_id):
        return {"product_id": product_id}

    monkeypatch.setattr(compare, "SOURCES", {"detsky_mir": SimpleNamespace(detmir_card=card)})
    result = await server.decision_inspect("detsky_mir", "https://www.detmir.ru/product/index/id/123/")
    assert result["card"]["product_id"] == 123


async def test_decision_inspect_rejects_stray_digits_in_wildberries_input(monkeypatch):
    async def card(*, nm_ids):
        return {"nm_ids": nm_ids}

    monkeypatch.setattr(compare, "SOURCES", {"wildberries": SimpleNamespace(wb_card=card)})
    with pytest.raises(Exception, match="positive numeric id"):
        await server.decision_inspect("wildberries", "item-123")
