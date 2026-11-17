"""The compare_prices guide tells the agent to verify the winner before quoting it.

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-VERIFY-BEFORE-QUOTE: the served compare_prices guide names compare_verify_offer on the
         winning row before its price is quoted, and identity_verification before the same model is claimed.
SCOPE: The guide resource the compare server serves; no fetch.
DEPENDS: pytest; fastmcp Client; compare_connector.server; mcp_core.resources.
LINKS: M-COMPARE; M-RESOURCES; R-COMPARE-VERIFY-BEFORE-QUOTE; plan item P1-6 (2026-11-17).
END_MODULE_CONTRACT
"""

from __future__ import annotations

import pytest
from compare_connector import server
from fastmcp import Client
from mcp_core.resources import guide_uri


@pytest.mark.req("R-COMPARE-VERIFY-BEFORE-QUOTE")
async def test_the_served_guide_says_verify_the_winner_before_quoting_it() -> None:
    async with Client(server.mcp) as client:
        guide = (await client.read_resource(guide_uri("compare_prices")))[0].text
    text = " ".join(guide.split())

    assert "Before quoting the winner's price or calling it the cheapest, call compare_verify_offer on that row" in text
    assert "read identity_verification before saying it is" in text
