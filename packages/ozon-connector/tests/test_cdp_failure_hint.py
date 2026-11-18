"""Ozon's all-tiers-failed error names the next step the failed tier calls for (audit cycle 2, 2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove R-OZON-CDP-FAILURE-HINT through the public ozon_search and ozon_card tools: a timeout or failure
         inside a reachable Chrome never says "Start Chrome"; an unreachable browser still does.
SCOPE: ozon_search, ozon_card with _fetch_composer stubbed to return status 0 and a tier.
DEPENDS: pytest; fastmcp ToolError; ozon_connector.server.
LINKS: M-OZON; R-OZON-CDP-FAILURE-HINT.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp.exceptions import ToolError
from ozon_connector import server


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, body: str, tier: str) -> None:
    async def fetch(path, ctx):
        return 0, body, tier

    monkeypatch.setattr(server, "_fetch_composer", fetch)


def _message(call) -> str:
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(call())
    return str(excinfo.value)


@pytest.mark.req("R-OZON-CDP-FAILURE-HINT")
@pytest.mark.parametrize(
    "call",
    [lambda: server.ozon_search("iphone 16 128gb"), lambda: server.ozon_card("1234567")],
    ids=["search", "card"],
)
def test_a_timeout_inside_a_reachable_chrome_does_not_say_start_chrome(monkeypatch, call) -> None:
    # The cycle-2 compare: Chrome was up, the Ozon tab did not answer within the limit.
    _stub_fetch(monkeypatch, "CDP timeout after 20.0s", "cdp")

    message = _message(call)

    assert "transport_down" in message
    assert "Start Chrome" not in message
    assert "Chrome is running" in message
    assert "CDP timeout after 20.0s" in message


@pytest.mark.req("R-OZON-CDP-FAILURE-HINT")
def test_an_unreachable_browser_still_says_start_chrome(monkeypatch) -> None:
    _stub_fetch(monkeypatch, "connect ECONNREFUSED 127.0.0.1:9222", "cdp_failed")

    message = _message(lambda: server.ozon_search("iphone 16 128gb"))

    assert "Start Chrome" in message
    assert "Chrome is running" not in message
