"""Offline tests for the Citilink connector.

CDP rendering is monkeypatched out: the suite runs with no Chrome and no
network. Fixtures mirror the tile/card extraction shapes from a rendered,
Qrator-passed page.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest
from citilink_connector import server
from fastmcp.exceptions import ToolError

SEARCH_EXTRACTED = {
    "title": "ноутбук — DNS",
    "items": [
        {
            "product_id": "5f4dcc3b5aa764d61d8327de",
            "title": "Ноутбук Lenovo IdeaPad 3",
            "price_meta": "52999",
            "price_text": "52 999₽",
            "old_price_text": "62 999",
            "price_texts": {"attached": ["52 999₽"], "other": ["62 999"]},
            "url": "https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/",
        },
        {
            "product_id": "5f4dcc3b5aa764d61d8327df",
            "title": "Ноутбук без цены",
            "price_meta": None,
            "price_text": None,
            "old_price_text": None,
            "price_texts": {"attached": [], "other": []},
            "url": "",
        },
    ],
}

CARD_EXTRACTED = {
    "title": "Ноутбук Lenovo IdeaPad 3 15ITL6",
    "price_rub": 52999.0,
    "old_price_rub": 62999.0,
    "is_available": True,
    "page_title": "Ноутбук Lenovo IdeaPad 3 — DNS",
}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    server._cache._data.clear()


def _patch_render(monkeypatch, payload):
    async def fake_render(url, extract_js, wait_ms, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render", fake_render)


# ------------------------------------------------------------------ citilink_search ----


async def test_search_parses_tiles(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.citilink_search("ноутбук")

    assert result.count == 2
    assert result.items[0].product_id == "5f4dcc3b5aa764d61d8327de"
    assert result.items[0].price_rub == 52999.0


async def test_search_a_pricelss_tile_is_none_never_zero(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.citilink_search("ноутбук")

    assert result.items[1].price_rub is None
    assert result.items[1].price_rub != 0


async def test_search_maps_zero_tiles_to_parser_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "ноутбук", "items": []})

    with pytest.raises(ToolError):
        await server.citilink_search("ноутбук")


# -------------------------------------------------------------------- citilink_card ----


async def test_card_parses_the_product(monkeypatch):
    _patch_render(monkeypatch, CARD_EXTRACTED)

    result = await server.citilink_card("https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/")

    assert result.product_id == "5f4dcc3b5aa764d61d8327de"
    assert result.price_rub == 52999.0
    assert result.is_available is True


async def test_card_rejects_a_url_without_a_product_id():
    with pytest.raises(ToolError):
        await server.citilink_card("https://www.citilink.ru/catalog/notebooks/")


async def test_card_flags_drift_when_neither_title_nor_price(monkeypatch):
    payload = {"title": None, "price_rub": None, "old_price_rub": None, "is_available": None, "page_title": "DNS"}
    _patch_render(monkeypatch, payload)

    with pytest.raises(ToolError):
        await server.citilink_card("https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/x/")


async def test_card_warns_when_in_stock_but_no_price_matched(monkeypatch):
    """An available card with no price block is suspicious: the buy-block
    layout most likely moved (anchors no longer match). Say so loudly in
    meta.warnings instead of silently handing back price_rub=None."""
    payload = {
        "title": "Ноутбук Lenovo IdeaPad 3 15ITL6",
        "price_meta": None,
        "price_text": None,
        "old_price_text": None,
        "price_texts": {"attached": [], "other": []},
        "is_available": True,
        "page_title": "Ноутбук — Citilink",
    }
    _patch_render(monkeypatch, payload)

    result = await server.citilink_card("https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/")

    assert result.price_rub is None
    assert result.is_available is True
    assert any(w.startswith("in_stock_no_price") for w in result.meta.warnings)
    assert result.meta.healthy is False


async def test_card_stays_silent_when_unavailable_and_unpriced(monkeypatch):
    """No stock and no price is a normal, expected combination (the buy block
    is simply absent) — it must not raise the in-stock canary."""
    payload = {
        "title": "Ноутбук Lenovo IdeaPad 3 15ITL6",
        "price_meta": None,
        "price_text": None,
        "old_price_text": None,
        "price_texts": {"attached": [], "other": []},
        "is_available": False,
        "page_title": "Ноутбук — Citilink",
    }
    _patch_render(monkeypatch, payload)

    result = await server.citilink_card("https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/")

    assert result.price_rub is None
    assert not any(w.startswith("in_stock_no_price") for w in result.meta.warnings)
    assert result.meta.healthy is True


# ------------------------------------------------------------- citilink_selfcheck ----


async def test_selfcheck_healthy_when_tiles_extract(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.citilink_selfcheck()

    assert result.status == "success"
    assert result.healthy is True


async def test_selfcheck_zero_tiles_is_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "x", "items": []})

    result = await server.citilink_selfcheck()

    assert result.status == "drift_detected"


async def test_selfcheck_healthy_tiles_carry_the_shape_reference(monkeypatch):
    """Tiles extracting is not enough: the shape must still match the captured
    reference. A matching payload says so explicitly."""
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.citilink_selfcheck()

    entry = result.checks["search"]
    assert entry.state == "healthy"
    assert "shape matches the captured reference" in entry.notes


async def test_selfcheck_reports_shape_drift_when_a_required_path_vanishes(monkeypatch):
    """A page that still yields tiles but lost a parser-critical field is
    structural drift — loud, with the missing path named."""
    drifted = {
        "title": SEARCH_EXTRACTED["title"],
        "items": [
            {key: value for key, value in item.items() if key != "price_text"} for item in SEARCH_EXTRACTED["items"]
        ],
    }
    _patch_render(monkeypatch, drifted)

    result = await server.citilink_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search"]
    assert entry.state == "drift"
    assert entry.reason == "shape_drift"
    assert any("items[].price_text" in note for note in entry.notes)


# ------------------------------------------------------------------- helpers ----


def test_extract_product_id():
    assert (
        server._extract_product_id("https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/x/")
        == "5f4dcc3b5aa764d61d8327de"
    )
    assert server._extract_product_id("https://www.citilink.ru/catalog/x/") is None


# ------------------------------------------------------------------- SSRF guard ----
#
# citilink_card renders in the operator's own Chrome, carrying whatever cookies
# that profile holds. So the navigated host must come from SITE_BASE and never
# from the argument. The id used to be extracted purely as a validity gate while
# the raw argument was navigated, which let any host carrying /product/<24-hex>
# anywhere in its path drive the logged-in browser.

_OFF_HOST_URLS = [
    "https://evil.example/product/5f4dcc3b5aa764d61d8327de/x/",
    "https://citilink.ru.evil.example/product/5f4dcc3b5aa764d61d8327de/",
    "http://127.0.0.1:9222/product/5f4dcc3b5aa764d61d8327de/",
    "//evil.example/product/5f4dcc3b5aa764d61d8327de/",
    "file:///etc/passwd#/product/5f4dcc3b5aa764d61d8327de",
    "javascript:fetch('/product/5f4dcc3b5aa764d61d8327de')",
]


@pytest.mark.parametrize("hostile", _OFF_HOST_URLS)
def test_extract_product_id_refuses_off_host_urls(hostile):
    assert server._extract_product_id(hostile) is None


@pytest.mark.parametrize("hostile", _OFF_HOST_URLS)
async def test_card_refuses_off_host_urls(monkeypatch, hostile):
    def explode(*_args, **_kwargs):
        raise AssertionError(f"card must not navigate an off-host URL: {hostile!r}")

    monkeypatch.setattr(server, "_cdp_render", explode)

    with pytest.raises(ToolError):
        await server.citilink_card(hostile)


async def test_search_sends_the_query_as_text(monkeypatch):
    """citilink.ru reads the search query from `text`. With `?q=` it renders the
    generic search landing: zero tiles, reported as drift while the site was fine
    (#104, measured 2026-09-18..29)."""
    seen: list[str] = []

    async def capture(url, extract_js, wait_ms, ctx):
        seen.append(url)
        return SEARCH_EXTRACTED

    monkeypatch.setattr(server, "_cdp_render", capture)

    await server.citilink_search("Samsung 870 EVO 500GB")

    assert seen == ["https://www.citilink.ru/search/?text=Samsung%20870%20EVO%20500GB"]


async def test_card_navigates_a_rebuilt_site_base_url(monkeypatch):
    """Even for a legitimate URL, we navigate our own construction, not theirs."""
    seen: list[str] = []

    async def capture(url, extract_js, wait_ms, ctx):
        seen.append(url)
        return CARD_EXTRACTED

    monkeypatch.setattr(server, "_cdp_render", capture)

    await server.citilink_card(
        "https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/?utm_source=x#frag"
    )

    assert seen == ["https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/"]


async def test_card_accepts_a_bare_product_id(monkeypatch):
    seen: list[str] = []

    async def capture(url, extract_js, wait_ms, ctx):
        seen.append(url)
        return CARD_EXTRACTED

    monkeypatch.setattr(server, "_cdp_render", capture)

    result = await server.citilink_card("5f4dcc3b5aa764d61d8327de")

    assert result.product_id == "5f4dcc3b5aa764d61d8327de"
    assert seen == ["https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/"]


# ----------------------------------------------------------- real id formats ----
#
# The original pattern demanded 24 hex characters — a MongoDB ObjectId shape
# citilink.ru does not use — so every search parsed zero tiles and the connector
# reported drift. It shipped because the fixtures above invented ids in the
# same wrong shape the parser expected, so the suite agreed with the bug.
#
# These are routes observed live on citilink.ru in July 2026. If the parser ever
# stops understanding them again, this is the test that says so.

_REAL_URLS = [
    ("https://www.citilink.ru/product/noutbuk-lenovo-2169270/", "noutbuk-lenovo-2169270"),
    ("https://www.citilink.ru/product/2169270/", "2169270"),
]


@pytest.mark.parametrize(("url", "expected"), _REAL_URLS)
def test_real_product_urls_yield_their_id(url, expected):
    assert server._extract_product_id(url) == expected


@pytest.mark.parametrize(("_url", "bare"), _REAL_URLS)
def test_a_real_bare_id_is_accepted(_url, bare):
    """The tool docstring promises "a product id or a URL"; honour both."""
    assert server._extract_product_id(bare) == bare


# The id is interpolated back into a URL opened in the operator's logged-in
# Chrome, so it must not be able to carry a query string, a fragment, an
# encoded slash or a traversal step. The host guard alone does not cover this:
# these all stay on-site and would still be rendered with the operator's cookies.
_INJECTION_INPUTS = [
    "/product/x?utm=1&redirect=evil",
    "/product/..",
    "/product/%2e%2e%2f%2e%2e",
    "/product/x#@evil.example",
    "/product/x%00",
]


@pytest.mark.parametrize("hostile", _INJECTION_INPUTS)
def test_an_id_never_carries_query_fragment_or_traversal(hostile):
    got = server._extract_product_id(hostile)
    if got is None:
        return
    assert all(ch not in got for ch in "?#%./\\"), f"id {got!r} would alter the rebuilt URL"


def test_the_dom_extractor_and_the_python_parser_agree():
    """Search and card must read the same id shape.

    If the JS running in the page pulls one charset and the Python parser
    accepts another, search hands back ids the card cannot reopen — a mismatch
    that looks like a marketplace problem and is ours. This is how the 24-hex
    bug half-survived its first fix: the two sides were changed to different
    patterns.
    """
    assert "[A-Za-z0-9_-]+" in server._SEARCH_EXTRACT_JS, "the search extractor JS no longer uses the shared id charset"
    assert server._ID_CHARS == "[A-Za-z0-9_-]", "the Python id charset moved; update the JS to match"


# ----------------------------------------------------------- Qrator regression ----


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, False),
        ({"title": "   "}, False),
        ({"title": "ДОСТУП ОГРАНИЧЕН"}, True),
        ({"title": "Qrator challenge"}, True),
        ({"title": "Ноутбук", "page_title": "Доступ ограничен"}, True),
        ({"title": "Доступ ограничен", "page_title": "Citilink"}, False),
        ({"title": "Qrator notebook", "page_title": ""}, False),
        ({"title": "Qrator notebook", "page_title": None}, False),
        ({"page_title": "", "qrator_wall": True}, True),
        ({"page_title": "", "qrator_wall": False}, False),
    ],
)
def test_qrator_wall_requires_positive_document_evidence(payload, expected):
    assert server._is_qrator_wall(payload) is expected


