"""Offline tests for the Yandex Market connector's tool layer.

Page fetches are monkeypatched to serve the trimmed real fixtures, so the suite
needs no network and no Russian-friendly IP.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from yandex_connector import server

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def clear_cache():
    server._cache.clear()
    yield
    server._cache.clear()


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    """Remove the politeness gap so tests do not actually wait 1.5s per call."""
    monkeypatch.setattr(server._limiter, "min_gap_s", 0.0)


def stub_html(monkeypatch, mapping: dict[str, str]):
    """Serve canned HTML from ``_fetch_html``, matched by URL substring."""

    async def fake_fetch(url: str, label: str, ctx=None) -> str:
        for needle, html in mapping.items():
            if needle in url:
                return html
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(server, "_fetch_html", fake_fetch)


def error_payload(err: ToolError) -> dict:
    return json.loads(str(err))


# ------------------------------------------------------------------ basics ----


def test_server_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == server.SERVER_VERSION


async def test_registered_tools_are_stable():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert names == {"yandex_search", "yandex_card", "yandex_selfcheck"}


# ------------------------------------------------------------------ search ----


async def test_search_returns_products_with_both_prices(monkeypatch):
    stub_html(monkeypatch, {"/search": load("search_washer.html")})

    result = await server.yandex_search(query="стиральная машина")

    assert result.returned == 3
    assert result.total_available == 1434
    assert result.meta.extraction == "ssr"
    first = result.items[0]
    assert first.brand == "Tuvio"
    assert first.price_rub == 22600.0
    assert first.price_with_plus == 16222.0


async def test_search_honours_the_limit(monkeypatch):
    stub_html(monkeypatch, {"/search": load("search_washer.html")})

    result = await server.yandex_search(query="стиральная машина", limit=2)

    assert result.returned == 2


async def test_search_builds_a_page_parameter_only_beyond_page_one(monkeypatch):
    seen: list[str] = []

    async def capture(url: str, label: str, ctx=None) -> str:
        seen.append(url)
        return load("search_washer.html")

    monkeypatch.setattr(server, "_fetch_html", capture)

    await server.yandex_search(query="тест", page=1)
    assert "page=" not in seen[-1]

    await server.yandex_search(query="тест", page=3)
    assert "page=3" in seen[-1]


async def test_search_percent_encodes_cyrillic_queries(monkeypatch):
    seen: list[str] = []

    async def capture(url: str, label: str, ctx=None) -> str:
        seen.append(url)
        return load("search_washer.html")

    monkeypatch.setattr(server, "_fetch_html", capture)

    await server.yandex_search(query="стиральная машина")

    assert "стиральная" not in seen[-1]
    assert "%D1%81" in seen[-1]


async def test_search_warns_on_empty_results(monkeypatch):
    stub_html(monkeypatch, {"/search": load("search_empty.html")})

    result = await server.yandex_search(query="йцукенгшщз")

    assert result.returned == 0
    assert result.meta.healthy is False
    assert any("no_results" in w for w in result.meta.warnings)


async def test_search_raises_drift_when_the_page_is_unrecognisable(monkeypatch):
    """Neither products nor an empty-result banner means the SSR shape moved."""
    stub_html(monkeypatch, {"/search": "<html><body>redesigned</body></html>"})

    with pytest.raises(ToolError) as excinfo:
        await server.yandex_search(query="телефон")

    assert error_payload(excinfo.value)["error"] == "parser_drift"


async def test_search_surfaces_a_captcha_as_rate_limited(monkeypatch):
    """A captcha means slow down, and the error must say so retryably."""
    stub_html(monkeypatch, {"/search": '<html><body><div id="SmartCaptcha"></div></body></html>'})

    with pytest.raises(ToolError) as excinfo:
        await server.yandex_search(query="телефон")

    payload = error_payload(excinfo.value)
    assert payload["error"] == "rate_limited"
    assert payload["retryable"] is True


async def test_search_flags_a_degraded_ldjson_fallback(monkeypatch):
    """Callers must know when fields are missing and the price is Plus-only."""
    html = """<html><body><script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[{"item":{"name":"X",
     "url":"https://market.yandex.ru/product/1","offers":{"price":100}}}]}
    </script></body></html>"""
    stub_html(monkeypatch, {"/search": html})

    result = await server.yandex_search(query="тест")

    assert result.meta.extraction == "ld+json"
    assert any("degraded" in w for w in result.meta.warnings)


@pytest.mark.parametrize("bad_query", ["", " ", "x"])
async def test_search_rejects_too_short_queries(monkeypatch, bad_query):
    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("a rejected query must never reach the network")

    monkeypatch.setattr(server, "_fetch_html", fail_fetch)

    with pytest.raises((ToolError, Exception)):
        await server.yandex_search(query=bad_query)


# -------------------------------------------------------------------- card ----


async def test_card_returns_prices_rating_breakdown_and_reviews(monkeypatch):
    stub_html(monkeypatch, {"/product/763970960": load("card_washer.html")})

    card = await server.yandex_card(product_id="763970960")

    assert card.title.startswith("Стиральная машина")
    assert card.price_rub == 19377.0
    assert card.price_with_plus == 18796.0
    assert card.rating == 4.8
    assert card.rating_stars == {1: 10, 2: 3, 3: 10, 4: 19, 5: 502}
    assert card.reviews
    assert card.reviews[0].author


async def test_card_can_skip_reviews(monkeypatch):
    stub_html(monkeypatch, {"/product/": load("card_washer.html")})

    card = await server.yandex_card(product_id="763970960", include_reviews=False)

    assert card.reviews == []
    assert card.review_count is not None  # the count still comes through


async def test_card_warns_when_a_resale_offer_has_no_rating(monkeypatch):
    """Real and common — the warning explains it rather than implying 0 stars."""
    stub_html(monkeypatch, {"/product/": load("card_no_rating.html")})

    card = await server.yandex_card(product_id="1912483624")

    assert card.rating is None
    assert card.meta.healthy is False
    assert any("no_rating" in w for w in card.meta.warnings)


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "123?foo=bar", "abc", "12 34", "1/2", "-5"],
)
async def test_card_rejects_non_numeric_ids(monkeypatch, bad_id):
    """The id goes into a URL path, so it is validated as digits, never escaped."""

    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("a rejected id must never reach the network")

    monkeypatch.setattr(server, "_fetch_html", fail_fetch)

    with pytest.raises(ToolError) as excinfo:
        await server.yandex_card(product_id=bad_id)

    assert error_payload(excinfo.value)["error"] == "bad_request"


async def test_card_raises_drift_when_no_title_is_found(monkeypatch):
    stub_html(monkeypatch, {"/product/": '<html><body>"mainPrice" but no product</body></html>'})

    with pytest.raises(ToolError) as excinfo:
        await server.yandex_card(product_id="1")

    assert error_payload(excinfo.value)["error"] == "parser_drift"


# --------------------------------------------------------------- selfcheck ----


async def test_selfcheck_chains_search_into_card(monkeypatch):
    """The card probe uses an id from search, so no fixture SKU can go stale."""
    stub_html(
        monkeypatch,
        {"/search": load("search_washer.html"), "/product/": load("card_washer.html")},
    )

    result = await server.yandex_selfcheck()

    assert result.status == "success"
    assert result.checks["search"].state == "healthy"
    assert result.checks["card"].state == "healthy"
    assert result.tool_count == 3


async def test_selfcheck_is_inconclusive_when_transport_fails(monkeypatch):
    """A block says nothing about the parsers, so it must not read as drift."""
    from mcp_core.errors import TransportDownError, raise_tool_error

    async def always_down(url: str, label: str, ctx=None) -> str:
        raise_tool_error(TransportDownError("upstream unreachable", provider="yandex"))
        raise AssertionError("unreachable")

    monkeypatch.setattr(server, "_fetch_html", always_down)

    result = await server.yandex_selfcheck()

    assert result.status == "inconclusive"
    assert result.checks["search"].state == "inconclusive"


async def test_selfcheck_reports_drift_when_pages_stop_parsing(monkeypatch):
    stub_html(monkeypatch, {"/search": "<html><body>redesigned</body></html>"})

    result = await server.yandex_selfcheck()

    assert result.status == "drift_detected"
    assert result.checks["search"].state == "drift"


async def test_selfcheck_skips_card_when_search_yields_no_id(monkeypatch):
    stub_html(monkeypatch, {"/search": load("search_empty.html")})

    result = await server.yandex_selfcheck()

    assert result.checks["card"].state == "inconclusive"
    assert "skipped" in result.checks["card"].detail


# --------------------------------------------------------------- transport ----


async def test_fetch_html_caches_within_the_ttl(monkeypatch):
    """Pages are 2 MB; refetching one inside a conversation is pure waste."""
    calls: list[str] = []

    async def fake_get(client, url, **kwargs):
        calls.append(url)
        return 200, load("search_washer.html")

    monkeypatch.setattr(server, "get_text_with_retries", fake_get)

    await server._fetch_html("https://market.yandex.ru/search?text=a", "test", None)
    await server._fetch_html("https://market.yandex.ru/search?text=a", "test", None)

    assert len(calls) == 1


async def test_fetch_html_rejects_an_empty_body(monkeypatch):
    async def fake_get(client, url, **kwargs):
        return 200, "   "

    monkeypatch.setattr(server, "get_text_with_retries", fake_get)

    with pytest.raises(ToolError) as excinfo:
        await server._fetch_html("https://market.yandex.ru/search?text=b", "test", None)

    assert error_payload(excinfo.value)["error"] == "transport_down"


async def test_fetch_html_maps_429_to_rate_limited(monkeypatch):
    async def fake_get(client, url, **kwargs):
        return 429, "slow down"

    monkeypatch.setattr(server, "get_text_with_retries", fake_get)

    with pytest.raises(ToolError) as excinfo:
        await server._fetch_html("https://market.yandex.ru/search?text=c", "test", None)

    assert error_payload(excinfo.value)["error"] == "rate_limited"


async def test_fetch_html_treats_an_empty_302_as_transport_failure(monkeypatch):
    """Yandex's transient hiccup: 302 with no body, retried then surfaced."""

    async def fake_get(client, url, **kwargs):
        return 302, ""

    monkeypatch.setattr(server, "get_text_with_retries", fake_get)

    with pytest.raises(ToolError) as excinfo:
        await server._fetch_html("https://market.yandex.ru/search?text=d", "test", None)

    payload = error_payload(excinfo.value)
    assert payload["error"] == "transport_down"
    assert payload["retryable"] is True


