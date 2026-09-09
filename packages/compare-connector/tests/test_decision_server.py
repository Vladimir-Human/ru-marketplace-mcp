"""Contract tests for the middle DSH profile."""

from __future__ import annotations

import pytest

from compare_connector import decision_server as server


@pytest.mark.asyncio
async def test_decision_profile_mounts_comparison_and_inspector():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert {"compare_prices", "compare_sources", "compare_verify_offer", "decision_inspect"} <= names


@pytest.mark.asyncio
async def test_decision_inspect_rejects_unknown_source():
    with pytest.raises(Exception, match="no supported card inspector"):
        await server.decision_inspect("unknown", "123")