@pytest.mark.parametrize("evidence", [{"title": "Доступ ограничен"}, {"title": "", "qrator_wall": True}])
async def test_search_rejects_qrator_wall_before_mapping_and_never_caches(monkeypatch, evidence):
    payload = {**SEARCH_EXTRACTED, **evidence}
    _patch_render(monkeypatch, payload)

    def unexpected_mapping(_tile):
        pytest.fail("Qrator wall must be rejected before product mapping")

    monkeypatch.setattr(server, "_search_item_from_tile", unexpected_mapping)
    with pytest.raises(ToolError) as caught:
        await server.citilink_search("ноутбук")

    error = json.loads(str(caught.value))
    assert error["error"] == "challenge_required"
    assert error["provider"] == "citilink"
    assert error["requires_user_action"] is True
    assert error["retryable"] is True
    assert error["challenge_type"] == "qrator"
    assert "citilink.ru" in error["message"]
    assert "Chrome" in error["message"]
    assert "scraping" in error["message"].lower()
    assert "pass" in error["message"].lower()
    assert "retry" in error["message"].lower()
    assert len(server._cache) == 0


@pytest.mark.parametrize(
    "evidence",
    [
        {"page_title": "ДОСТУП ОГРАНИЧЕН"},
        {"page_title": "Qrator"},
        {"page_title": "", "qrator_wall": True},
    ],
)
async def test_card_rejects_qrator_wall_before_mapping_and_never_caches(monkeypatch, evidence):
    _patch_render(monkeypatch, {**CARD_EXTRACTED, **evidence})

    def unexpected_prices(_payload):
        pytest.fail("Qrator wall must be rejected before product price mapping")

    monkeypatch.setattr(server, "prices_from_tile", unexpected_prices)
    with pytest.raises(ToolError) as caught:
        await server.citilink_card("2169270")

    error = json.loads(str(caught.value))
    assert error["error"] == "challenge_required"
    assert error["requires_user_action"] is True
    assert error["challenge_type"] == "qrator"
    assert "citilink.ru" in error["message"]
    assert "Chrome" in error["message"]
    assert "retry" in error["message"].lower()
    assert len(server._cache) == 0


