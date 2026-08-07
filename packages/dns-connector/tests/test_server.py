"""Offline tests for the DNS-Shop connector.

CDP rendering is monkeypatched out: the suite runs with no Chrome and no
network. Fixtures mirror the tile/card extraction shapes from a rendered,
Qrator-passed page.
"""

from __future__ import annotations

import pytest
from dns_connector import server
from fastmcp.exceptions import ToolError

SEARCH_EXTRACTED = {
    "title": "ноутбук — DNS",
    "items": [
        {
            "product_id": "5f4dcc3b5aa764d61d8327de",
            "title": "Ноутбук Lenovo IdeaPad 3",
            "price_text": "52 999 ₽",
            "old_price_text": "62 999",
            "availability_text": "В наличии",
            "url": "https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/",
        },
        {
            "product_id": "5f4dcc3b5aa764d61d8327df",
            "title": "Ноутбук без цены",
            "price_text": None,
            "old_price_text": None,
            "availability_text": "В наличии",
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


# ------------------------------------------------------------------ dns_search ----


async def test_search_parses_tiles(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.dns_search("ноутбук")

    assert result.count == 2
    assert result.items[0].product_id == "5f4dcc3b5aa764d61d8327de"
    assert result.items[0].price_rub == 52999.0


async def test_search_a_pricelss_tile_is_none_never_zero(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.dns_search("ноутбук")

    assert result.items[1].price_rub is None
    assert result.items[1].price_rub != 0


async def test_search_maps_zero_tiles_to_parser_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "ноутбук", "items": []})

    with pytest.raises(ToolError):
        await server.dns_search("ноутбук")


# -------------------------------------------------------------------- dns_card ----


async def test_card_parses_the_product(monkeypatch):
    _patch_render(monkeypatch, CARD_EXTRACTED)

    result = await server.dns_card("https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/")

    assert result.product_id == "5f4dcc3b5aa764d61d8327de"
    assert result.price_rub == 52999.0
    assert result.is_available is True


async def test_card_rejects_a_url_without_a_product_id():
    with pytest.raises(ToolError):
        await server.dns_card("https://www.dns-shop.ru/catalog/notebooks/")


async def test_card_flags_drift_when_neither_title_nor_price(monkeypatch):
    payload = {"title": None, "price_rub": None, "old_price_rub": None, "is_available": None, "page_title": "DNS"}
    _patch_render(monkeypatch, payload)

    with pytest.raises(ToolError):
        await server.dns_card("https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/x/")


# ------------------------------------------------------------- dns_selfcheck ----


async def test_selfcheck_healthy_when_tiles_extract(monkeypatch):
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.dns_selfcheck()

    assert result.status == "success"
    assert result.healthy is True


async def test_selfcheck_zero_tiles_is_drift(monkeypatch):
    _patch_render(monkeypatch, {"title": "x", "items": []})

    result = await server.dns_selfcheck()

    assert result.status == "drift_detected"


async def test_selfcheck_healthy_tiles_carry_the_shape_reference(monkeypatch):
    """Tiles extracting is not enough: the shape must still match the captured
    reference. A matching payload says so explicitly."""
    _patch_render(monkeypatch, SEARCH_EXTRACTED)

    result = await server.dns_selfcheck()

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

    result = await server.dns_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search"]
    assert entry.state == "drift"
    assert entry.reason == "shape_drift"
    assert any("items[].price_text" in note for note in entry.notes)


# ------------------------------------------------------------------- helpers ----


def test_extract_product_id():
    assert (
        server._extract_product_id("https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/x/")
        == "5f4dcc3b5aa764d61d8327de"
    )
    assert server._extract_product_id("https://www.dns-shop.ru/catalog/x/") is None


# ------------------------------------------------------------------- SSRF guard ----
#
# dns_card renders in the operator's own Chrome, carrying whatever cookies that
# profile holds. So the navigated host must come from SITE_BASE and never from
# the argument. The id used to be extracted purely as a validity gate while the
# raw argument was navigated, which let any host carrying /product/<24-hex>
# anywhere in its path drive the logged-in browser.

_OFF_HOST_URLS = [
    "https://evil.example/product/5f4dcc3b5aa764d61d8327de/x/",
    "https://dns-shop.ru.evil.example/product/5f4dcc3b5aa764d61d8327de/",
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
        await server.dns_card(hostile)


async def test_card_navigates_a_rebuilt_site_base_url(monkeypatch):
    """Even for a legitimate URL, we navigate our own construction, not theirs."""
    seen: list[str] = []

    async def capture(url, extract_js, wait_ms, ctx):
        seen.append(url)
        return CARD_EXTRACTED

    monkeypatch.setattr(server, "_cdp_render", capture)

    await server.dns_card("https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/?utm_source=x#frag")

    assert seen == ["https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/"]


async def test_card_accepts_a_bare_product_id(monkeypatch):
    seen: list[str] = []

    async def capture(url, extract_js, wait_ms, ctx):
        seen.append(url)
        return CARD_EXTRACTED

    monkeypatch.setattr(server, "_cdp_render", capture)

    result = await server.dns_card("5f4dcc3b5aa764d61d8327de")

    assert result.product_id == "5f4dcc3b5aa764d61d8327de"
    assert seen == ["https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/"]


# ----------------------------------------------------------- real id formats ----
#
# The original pattern demanded 24 hex characters — a MongoDB ObjectId shape
# dns-shop.ru does not use — so every search parsed zero tiles and the connector
# reported drift. It shipped because the fixtures above invented ids in the
# same wrong shape the parser expected, so the suite agreed with the bug.
#
# These are routes observed live on dns-shop.ru in July 2026. If the parser ever
# stops understanding them again, this is the test that says so.

_REAL_URLS = [
    ("https://www.dns-shop.ru/product/b7a1667f9b19ed20/noutbuk-15/", "b7a1667f9b19ed20"),
    ("https://www.dns-shop.ru/product/b7a1667f9b19ed20/", "b7a1667f9b19ed20"),
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
