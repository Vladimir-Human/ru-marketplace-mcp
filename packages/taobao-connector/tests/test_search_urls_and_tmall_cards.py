"""Offline regressions from the owner's Taobao brief dated 2026-10-30."""
# START_MODULE_CONTRACT
# PURPOSE: Prove canonical search queries and explicit unsupported Tmall card errors.
# SCOPE: Public tools with fake rendering/navigation; no Chrome or network.
# DEPENDS: pytest; mcp_core cache/errors/CDP; taobao_connector.server; captured provenance.
# LINKS: M-TAOBAO; R-TAOBAO-QUERY; R-TAOBAO-TMALL-CARD; R-INPUT-TRUST.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_search_query_encoding_and_cache_key: Preserve spaces/literal plus signs and reuse canonical keys.
# test_card_refuses_tmall_url_without_navigation: Explain unsupported cards before browser access.
# test_card_redirect_refusal_preserves_host_policy: Translate only Tmall redirects, without another navigation.
# END_MODULE_MAP

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from mcp_core.cache import TTLCache
from mcp_core.transport.chrome_cdp import NavigationPolicyError
from taobao_connector import server

PROVENANCE = json.loads(
    (Path(__file__).parent / "fixtures" / "search_grid_iphone15_live.provenance.json").read_text(encoding="utf-8")
)
CATALOG = {
    "title": "iphone 15_淘宝搜索",
    "items": [it for it in PROVENANCE["ground_truth"]["items"] if it["kind"] != "simba_ad"],
}


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=300))

    async def no_wait():
        pass

    monkeypatch.setattr(server, "_polite_wait", no_wait)


# START_BLOCK_QUERY_ENCODING
@pytest.mark.parametrize(
    ("query", "encoded"),
    [("iphone 15", "iphone%2015"), ("iphone+15", "iphone%2B15")],
    ids=["space", "literal_plus"],
)
async def test_search_query_encoding_and_cache_key(monkeypatch, query, encoded):
    """R-TAOBAO-QUERY: the navigated URL and reused cache key preserve query text."""
    navigations = []

    async def render(url, extract_js, wait_ms, ctx):
        navigations.append(url)
        return CATALOG

    monkeypatch.setattr(server, "_cdp_render", render)
    first = await server.taobao_search(query, page=2)
    cached = await server.taobao_search(f" {query} ", page=2)
    expected_url = f"https://s.taobao.com/search?q={encoded}&page=2"
    assert navigations == [expected_url]
    assert "+" not in expected_url
    assert first.tier_used == "cdp"
    assert cached.tier_used == "cache"
    assert server._cache.get(expected_url) == CATALOG
    if " " in query:
        assert server._cache.get("https://s.taobao.com/search?q=iphone+15&page=2") is None


# END_BLOCK_QUERY_ENCODING


# START_BLOCK_TMALL_CARD_REFUSAL
@pytest.mark.parametrize(
    "url",
    [
        "https://detail.tmall.com/item.htm?id=1096307293333",
        "//detail.tmall.com/item.htm?id=1096307293333",
    ],
    ids=["https", "scheme_relative"],
)
async def test_card_refuses_tmall_url_without_navigation(monkeypatch, url):
    """R-TAOBAO-TMALL-CARD: a known unsupported host needs an actionable, non-retryable refusal."""
    navigations = []

    async def render(url, extract_js, wait_ms, ctx):
        navigations.append(url)
        raise AssertionError("An explicit Tmall card URL must never navigate")

    monkeypatch.setattr(server, "_cdp_render", render)
    with pytest.raises(ToolError) as excinfo:
        await server.taobao_card(url)
    error = json.loads(str(excinfo.value))
    assert error["error"] == "bad_request"
    assert error["retryable"] is False
    assert "Tmall listings are not readable by taobao_card" in error["message"]
    assert "title, price, shop and URL" in error["message"]
    assert navigations == []
    assert len(server._cache) == 0


@pytest.mark.parametrize(
    ("final_url", "expected_error"),
    [
        ("https://detail.tmall.com/item.htm?id=1096307293333", "bad_request"),
        ("https://detail.tmall.com.evil.invalid/item.htm?id=1096307293333", "transport_down"),
    ],
    ids=["tmall", "unrelated_host"],
)
async def test_card_redirect_refusal_preserves_host_policy(monkeypatch, final_url, expected_error):
    """R-TAOBAO-TMALL-CARD, R-INPUT-TRUST: translate the refused host, never allow or retry it."""
    navigations = []

    @asynccontextmanager
    async def refused_page(url, wait_ms):
        navigations.append(url)
        if final_url:
            raise NavigationPolicyError(final_url, {"item.taobao.com"})
        yield None

    monkeypatch.setattr(server, "open_page", refused_page)
    with pytest.raises(ToolError) as excinfo:
        await server.taobao_card("1096307293333")
    error = json.loads(str(excinfo.value))
    assert error["error"] == expected_error
    assert error["retryable"] is (expected_error == "transport_down")
    if expected_error == "bad_request":
        assert "Tmall listings are not readable by taobao_card" in error["message"]
        assert "title, price, shop and URL" in error["message"]
    assert navigations == ["https://item.taobao.com/item.htm?id=1096307293333"]
    assert len(server._cache) == 0


# END_BLOCK_TMALL_CARD_REFUSAL