@pytest.mark.parametrize("operation", ["search", "card"])
@pytest.mark.parametrize("legacy_cached_wall", [False, True])
async def test_qrator_wall_retry_renders_immediately_and_caches_success(monkeypatch, operation, legacy_cached_wall):
    if operation == "search":
        tool, argument = server.citilink_search, "ноутбук"
        url = f"{server.SEARCH_URL}?text={urllib.parse.quote(argument)}"
        healthy = SEARCH_EXTRACTED
        wall = {"title": "Доступ ограничен", "items": []}
    else:
        tool, argument = server.citilink_card, "2169270"
        url = f"{server.SITE_BASE}/product/{argument}/"
        healthy = CARD_EXTRACTED
        # Older card extractors copied the challenge's document title into title.
        wall = {"title": "Доступ ограничен", "price_rub": None}
    rendered = []
    if legacy_cached_wall:
        server._cache.set(url, wall)

    async def render(render_url, extract_js, wait_ms, ctx):
        rendered.append(render_url)
        return healthy if legacy_cached_wall or len(rendered) > 1 else wall

    monkeypatch.setattr(server, "_cdp_render", render)
    with pytest.raises(ToolError) as caught:
        await tool(argument)
    assert json.loads(str(caught.value))["error"] == "challenge_required"
    assert server._cache.get(url) is None

    result = await tool(argument)
    assert result.tier_used == "cdp"
    assert result.status == "success"
    cached_result = await tool(argument)
    assert cached_result.tier_used == "cache"
    assert rendered == [url] * (1 if legacy_cached_wall else 2)


