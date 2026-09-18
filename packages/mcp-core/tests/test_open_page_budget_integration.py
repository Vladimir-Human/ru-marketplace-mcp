"""open_page and the navigation budget, together — the integration the review named.

The budget's units are covered on their own, but nothing pinned the wiring: the
permit is taken before the navigation and released *before* the page is yielded,
because a retained challenge page lives for minutes and must not hold its host's
slot. If that release ever moves back after the yield, the second open of the same
host blocks forever, and this test turns that deadlock into a plain failure.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp_core.transport import chrome_cdp
from mcp_core.transport.cdp_budget import budget_snapshot

URL = "https://shop.test/search?q=a"
OTHER = "https://other.test/search?q=a"


@pytest.fixture
def fake_browser(monkeypatch):
    """A page object just real enough for open_page: it reads only ``.url``."""
    opened: list[str] = []

    def factory(url, wait_ms):
        @asynccontextmanager
        async def cm():
            opened.append(url)
            yield SimpleNamespace(url=url, closed=False)

        return cm()

    monkeypatch.setattr(chrome_cdp, "_playwright_page", factory)
    chrome_cdp.navigation_budget().reset()
    yield opened
    chrome_cdp.navigation_budget().reset()


async def test_two_pages_for_one_host_can_be_open_at_the_same_time(fake_browser):
    """The permit covers the navigation, not the page's lifetime."""

    async def both():
        async with chrome_cdp.open_page(URL) as first:
            async with chrome_cdp.open_page(URL) as second:
                assert first.url == second.url == URL

    await asyncio.wait_for(both(), timeout=5), "a held permit would deadlock here"
    assert len(fake_browser) == 2


async def test_the_host_slot_is_free_while_the_page_is_open(fake_browser):
    async with chrome_cdp.open_page(URL):
        assert budget_snapshot()["in_flight"] == 0, "an open page must not hold a slot"

    # peak_in_flight proves the budget was consulted on this path: it can only rise
    # above zero if open_page itself took a permit.
    assert budget_snapshot()["peak_in_flight"] == 1


async def test_a_navigation_that_succeeds_does_not_trip_the_breaker(fake_browser):
    for _ in range(3):
        async with chrome_cdp.open_page(URL):
            pass

    assert budget_snapshot()["hosts"]["shop.test"]["open"] is False


async def test_different_hosts_are_independent(fake_browser):
    async with chrome_cdp.open_page(URL):
        async with chrome_cdp.open_page(OTHER):
            pass

    assert set(budget_snapshot()["hosts"]) == {"shop.test", "other.test"}


async def test_a_non_http_url_is_refused_before_any_permit_is_taken(fake_browser):
    with pytest.raises(ValueError):
        async with chrome_cdp.open_page("file:///etc/passwd"):
            pass

    assert budget_snapshot()["hosts"] == {}
