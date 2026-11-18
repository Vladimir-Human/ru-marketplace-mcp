"""Avito's search must not answer "CDP timeout" while it waits for a Chrome slot.

R-CDP-QUEUE-CREDIT (GitHub issue #330, 2026-11-18). In a full
``compare_prices`` fan-out about ten sources ask the operator's Chrome for a
navigation permit at once, and the budget serves three navigations at a time.
Avito's browser attempt was bounded by its own 20 s timeout, and the seconds
it spent *queueing* for a permit counted against that timeout: Avito (and
Ozon) answered "CDP timeout after 20.0s" while both read fine alone.

This test drives the public tool through the real budget: the permit is held
by another task for longer than Avito's timeout, and ``avito_search`` must
still return the fetched listings instead of the timeout message. The old code
fails here with exactly that message.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from avito_connector import server
from mcp_core.transport.cdp_budget import NavigationBudget

SEARCH_PAYLOAD = {
    "totalCount": 1,
    "items": [
        {
            "id": 4612345678,
            "title": "Ноутбук ThinkPad X1 Carbon",
            "price": 45500,
            "uri": "/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
            "location": "Москва",
        }
    ],
}

# Avito's own timeout, shortened so the test can hold the permit for longer
# than it. The queue wait below is 1.5x the timeout: longer than the attempt's
# budget, shorter than the twice-the-timeout the helper allows.
TIMEOUT_S = 0.5
HOLD_S = 1.5 * TIMEOUT_S


class _FakePage:
    """A page just real enough for the in-tab fetch: it answers one evaluate."""

    async def evaluate(self, script, args=None):
        return json.dumps({"status": 200, "text": json.dumps(SEARCH_PAYLOAD, ensure_ascii=False)})


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_search_completes_while_it_waits_for_a_navigation_permit(monkeypatch):
    """The queue wait must not be charged to Avito's own timeout."""
    budget = NavigationBudget(max_concurrent=1, per_host=1)

    @asynccontextmanager
    async def fake_open_page(url, wait_ms=0, *, allowed_hosts=None, **kwargs):
        # The real permit path: the budget decides when this tab may open.
        permit = await budget.acquire("www.avito.ru")
        try:
            permit.ok()
            yield _FakePage()
        finally:
            permit.release()

    async def no_wait():
        return None

    def refused_get(url, proxy=None):
        # Tier 1 is blocked, so the search falls through to the browser tier.
        return 403, "denied"

    monkeypatch.setattr(server, "TIMEOUT", TIMEOUT_S)
    monkeypatch.setattr(server, "open_page", fake_open_page)
    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "_sync_curl_get", refused_get)
    server._cache._data.clear()

    holding = asyncio.Event()

    async def holder():
        async with budget.slot("www.avito.ru") as slot:
            slot.ok()
            holding.set()
            await asyncio.sleep(HOLD_S)

    holder_task = asyncio.create_task(holder())
    await holding.wait()
    try:
        result = await server.avito_search("ноутбук")
    finally:
        await holder_task

    assert result.status == "success"
    assert result.count == 1
    assert result.items[0].price_rub == 45500