def test_retry_statuses_include_302_but_not_429():
    """302 is a transient hiccup here; retrying a 429 would deepen the limit."""
    assert 302 in server._RETRY_STATUSES
    assert 429 not in server._RETRY_STATUSES


# ------------------------------------------------------- pagination dedupe ----


def _stub_parsed_items(monkeypatch, raw_items):
    """Feed yandex_search a known item list, bypassing HTML and the SSR parser."""
    stub_html(monkeypatch, {"/search": "<html></html>"})

    def fake_parse_search(_html):
        return {
            "status": server.ssr.ParseStatus.OK,
            "items": raw_items,
            "query": "проверка",
            "page": 1,
            "page_count": 30,
            "total": 500,
            "has_next_page": True,
        }

    monkeypatch.setattr(server.ssr, "parse_search", fake_parse_search)


async def test_search_drops_repeated_product_ids(monkeypatch):
    """One product occupying several snippets must be reported once."""
    _stub_parsed_items(
        monkeypatch,
        [
            {"product_id": "111", "title": "первый"},
            {"product_id": "222", "title": "второй"},
            {"product_id": "111", "title": "первый снова"},
            {"product_id": "333", "title": "третий"},
        ],
    )

    result = await server.yandex_search(query="проверка")

    ids = [item.product_id for item in result.items]
    assert ids == ["111", "222", "333"]
    assert result.returned == 3


