"""Requirement-derived browser scenarios over real HTTPS and real CDP.

START_MODULE_CONTRACT
PURPOSE: Prove S1/S2/S4/S9 without real marketplace requests or fabricated parser/browser replies.
SCOPE: Native public MCP searches and shared open_page deadlines, in Playwright and raw-CDP paths.
DEPENDS: FastMCP Client; mcp_core.transport.chrome_cdp; pytest; fixture_server; stdlib asyncio,json,urllib.parse.
LINKS: M-CDP-SIM, R-CDP-SIM-S1, R-CDP-SIM-S2, R-CDP-SIM-S4, R-CDP-SIM-S9.
END_MODULE_CONTRACT
START_MODULE_MAP
  search: Call the native registered tool through a real in-memory MCP session.
  test_s1_search: Read committed fixtures through Chrome and assert nonempty parsed prices/identities and real request URLs.
  test_s2_401_self_reload: A server-scripted 401 Refresh followed by 200 must succeed through dns_search.
  test_s4_429_fail_fast: A stalled 429 document must become a typed Citilink rate_limited answer within 12 seconds.
  test_s9_hang_timeout: A server that never sends headers must exhaust the real navigation deadline and close its tab.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastmcp import Client
from mcp_core.transport import chrome_cdp

from .chrome import assert_targets_settle
from .fixture_server import Reply

pytestmark = [pytest.mark.cdp, pytest.mark.cdp_sim]
QUERY = "ноутбук"

# START_BLOCK_PUBLIC_SEARCH
# Expected values come from the existing captured-fixture regression tests.
SEARCHES = {
    "ozon": ("www.ozon.ru", "/api/composer-api.bx/page/json/v2", "sku", "3497076095", "price", 39534.0),
    "dns": ("www.dns-shop.ru", "/search/", "product_id", "b7a1667f9b19ed20", "price_rub", 58999.0),
    "citilink": (
        "www.citilink.ru",
        "/search/",
        "product_id",
        "noutbuk-acer-gadget-erbook-air-ryzen-7-8745hs-16gb-ssd512gb-780m-14-ip-2147976",
        "price_rub",
        59990.0,
    ),
    "avito": ("www.avito.ru", "/web/1/js/items", "item_id", "8105179339", "price_rub", 69900.0),
}


async def search(module, source, *, raise_on_error=True):
    async with Client(module.mcp) as client:
        return await client.call_tool(f"{source}_search", {"query": QUERY}, raise_on_error=raise_on_error)


@pytest.mark.req("R-CDP-SIM-S1", "R-CDP-SIM-INFRA.isolation")
@pytest.mark.parametrize("source", list(SEARCHES))
async def test_s1_search(cdp_sim, connector, source):
    """S1: Native MCP search succeeds through the real browser and committed source fixture."""
    host, path, id_key, first_id, price_key, first_price = SEARCHES[source]
    reply = Reply(f"{source}_search")
    cdp_sim.server.route(host, path, reply)
    if source in {"ozon", "avito"}:
        # JSON fixtures suffice as same-origin bootstrap documents for in-page fetch;
        # no synthetic home-page markup or CDP evaluation result is needed.
        cdp_sim.server.route(host, "/", reply)
    loaded = connector(source)
    async with asyncio.timeout(35):
        result = await search(loaded.module, source)
    assert not result.is_error and result.structured_content is not None
    data = result.structured_content
    assert data["tier_used"] == "cdp"
    assert data["count"] == len(data["items"]) > 0
    assert str(data["items"][0][id_key]) == first_id
    assert data["items"][0][price_key] == first_price
    assert all(item["title"] for item in data["items"])
    requests = [entry for entry in cdp_sim.server.requests if entry.host == host and entry.path == path]
    assert len(requests) == 1 and requests[0].status == 200
    params = parse_qs(urlsplit(requests[0].target).query)
    if source == "ozon":
        params = parse_qs(urlsplit(params["url"][0]).query)
    assert params[{"dns": "q", "citilink": "text", "ozon": "text", "avito": "q"}[source]] == [QUERY]
    assert len(loaded.anonymous_calls) == (1 if source in {"ozon", "avito"} else 0)


@pytest.mark.req("R-CDP-SIM-S2", "R-CDP-NAV-TIMEOUT.antibot-recheck")
async def test_s2_401_self_reload(cdp_sim, connector):
    """S2: DNS's 401 main document self-reloads to 200; the waiting public call succeeds."""
    cdp_sim.server.route(
        "www.dns-shop.ru",
        "/search/",
        Reply("dns_search", status=401, headers=(("Refresh", "1"),)),
        Reply("dns_search"),
    )
    async with asyncio.timeout(30):
        result = await search(connector("dns").module, "dns")
    data = result.structured_content
    assert not result.is_error and data is not None
    assert data["tier_used"] == "cdp" and data["count"] == len(data["items"]) == 2
    assert data["items"][0]["price_rub"] == 58999.0
    requests = [
        entry for entry in cdp_sim.server.requests if entry.host == "www.dns-shop.ru" and entry.path == "/search/"
    ]
    assert [entry.status for entry in requests] == [401, 200]
    assert requests[0].target == requests[1].target, "the browser, not a second tool call, must reload the same URL"


