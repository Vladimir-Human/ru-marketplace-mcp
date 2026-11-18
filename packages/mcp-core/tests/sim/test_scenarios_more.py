"""Requirement-derived S3/S5/S6/S7/S8/S10 over real loopback HTTPS/CDP.

START_MODULE_CONTRACT
PURPOSE: Exercise reloads, signal-held challenges, host guards, real cooldowns and exact tab ownership/recovery.
SCOPE: Public DNS MCP calls; direct open_page only for S10's transport lifecycle; both real browser paths.
DEPENDS: FastMCP; pytest; mcp_core.pacing,transport; sibling fixture_server,observe,test_scenarios; stdlib.
LINKS: M-CDP-SIM, R-CDP-SIM-S3, R-CDP-SIM-S5, R-CDP-SIM-S6, R-CDP-SIM-S7, R-CDP-SIM-S8, R-CDP-SIM-S10.
END_MODULE_CONTRACT
START_MODULE_MAP
  test_s3_403_self_reload: One public search survives a real 403 Refresh and returns captured products.
  test_s5_signal_held_challenge: The waiting call survives a committed refusal until the origin's success is signalled.
  test_s6_cross_host_redirect: Refuse a foreign redirect before the foreign host is requested.
  test_s6_foreign_url_opens_no_tab: Reject a foreign URL with a valid product path before creating any target.
  test_s7_ten_refusals_open_breaker: Ten actual refused navigations open the shared breaker; later calls send no request.
  test_s8_foreign_tab_untouched: An independently owned tab never navigates/closes or changes identity, URL or title.
  test_s10_closed_target_between_calls: Close the first owned transport target externally; the next tool call is bounded.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastmcp import Client
from mcp_core.pacing import Pacer
from mcp_core.transport import cdp_budget, chrome_cdp

from .fixture_server import Reply
from .observe import browser_channel, owned_tab, target_info
from .test_scenarios import QUERY, search

pytestmark = [pytest.mark.cdp, pytest.mark.cdp_sim]
HOST = "www.dns-shop.ru"

# START_BLOCK_RELOAD_AND_CHALLENGE


def assert_dns_results(result):
    assert not result.is_error, "the waiting public search must succeed, not return a refusal"
    data = result.structured_content
    assert data is not None and data["tier_used"] == "cdp"
    assert data["count"] == len(data["items"]) == 2
    assert data["items"][0]["product_id"] == "b7a1667f9b19ed20"
    assert data["items"][0]["price_rub"] == 58999.0


def search_requests(cdp_sim):
    return [entry for entry in cdp_sim.server.requests if entry.host == HOST and entry.path == "/search/"]


@pytest.mark.req("R-CDP-SIM-S3")
async def test_s3_403_self_reload(cdp_sim, connector):
    """S3: A 403 document reloads to 200 within one public MCP call, in both paths."""
    cdp_sim.server.route(HOST, "/search/", Reply("refusal", 403, (("Refresh", "1"),)), Reply("dns_search"))
    async with asyncio.timeout(25):
        result = await search(connector("dns").module, "dns", raise_on_error=False)
    assert_dns_results(result)
    requests = search_requests(cdp_sim)
    assert [entry.status for entry in requests] == [403, 200]
    assert [entry.fixture for entry in requests] == ["refusal", "dns_search"]
    assert requests[0].target == requests[1].target, "Chrome must reload, not a second tool call"


@pytest.mark.req("R-CDP-SIM-S5.waiting")
async def test_s5_signal_held_challenge(cdp_sim, connector, monkeypatch):
    """S5: Signal the origin only while a committed refusal still holds the waiting call."""
    handoff_s = 20
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", str(handoff_s))
    arrived, clear = threading.Event(), threading.Event()
    cdp_sim.server.route(
        HOST,
        "/search/",
        Reply("refusal", 403, (("Refresh", "1"),)),
        Reply("dns_search", wait_for=clear, arrived=arrived),
    )
    loop = asyncio.get_running_loop()
    start = loop.time()
    async with browser_channel(cdp_sim) as browser:
        await browser.send("Target.getTargets")
        browser.events.clear()
        call = asyncio.create_task(search(connector("dns").module, "dns", raise_on_error=False))
        try:
            async with asyncio.timeout(handoff_s):
                assert await asyncio.to_thread(arrived.wait, 5), "the challenge must request its same-URL reload"
                assert not call.done(), "the challenged tool must still be waiting before the clear signal"
                targets = (await browser.send("Target.getTargets"))["targetInfos"]
                held = [
                    target
                    for target in targets
                    if target["type"] == "page" and target["targetId"] not in cdp_sim.before
                ]
                assert len(held) == 1
                target_id = held[0]["targetId"]
                snapshot = await target_info(browser, target_id)
                assert snapshot["title"] == "Запрос отклонен"
                assert snapshot["url"].startswith(f"https://{HOST}/search/?q=")
                assert loop.time() - start < handoff_s
                clear.set()  # The origin, not an extractor/browser mock, now emits its committed 200 fixture.
                result = await call
            assert_dns_results(result)
            requests = search_requests(cdp_sim)
            assert [entry.status for entry in requests] == [403, 200]
            assert requests[0].target == requests[1].target
            created = [
                event["params"]["targetInfo"] for event in browser.events if event["method"] == "Target.targetCreated"
            ]
            assert [target["targetId"] for target in created if target["type"] == "page"] == [target_id]
        finally:
            clear.set()
            if not call.done():
                call.cancel()
            await asyncio.gather(call, return_exceptions=True)
    # This checks the waiting navigation, not browser_handoff's headed-only
    # lease/resume API: that separate limit is explicitly recorded in GRACE.


# END_BLOCK_RELOAD_AND_CHALLENGE
# START_BLOCK_HOST_GUARDS


@pytest.mark.req("R-CDP-SIM-S6.redirect")
async def test_s6_cross_host_redirect(cdp_sim, connector):
    """S6: A foreign redirect is refused before its otherwise valid DNS tiles can become results."""
    destination = "https://foreign.invalid/search/"
    cdp_sim.server.route(HOST, "/search/", Reply("dns_search", 302, (("Location", destination),)))
    cdp_sim.server.route("foreign.invalid", "/search/", Reply("dns_search"))
    async with asyncio.timeout(25):
        result = await search(connector("dns").module, "dns", raise_on_error=False)
    assert result.is_error, "off-host product fixtures must never be returned as a successful search"
    error = json.loads(result.content[0].text)
    assert error["error"] == "transport_down"
    assert "allowed host policy" in error["message"]
    requests = search_requests(cdp_sim)
    assert len(requests) == 1 and requests[0].status == 302
    foreign = [
        entry for entry in cdp_sim.server.requests if entry.host == "foreign.invalid" and entry.path == "/search/"
    ]
    assert foreign == [], "the foreign host must receive no request at all (R-CDP-REDIRECT-GUARD)"
    state = cdp_budget.navigation_budget().snapshot()["hosts"].get(HOST)
    assert state is None or (state["refusals"] == 0 and not state["open"]), (
        "our host-policy refusal must not blame the source"
    )


@pytest.mark.req("R-CDP-SIM-S6.input")
async def test_s6_foreign_url_opens_no_tab(cdp_sim, connector):
    """S6: A valid-looking card path on a foreign host fails before even transient tab creation."""
    cdp_sim.server.route("foreign.invalid", "/product/b7a1667f9b19ed20/", Reply("dns_search"))
    async with browser_channel(cdp_sim) as browser:
        # Drain the initial target discovery before measuring newly created targets.
        await browser.send("Target.getTargets")
        browser.events.clear()
        async with asyncio.timeout(5):
            async with Client(connector("dns").module.mcp) as client:
                result = await client.call_tool(
                    "dns_card",
                    {"product_url": "https://foreign.invalid/product/b7a1667f9b19ed20/"},
                    raise_on_error=False,
                )
        await browser.send("Target.getTargets")
        assert result.is_error
        assert json.loads(result.content[0].text)["error"] == "bad_request"
        assert not [event for event in browser.events if event["method"] == "Target.targetCreated"], (
            "input rejection must open no tab"
        )
        assert await cdp_sim.targets() == cdp_sim.before
        assert cdp_sim.server.requests == (), "foreign tool input must cause no origin request"
        assert cdp_budget.navigation_budget().snapshot()["hosts"] == {}


# END_BLOCK_HOST_GUARDS
# START_BLOCK_REAL_BREAKER


@pytest.mark.req("R-CDP-SIM-S7")
async def test_s7_ten_refusals_open_breaker(cdp_sim, connector, monkeypatch):
    """S7: Ten real main-document refusals open the real shared cooldown and stop further HTTP traffic."""
    # A whole-body 429 is a committed refusal without 401/403's ten-second reload
    # window. This exercises ten real navigations rather than seeding counters.
    cdp_sim.server.route(HOST, "/search/", Reply("refusal", 429))
    module = connector("dns").module
    # Keep the real Pacer/clock/sleep and breaker. Zero only this local fixture
    # source's configurable pacing gap so ten attaches fit the 30-second budget.
    monkeypatch.setattr(module, "_min_gap", 0)
    monkeypatch.setattr(module, "_pacer", Pacer(0))
    budget = cdp_budget.navigation_budget()
    assert budget.breaker_threshold == 10 and budget.breaker_cooldown_s == 60
    async with asyncio.timeout(30):
        async with Client(module.mcp) as client:
            for index in range(10):
                result = await client.call_tool("dns_search", {"query": QUERY}, raise_on_error=False)
                assert result.is_error
                assert json.loads(result.content[0].text)["error"] == "transport_down"
                assert len(search_requests(cdp_sim)) == index + 1
            state = budget.snapshot()["hosts"][HOST]
            assert state["refusals"] == 10 and state["open"] and state["opens"] == 1
            before = cdp_sim.server.requests
            pauses = []
            async with browser_channel(cdp_sim) as browser:
                await browser.send("Target.getTargets")
                browser.events.clear()
                for _ in range(2):
                    result = await client.call_tool("dns_search", {"query": QUERY}, raise_on_error=False)
                    assert result.is_error
                    error = json.loads(result.content[0].text)
                    assert error["error"] == "rate_limited" and error["retryable"] is True
                    assert 0 < error["retry_after_s"] <= 60
                    pauses.append(error["retry_after_s"])
                    assert cdp_sim.server.requests == before, "the open cooldown must send no further request"
                await browser.send("Target.getTargets")
                assert not [event for event in browser.events if event["method"] == "Target.targetCreated"]
            assert pauses[1] <= pauses[0]
    assert [entry.status for entry in search_requests(cdp_sim)] == [429] * 10


# END_BLOCK_REAL_BREAKER
# START_BLOCK_TARGET_OWNERSHIP_AND_RECOVERY


@pytest.mark.req("R-CDP-SIM-S8")
@pytest.mark.parametrize("tab_host", ["foreign.invalid", HOST])
async def test_s8_foreign_tab_untouched(cdp_sim, connector, tab_host):
    """S8: An external tab, even on the same host, keeps identity/title/URL and emits no navigation or close."""
    url = f"https://{tab_host}/s8-test-owned"
    cdp_sim.server.route(tab_host, "/s8-test-owned", Reply("dns_search"))
    cdp_sim.server.route(HOST, "/search/", Reply("dns_search"))
    async with browser_channel(cdp_sim) as browser:
        async with owned_tab(cdp_sim, browser, url) as (target_id, page, before):
            assert before["title"] == "Ноутбуки — купить ноутбук в интернет-магазине DNS"
            await page.send("Page.getFrameTree")
            page.events.clear()
            browser.events.clear()
            async with asyncio.timeout(25):
                result = await search(connector("dns").module, "dns", raise_on_error=False)
            assert_dns_results(result)
            after = await target_info(browser, target_id)
            await page.send("Page.getFrameTree")
            assert after == before, "the foreign tab's target id, URL and title must stay unchanged"
            assert not [
                event
                for event in page.events
                if event["method"]
                in {
                    "Page.frameNavigated",
                    "Page.navigatedWithinDocument",
                    "Page.frameStartedLoading",
                    "Network.requestWillBeSent",
                    "Inspector.detached",
                    "Inspector.targetCrashed",
                }
            ], "the foreign tab must never navigate/reload, even if it later returns to the original URL"
            assert not [
                event
                for event in browser.events
                if event["method"] == "Target.targetDestroyed" and event["params"]["targetId"] == target_id
            ], "the foreign tab must never be closed"
            own_requests = [
                entry for entry in cdp_sim.server.requests if entry.host == tab_host and entry.path == "/s8-test-owned"
            ]
            assert len(own_requests) == 1 and own_requests[0].status == 200
            assert await cdp_sim.targets() == cdp_sim.before | {target_id}


@pytest.mark.req("R-CDP-SIM-S10.closed-target")
async def test_s10_closed_target_between_calls(cdp_sim, connector):
    """S10: Externally close a successfully read owned target; the next real public search never hangs."""
    first_url = f"https://{HOST}/s10-first"
    cdp_sim.server.route(HOST, "/s10-first", Reply("dns_search"))
    cdp_sim.server.route(HOST, "/search/", Reply("dns_search"))
    async with browser_channel(cdp_sim) as browser:
        async with asyncio.timeout(25):
            # open_page creates fresh tabs, not a reusable pool. Keep the first
            # successfully returned context alive to exercise external target
            # loss without a fake that suppresses the transport's normal cleanup.
            async with chrome_cdp.open_page(
                first_url, wait_ms=0, allowed_hosts={HOST}, navigation_timeout_ms=3000
            ) as page:
                assert (
                    await page.evaluate("() => document.title") == "Ноутбуки — купить ноутбук в интернет-магазине DNS"
                )
                targets = (await browser.send("Target.getTargets"))["targetInfos"]
                owned = [
                    target["targetId"] for target in targets if target["type"] == "page" and target["url"] == first_url
                ]
                assert len(owned) == 1 and owned[0] not in cdp_sim.before
                assert (await browser.send("Target.closeTarget", {"targetId": owned[0]}))["success"]
                async with asyncio.timeout(5):
                    while owned[0] in await cdp_sim.targets():
                        await asyncio.sleep(0.05)
                start = asyncio.get_running_loop().time()
                async with asyncio.timeout(15):
                    result = await search(connector("dns").module, "dns", raise_on_error=False)
                assert asyncio.get_running_loop().time() - start < 15
                if result.is_error:
                    error = json.loads(result.content[0].text)
                    assert error["error"] in {"timeout", "transport_down"} and error["retryable"] is True
                else:
                    assert_dns_results(result)
                    assert [entry.status for entry in search_requests(cdp_sim)] == [200]
                assert owned[0] not in await cdp_sim.targets(), "the discarded target must not be reused/resurrected"
    # Retained headed-only challenge targets have a distinct unproven recovery
    # case; this test does not manufacture reuse that open_page does not have.


# END_BLOCK_TARGET_OWNERSHIP_AND_RECOVERY