async def test_dedupe_runs_before_the_limit_is_applied(monkeypatch):
    """A duplicate must not eat part of the caller's budget.

    Slicing first would return two distinct products for limit=3 while claiming
    the page was full, with nothing to explain the shortfall.
    """
    _stub_parsed_items(
        monkeypatch,
        [
            {"product_id": "111", "title": "a"},
            {"product_id": "111", "title": "a duplicate"},
            {"product_id": "222", "title": "b"},
            {"product_id": "333", "title": "c"},
        ],
    )

    result = await server.yandex_search(query="проверка", limit=3)

    assert [item.product_id for item in result.items] == ["111", "222", "333"]
    assert result.returned == 3


async def test_dedupe_preserves_upstream_ranking_order(monkeypatch):
    """Yandex's ordering is the result of the search and must survive."""
    _stub_parsed_items(
        monkeypatch,
        [
            {"product_id": "999", "title": "ranked first"},
            {"product_id": "111", "title": "ranked second"},
            {"product_id": "999", "title": "repeat of first"},
            {"product_id": "555", "title": "ranked third"},
        ],
    )

    result = await server.yandex_search(query="проверка")

    assert [item.product_id for item in result.items] == ["999", "111", "555"]


async def test_products_without_an_id_are_never_collapsed(monkeypatch):
    """A blank id means "unknown", not "the same product"."""
    _stub_parsed_items(
        monkeypatch,
        [
            {"product_id": "", "title": "unidentified one"},
            {"product_id": "", "title": "unidentified two"},
            {"product_id": "111", "title": "identified"},
        ],
    )

    result = await server.yandex_search(query="проверка")

    assert result.returned == 3
    assert [item.title for item in result.items] == [
        "unidentified one",
        "unidentified two",
        "identified",
    ]


async def test_limit_still_caps_a_page_without_duplicates(monkeypatch):
    _stub_parsed_items(
        monkeypatch,
        [{"product_id": str(i), "title": f"item {i}"} for i in range(10)],
    )

    result = await server.yandex_search(query="проверка", limit=4)

    assert result.returned == 4
    assert [item.product_id for item in result.items] == ["0", "1", "2", "3"]
