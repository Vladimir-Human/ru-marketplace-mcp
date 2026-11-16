"""R-MEGAMARKET-ONE-LOAD: public-tool I/O with an owned-page fake, never Chrome."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from mcp_core.errors import ToolError
from mcp_core.transport.chrome_cdp import NavBlocked, NavigationPolicyError
from megamarket_connector import server

HOME = "https://megamarket.ru/"
CATEGORY = "https://megamarket.ru/catalog/telefony/"
CHECK = "https://megamarket.ru/xpvnsulc/"
ADDRESS = "/addressSuggestService/address/suggest"
PARSE = "/urlService/url/parse"
SEARCH = "/catalogService/catalog/search"
CARD = "/catalogService/productCardMainInfo/get"


class BrowserFake:
    """Count actual navigation seams; return only synthetic API envelopes.

    evaluate models fetch response metadata, not catalog parsing. A navigation
    exception models open_page's real NavBlocked without starting a browser.
    """

    def __init__(self, *, failure=None, status=403, final_url=HOME, retry_after=None, fallback=False, payload=None):
        self.failure = failure
        self.status = status
        self.final_url = final_url
        self.retry_after = retry_after
        self.fallback = fallback
        self.payload = payload
        self.raw = False
        self.loads = []
        self.tabs = []
        self.fetches = []
        self.closed = []
        self.waits = []

    @asynccontextmanager
    async def open_page(self, url, **kwargs):
        self.loads.append(url)
        if self.failure == "home":
            raise NavBlocked(self.status, self.final_url)
        if self.failure == "home_error":
            raise RuntimeError("synthetic browser failure")
        if self.failure == "home_timeout":
            raise TimeoutError("synthetic navigation timeout")
        page = RawPageFake(self, url) if self.raw else PageFake(self, url)
        self.tabs.append(page)
        try:
            yield page
        finally:
            self.closed.append(page)


class PageFake:
    def __init__(self, browser, url):
        self.browser = browser
        self.url = url

    async def evaluate(self, expression, arg=None):
        browser = self.browser
        assert server._cdp_lock.locked(), "every dependent fetch keeps the source lock"
        url = arg["url"]
        path = url.removeprefix(server.API_BASE) if url.startswith(server.API_BASE) else "catalog"
        browser.fetches.append((self, path, arg))
        refused = browser.failure == path
        if path == "catalog":
            return {
                "status": browser.status if refused else 200,
                "url": browser.final_url if refused else url if browser.fallback else CATEGORY,
                "retry_after": browser.retry_after,
            }
        payload = {
            ADDRESS: {"items": [{"addressId": "synthetic-address"}]},
            PARSE: {"params": {"collection": {"collectionId": "synthetic-collection"}}},
            SEARCH: {
                "total": 1,
                "items": [{"id": "1234567", "title": "Synthetic offer", "price": 123, "url": HOME + "product/1234567"}],
            },
            CARD: {"goods": {"title": "Synthetic card", "price": 123}},
        }[path]
        return {
            "status": browser.status if refused else 200,
            "text": (json.dumps(browser.payload) if browser.payload else "refused")
            if refused
            else json.dumps(browser.payload or payload),
            "url": browser.final_url if refused else url,
            "retry_after": browser.retry_after,
        }

    async def goto(self, url, **kwargs):
        self.browser.loads.append(url)
        self.url = self.browser.final_url if self.browser.failure == "navigation" else CATEGORY
        return ResponseFake(
            self.browser.status if self.browser.failure == "navigation" else 200, self.browser.retry_after
        )

    async def wait_for_timeout(self, milliseconds):
        pass


class RawPageFake(PageFake):
    """The raw-CDP navigation seam returns only status, not a response object."""

    async def goto_and_status(self, url):
        response = await self.goto(url)
        return response.status


class ResponseFake:
    def __init__(self, status, retry_after):
        self.status = status
        self.headers = {"retry-after": retry_after} if retry_after is not None else {}


@pytest.fixture
def browser(monkeypatch):
    fake = BrowserFake()
    server._cache._data.clear()
    server._address_cache.clear()
    server._search_params_cache.clear()
    server._pacer.reset()
    monkeypatch.setattr(server, "_address_id", None)
    monkeypatch.setattr(server, "_address_source", None)
    monkeypatch.setattr(server, "_address_resolved", False)
    monkeypatch.setattr(server, "_USE_PROFILE_ADDRESS", False)
    monkeypatch.setattr(server, "_min_gap", 0)
    monkeypatch.setattr(server, "open_page", fake.open_page)

    async def wait():
        fake.waits.append(True)

    monkeypatch.setattr(server, "_polite_wait", wait)
    return fake


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
@pytest.mark.parametrize("tool", ["search", "selfcheck"])
async def test_one_home_load_and_same_tab_fetches_preserve_caches(browser, tool):
    if tool == "search":
        result = await server.megamarket_search("phone")
        assert result.items[0].price_rub == 123
    else:
        result = await server.megamarket_selfcheck()
        assert result.status == "success"
        assert result.healthy is True
    assert browser.loads == [HOME]
    assert [path for _, path, _ in browser.fetches] == [ADDRESS, "catalog", PARSE, SEARCH]
    assert all(page is browser.tabs[0] for page, _, _ in browser.fetches)
    assert browser.fetches[2][2]["body"]["url"] == CATEGORY
    body = browser.fetches[3][2]["body"]
    assert body["addressId"] == "synthetic-address"
    assert body["collectionId"] == "synthetic-collection"
    assert all(arg["cap"] == server.MAX_BODY_BYTES for _, path, arg in browser.fetches if path != "catalog")
    assert len(browser.waits) >= len(browser.fetches)
    assert browser.closed == browser.tabs
    # All three caches must still avoid work on the identical second call.
    if tool == "search":
        await server.megamarket_search("phone")
    else:
        await server.megamarket_selfcheck()
    assert browser.loads == [HOME]
    assert len(browser.fetches) == 4
    await server.megamarket_card("1234567")
    assert browser.loads == [HOME, HOME], "card already needs just one home load"
    assert browser.closed == browser.tabs


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
@pytest.mark.parametrize("raw", [False, True])
async def test_hidden_redirect_navigates_only_the_existing_tab(browser, raw):
    browser.fallback = True
    browser.raw = raw
    await server.megamarket_search("phone")
    assert browser.loads == [HOME, "https://megamarket.ru/catalog/?q=phone"]
    assert len(browser.tabs) == 1
    assert all(page is browser.tabs[0] for page, _, _ in browser.fetches)
    assert browser.fetches[2][2]["body"]["url"] == CATEGORY
    assert browser.closed == browser.tabs


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
@pytest.mark.parametrize("status", [401, 403, 429])
@pytest.mark.parametrize("phase", ["home", ADDRESS, "catalog", PARSE, SEARCH, "navigation"])
async def test_refusal_stops_search_without_another_request(browser, status, phase):
    browser.failure = phase
    browser.status = status
    browser.fallback = phase == "navigation"
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("phone")
    error = json.loads(str(excinfo.value))
    assert error["error"] == "rate_limited"
    assert error["retry_after_s"] == 1200
    assert error["status_code"] == status
    assert "refused this browser after repeated page loads" in error["message"]
    assert "human step will not lift" in error["message"]
    assert "IP reputation" not in error["message"]
    assert "residential IP" not in error["message"]
    expected = {
        "home": [],
        ADDRESS: [ADDRESS],
        "catalog": [ADDRESS, "catalog"],
        PARSE: [ADDRESS, "catalog", PARSE],
        SEARCH: [ADDRESS, "catalog", PARSE, SEARCH],
        "navigation": [ADDRESS, "catalog"],
    }
    assert [path for _, path, _ in browser.fetches] == expected[phase]
    assert browser.closed == browser.tabs
    if phase in ("home", ADDRESS):
        assert server._address_cache == {}
    if phase != SEARCH:
        assert server._search_params_cache == {}


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
@pytest.mark.parametrize("phase", ["catalog", PARSE, "navigation"])
async def test_retry_after_survives_the_refused_step(browser, phase):
    browser.failure = phase
    browser.status = 429
    browser.retry_after = "87"
    browser.fallback = phase == "navigation"
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("phone")
    assert json.loads(str(excinfo.value))["retry_after_s"] == 87


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
@pytest.mark.parametrize("phase", ["home", "catalog", PARSE, "navigation"])
async def test_xpvnsulc_is_a_human_challenge_not_a_standing_refusal(browser, phase):
    browser.failure = phase
    browser.final_url = CHECK
    browser.fallback = phase == "navigation"
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("phone")
    error = json.loads(str(excinfo.value))
    assert error["error"] == "challenge_required"
    assert error["requires_user_action"] is True
    assert error["retryable_now"] is False
    assert "IP reputation" not in error["message"]
    assert SEARCH not in [path for _, path, _ in browser.fetches]
    assert browser.closed == browser.tabs


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
async def test_selfcheck_preserves_the_refusal_reason(browser):
    browser.failure = PARSE
    result = await server.megamarket_selfcheck()
    assert result.status == "inconclusive"
    assert result.checks["search"].reason == "rate_limited"
    assert SEARCH not in [path for _, path, _ in browser.fetches]


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"success": False, "errors": [{"code": 403}]}, "rate_limited"),
        ({"error": "Try disabling VPN", "code": 8}, "transport_down"),
    ],
)
async def test_only_explicit_code_seven_gets_ip_reputation_advice(browser, payload, expected):
    browser.payload = payload
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_card("1234567")
    error = json.loads(str(excinfo.value))
    assert error["error"] == expected
    assert "IP reputation" not in error["message"]
    assert "residential IP" not in error["message"]
    browser.payload = {"error": "ServicePipe refused", "code": 7}
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_card("1234567")
    error = json.loads(str(excinfo.value))
    assert error["error"] == "transport_down"
    assert "ServicePipe code 7" in error["message"]


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
async def test_code_seven_body_on_an_http_refusal_keeps_its_servicepipe_verdict(browser):
    browser.failure, browser.status = CARD, 403
    browser.payload = {"error": "Произошла ошибка. Попробуйте отключить VPN…", "code": 7}
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_card("1234567")
    error = json.loads(str(excinfo.value))
    assert error["error"] == "transport_down"
    assert "ServicePipe code 7" in error["message"]


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
async def test_redirect_fetch_cannot_feed_an_offsite_url_into_parse(browser):
    browser.failure = "catalog"
    browser.status = 200
    browser.final_url = "https://other.invalid/catalog/"
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("phone")
    assert "allowed host policy" in str(excinfo.value)
    assert PARSE not in [path for _, path, _ in browser.fetches]
    assert browser.closed == browser.tabs


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
@pytest.mark.parametrize("failure", ["home_error", "home_timeout"])
async def test_failed_home_load_is_not_reopened_or_deadlocked(browser, failure):
    browser.failure = failure
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("phone")
    assert json.loads(str(excinfo.value))["error"] == "transport_down"
    assert browser.loads == [HOME]
    assert browser.fetches == []
    assert not server._cdp_lock.locked()


@pytest.mark.req("R-MEGAMARKET-ONE-LOAD")
async def test_navigation_policy_failure_is_not_degraded_into_a_search(browser, monkeypatch):
    browser.fallback = True

    async def blocked_navigation(page, url, **kwargs):
        raise NavigationPolicyError("https://other.invalid/", {"megamarket.ru"})

    monkeypatch.setattr(PageFake, "goto", blocked_navigation)
    with pytest.raises(ToolError) as excinfo:
        await server.megamarket_search("phone")
    assert "allowed host policy" in str(excinfo.value)
    assert PARSE not in [path for _, path, _ in browser.fetches]