@pytest.mark.parametrize("operation", ["search", "card"])
async def test_empty_page_is_drift_without_cache(monkeypatch, operation):
    _patch_render(monkeypatch, {"title": None, "page_title": "", "items": [], "price_rub": None})
    tool, argument = (server.citilink_search, "ноутбук") if operation == "search" else (server.citilink_card, "2169270")

    with pytest.raises(ToolError) as caught:
        await tool(argument)
    assert json.loads(str(caught.value))["error"] == "parser_drift"
    assert len(server._cache) == 0


async def test_search_with_empty_document_title_and_missing_prices_is_accepted(monkeypatch):
    payload = {"title": "", "items": [SEARCH_EXTRACTED["items"][1]]}
    _patch_render(monkeypatch, payload)

    result = await server.citilink_search("ноутбук")
    assert result.status == "success"
    assert result.items[0].price_rub is None
    assert "no_prices_on_page" in result.meta.warnings
    assert len(server._cache) == 1


async def test_card_with_empty_document_title_and_missing_price_is_accepted(monkeypatch):
    payload = {"title": "Qrator notebook", "page_title": "", "price_rub": None, "is_available": False}
    _patch_render(monkeypatch, payload)

    result = await server.citilink_card("2169270")
    assert result.title == "Qrator notebook"
    assert result.price_rub is None
    assert result.meta.healthy is True
    assert len(server._cache) == 1


@pytest.mark.parametrize(
    "evidence", [{"title": "Доступ ограничен"}, {"title": "Qrator"}, {"title": "", "qrator_wall": True}]
)
@pytest.mark.parametrize("with_tiles", [False, True])
async def test_selfcheck_qrator_wall_is_blocked_not_drift(monkeypatch, evidence, with_tiles):
    _patch_render(
        monkeypatch, {**SEARCH_EXTRACTED, **evidence, "items": SEARCH_EXTRACTED["items"] if with_tiles else []}
    )

    result = await server.citilink_selfcheck()
    assert result.status == "inconclusive"
    assert result.healthy is None
    assert result.checks["search"].state == "inconclusive"
    assert result.checks["search"].reason == "blocked"
    assert len(server._cache) == 0


async def test_selfcheck_empty_document_is_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "", "items": []})

    result = await server.citilink_selfcheck()
    assert result.status == "drift_detected"
    assert result.checks["search"].reason == "parse_smoke_failed"
