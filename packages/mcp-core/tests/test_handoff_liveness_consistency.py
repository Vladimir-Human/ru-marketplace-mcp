"""The handle issuer and the handle consumer must agree on what 'live' means.

``get_handoff_id`` checked only the lifetime deadline while ``snapshot_handoff``
used ``_expired`` (lifetime *or* idle), so a lease kept alive by its lifetime but
past its idle bound was handed a handle that the consumer refused as expired —
two answers to one question (independent review, 2026-09-18).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp_core.transport import browser_handoff as handoff
from mcp_core.transport import chrome_cdp

URL = "https://shop.test/search?q=a"


@pytest.fixture
async def browser(monkeypatch):
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", "900")
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_IDLE_S", "60")
    monkeypatch.setattr(chrome_cdp, "HEADLESS", False)

    @asynccontextmanager
    async def open_page(url, wait_ms=0, *, allowed_hosts=None):
        yield SimpleNamespace(url=url, closed=False, released=asyncio.Event())

    monkeypatch.setattr(chrome_cdp, "open_page", open_page)
    monkeypatch.setattr(chrome_cdp, "reveal_owned_page", AsyncMock(return_value=True))
    monkeypatch.setattr(chrome_cdp, "_hide_chrome_windows", lambda: None)
    yield
    await handoff.close_handoffs()


async def blocked(page):
    return {"challenge": "captcha"}


def _call():
    return handoff.read_with_handoff(
        url=URL,
        wait_ms=0,
        scope="session-1",
        operation="search",
        read=blocked,
        challenge=lambda payload: payload.get("challenge"),
    )


async def test_an_idle_expired_lease_is_not_handed_out(browser):
    await _call()
    lease = next(iter(handoff._leases.values()))
    handle = lease.handoff_id

    # Alive by lifetime, forgotten by idle: exactly the window the two functions
    # used to disagree about.
    lease.last_used = asyncio.get_running_loop().time() - 120
    assert lease.deadline > asyncio.get_running_loop().time(), "the lifetime has not run out"

    assert handoff.get_handoff_id(scope="session-1", operation="search", url=URL) is None

    with pytest.raises(Exception) as caught:
        await handoff.snapshot_handoff(scope="session-1", handoff_id=handle)
    assert "no longer retained" in str(caught.value), "the consumer must agree it is gone"


async def test_a_live_lease_is_still_handed_out(browser):
    await _call()

    assert handoff.get_handoff_id(scope="session-1", operation="search", url=URL) is not None