# END_BLOCK_PUBLIC_SEARCH
# START_BLOCK_FAILURE_DEADLINES


@pytest.mark.req("R-CDP-SIM-S4", "R-CDP-NAV-TIMEOUT.qrator-429")
async def test_s4_429_fail_fast(cdp_sim, connector, request):
    """S4: A 429 whose body never finishes is refused before parsing/settling, within 12 seconds."""
    if cdp_sim.mode == "raw":
        request.applymarker(
            pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="G-CDP-SIM-RAW-STALLED-429-TAB: the raw-CDP tab stays open after a 429 whose body stalls",
            )
        )
    cdp_sim.server.route("www.citilink.ru", "/search/", Reply("citilink_search", status=429, stall_body=True))
    module = connector("citilink").module
    start = asyncio.get_running_loop().time()
    async with asyncio.timeout(12):
        result = await search(module, "citilink", raise_on_error=False)
    elapsed = asyncio.get_running_loop().time() - start
    assert elapsed < 12 and result.is_error
    error = json.loads(result.content[0].text)
    assert error["error"] == "rate_limited"
    assert error["status_code"] == 429 and error["retry_after_s"] == 30.0 and error["retryable"] is True
    requests = [
        entry for entry in cdp_sim.server.requests if entry.host == "www.citilink.ru" and entry.path == "/search/"
    ]
    assert len(requests) == 1 and requests[0].status == 429 and requests[0].stalled
    # The tab check runs here, not in the fixture, so the known raw-CDP leak is an xfail, not a teardown error.
    cdp_sim.targets_checked_by_test = True
    await assert_targets_settle(cdp_sim, cdp_sim.before)


@pytest.mark.req("R-CDP-SIM-S9")
async def test_s9_hang_timeout(cdp_sim):
    """S9: A real never-responding HTTPS origin hits the navigation budget, not the outer watchdog."""
    cdp_sim.server.route("www.citilink.ru", "/search/", Reply("citilink_search", hang=True))
    start = asyncio.get_running_loop().time()
    async with asyncio.timeout(12):
        with pytest.raises((chrome_cdp._PlaywrightTimeoutError, chrome_cdp.TransportDownError)) as caught:
            async with chrome_cdp.open_page(
                "https://www.citilink.ru/search/?text=hang",
                wait_ms=0,
                allowed_hosts={"www.citilink.ru"},
                navigation_timeout_ms=1000,
                wait_for_dom_parsed=True,
            ):
                pytest.fail("a hanging main document must never be yielded")
    assert asyncio.get_running_loop().time() - start < 12
    if isinstance(caught.value, chrome_cdp.TransportDownError):
        # The raw fallback deliberately wraps a timeout with its attach diagnosis.
        # A generic transport failure, parser error or watchdog expiry is not proof.
        assert isinstance(caught.value.__cause__, TimeoutError)
        assert "main document did not commit" in str(caught.value)
    else:
        assert "1000ms" in str(caught.value)
    requests = [
        entry for entry in cdp_sim.server.requests if entry.host == "www.citilink.ru" and entry.path == "/search/"
    ]
    assert requests and all(entry.status is None and entry.stalled for entry in requests)


# END_BLOCK_FAILURE_DEADLINES
