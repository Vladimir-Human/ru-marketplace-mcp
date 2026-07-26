"""Offline tests for the Detsky Mir connector.

Every upstream call is monkeypatched, so the suite runs with no network and no
geo dependency. Fixtures mirror payload shapes captured live in Jul 2026 —
including the quirks that make this API easy to parse wrongly.
"""

from __future__ import annotations

import json
import tomllib
import urllib.parse
from pathlib import Path

import pytest
from detmir_connector import server
from fastmcp.exceptions import ToolError

# ---------------------------------------------------------------- fixtures ----

CARD_PAYLOAD = {
    "item": {
        "id": "6673568",
        "title": "Кукла пупс Demi Star в розовом комбинезоне",
        "article": "DF10-093A",
        "price": {"price": 699, "currency": "RUB"},
        "old_price": {"price": 1999, "currency": "RUB"},
        "discount_percentage": 65,
        "rating": 5,
        "review_count": 19,
        "questions_count": 2,
        "brands": [{"id": "112", "title": "Demi Star"}],
        "availability": "AVAILABLE",
        "available": {
            "online": {"warehouse_codes": ["1320"]},
            "offline": {"stores": ["0150", "1104", "1144"]},
        },
        "is_from_marketplace": False,
        "link": {"web_url": "https://www.detmir.ru/product/index/id/6673568/"},
        "pictures": [{"web": "https://img.detmir.st/x.jpg"}],
    }
}

CATEGORY_PAYLOAD = {
    "items": [
        {
            "id": 3710649,
            "title": "Кукла пупс Demi Star высота 35 см",
            "price": {"price": 1999, "currency": "RUB"},
            "old_price": {"price": 2499, "currency": "RUB"},
            "rating": 4.9,
            "review_count": 196,
            "brands": [{"id": "112", "title": "Demi Star"}],
            "availability": "AVAILABLE",
            "link": {"web_url": "https://www.detmir.ru/product/index/id/3710649/"},
        },
        {
            "id": 5555555,
            "title": "Кукла пупс CRY BABIES Кэти",
            "price": {"price": 4999, "currency": "RUB"},
            "rating": 4.4,
            "review_count": 12,
            "availability": "AVAILABLE",
        },
    ],
    "meta": {"length": 708, "title": "Пупсы", "limit": 20, "offset": 0},
}

CATEGORIES_PAYLOAD = {
    # NOTE: this endpoint returns rows under "data", unlike /v4/products.
    "data": [
        {
            "id": 1,
            "alias": "igry_i_igrushki",
            "title": "Игрушки и игры",
            "full_name": "Игрушки и игры",
            "level": 1,
            "products_count": 265291,
            "web_url": "https://www.detmir.ru/catalog/index/name/igry_i_igrushki/",
        },
        {
            "id": 114677,
            "alias": "detskaya_verhnyaya_odezhda",
            "title": "Детская верхняя одежда",
            "level": 1,
            "products_count": 213009,
            "parentId": None,
        },
    ],
    "meta": {"total": 27, "offset": 0, "limit": 30},
}


@pytest.fixture(autouse=True)
def clear_cache():
    """Keep cached payloads from leaking between tests."""
    server._cache.clear()
    yield
    server._cache.clear()


@pytest.fixture
def no_delay(monkeypatch):
    """Remove the politeness gap so tests do not actually wait."""
    monkeypatch.setattr(server._limiter, "min_gap_s", 0.0)


def stub_json(monkeypatch, mapping: dict[str, object]):
    """Route ``_fetch_json`` to canned payloads, matched by URL substring."""

    async def fake_fetch(url: str, label: str, ctx=None):
        for needle, payload in mapping.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(server, "_fetch_json", fake_fetch)


def error_payload(err: ToolError) -> dict:
    return json.loads(str(err))


# ------------------------------------------------------------------ basics ----


def test_server_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == server.SERVER_VERSION


async def test_registered_tools_are_stable():
    """The tool surface is a public contract — renames break client configs."""
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert names == {"detmir_card", "detmir_category", "detmir_categories", "detmir_selfcheck"}


async def test_no_search_tool_is_exposed():
    """Detsky Mir has no working text search, so no tool may pretend otherwise.

    The v4 filter ignores every text key and returns the whole catalog, and the
    site's search route answers 404 with a promo carousel. A 'search' tool here
    would return confidently wrong products.
    """
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert not any("search" in name for name in names)


# -------------------------------------------------------------------- card ----


async def test_card_parses_price_rating_and_stock(monkeypatch, no_delay):
    stub_json(monkeypatch, {"/v2/products/6673568": CARD_PAYLOAD})

    result = await server.detmir_card(product_id=6673568)

    assert result.product.product_id == 6673568
    assert result.product.title.startswith("Кукла пупс")
    assert result.product.price_rub == 699.0
    assert result.product.old_price_rub == 1999.0
    assert result.product.discount_percent == 65
    assert result.product.rating == 5.0
    assert result.product.review_count == 19
    assert result.product.brand == "Demi Star"
    assert result.product.availability == "AVAILABLE"
    assert result.product.available_online is True
    assert result.product.store_count == 3
    assert result.product.is_marketplace is False
    assert result.meta.healthy is True


async def test_card_treats_404_in_a_200_body_as_not_found(monkeypatch, no_delay):
    """The signature Detsky Mir quirk: HTTP 200 carrying {"status": 404}."""
    stub_json(monkeypatch, {"/v2/products/1": {"status": 404, "error": "not_found"}})

    with pytest.raises(ToolError) as excinfo:
        await server.detmir_card(product_id=1)

    payload = error_payload(excinfo.value)
    assert payload["error"] == "not_found"
    assert payload["retryable"] is False


async def test_card_raises_drift_when_no_product_node(monkeypatch, no_delay):
    stub_json(monkeypatch, {"/v2/products/": {"unexpected": "shape"}})

    with pytest.raises(ToolError) as excinfo:
        await server.detmir_card(product_id=42)

    assert error_payload(excinfo.value)["error"] == "parser_drift"


async def test_card_warns_when_price_is_missing(monkeypatch, no_delay):
    payload = {"item": {"id": "7", "title": "Товар без цены"}}
    stub_json(monkeypatch, {"/v2/products/7": payload})

    result = await server.detmir_card(product_id=7)

    assert result.product.price_rub is None
    assert result.meta.healthy is False
    assert any("no_price" in w for w in result.meta.warnings)


async def test_card_falls_back_through_price_shapes(monkeypatch, no_delay):
    """Prices arrive as a dict, a bare number, or under 'prices'/'final_price'."""
    payload = {"item": {"id": "8", "title": "X", "prices": {"sale": 1234}}}
    stub_json(monkeypatch, {"/v2/products/8": payload})

    result = await server.detmir_card(product_id=8)
    assert result.product.price_rub == 1234.0


async def test_card_never_reports_zero_as_a_price(monkeypatch, no_delay):
    """A 0 price would rank a dead listing as the cheapest option."""
    payload = {"item": {"id": "9", "title": "X", "price": {"price": 0, "currency": "RUB"}}}
    stub_json(monkeypatch, {"/v2/products/9": payload})

    result = await server.detmir_card(product_id=9)
    assert result.product.price_rub is None


# ---------------------------------------------------------------- category ----


async def test_category_lists_products_with_upstream_total(monkeypatch, no_delay):
    stub_json(monkeypatch, {"/v4/products": CATEGORY_PAYLOAD})

    result = await server.detmir_category(alias="pups", limit=20)

    assert result.mode == "category"
    assert result.category_title == "Пупсы"
    assert result.total_available == 708
    assert result.returned == 2
    assert result.items[0].price_rub == 1999.0
    assert result.items[0].rating == 4.9


async def test_category_accepts_a_bare_array_payload(monkeypatch, no_delay):
    """Without meta=* the endpoint returns a plain array; both shapes are valid."""
    stub_json(monkeypatch, {"/v4/products": CATEGORY_PAYLOAD["items"]})

    result = await server.detmir_category(alias="pups")

    assert result.returned == 2
    assert result.total_available is None


@pytest.mark.parametrize(
    "bad_alias",
    [
        "pups;withregion:RU-SPE",  # filter-expression injection
        "pups;q:x",
        "../../etc/passwd",
        "Пупсы",  # non-ASCII is never a valid slug
        "alias with spaces",
        "pups/extra",
        "-leading-dash",
    ],
)
async def test_category_rejects_non_slug_aliases(monkeypatch, no_delay, bad_alias):
    """The alias is interpolated into a filter expression, so it is validated."""

    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("a rejected alias must never reach the network")

    monkeypatch.setattr(server, "_fetch_json", fail_fetch)

    with pytest.raises(ToolError) as excinfo:
        await server.detmir_category(alias=bad_alias)

    assert error_payload(excinfo.value)["error"] == "bad_request"


@pytest.mark.parametrize("alias", ["PUPS", "  pups  ", "Igry_I_Igrushki"])
async def test_category_normalises_case_and_whitespace(monkeypatch, no_delay, alias):
    """Case and stray whitespace are user slips, not attacks — normalise them.

    Slugs are lowercase upstream, so folding case is safe and spares the caller a
    pointless error; anything that is still not a slug afterwards is rejected.
    """
    seen: dict[str, str] = {}

    async def capture(url: str, label: str, ctx=None):
        seen["url"] = url
        return CATEGORY_PAYLOAD

    monkeypatch.setattr(server, "_fetch_json", capture)

    result = await server.detmir_category(alias=alias)

    assert result.query == alias.strip().lower()
    assert alias.strip().lower() in seen["url"] or "alias%3A" in seen["url"]


async def test_category_warns_on_empty_result(monkeypatch, no_delay):
    stub_json(monkeypatch, {"/v4/products": {"items": [], "meta": {"length": 0}}})

    result = await server.detmir_category(alias="nonexistent-slug")

    assert result.returned == 0
    assert result.meta.healthy is False
    assert any("empty" in w for w in result.meta.warnings)


async def test_category_passes_region_from_settings(monkeypatch, no_delay):
    seen: dict[str, str] = {}

    async def capture(url: str, label: str, ctx=None):
        seen["url"] = url
        return CATEGORY_PAYLOAD

    monkeypatch.setattr(server, "_fetch_json", capture)

    result = await server.detmir_category(alias="pups", limit=5, offset=10)

    assert "withregion" in seen["url"]
    assert server._settings.region in seen["url"]
    assert "limit=5" in seen["url"]
    assert "offset=10" in seen["url"]
    assert result.offset == 10


# -------------------------------------------------------------- categories ----


async def test_categories_reads_rows_from_the_data_key(monkeypatch, no_delay):
    """/v2/categories nests rows under 'data' — not 'items' like /v4/products."""
    stub_json(monkeypatch, {"/v2/categories": CATEGORIES_PAYLOAD})

    result = await server.detmir_categories(parent="top", limit=30)

    assert result.parent == "top"
    assert result.returned == 2
    assert result.total_available == 27
    assert result.items[0].alias == "igry_i_igrushki"
    assert result.items[0].products_count == 265291


async def test_categories_tolerates_an_items_key(monkeypatch, no_delay):
    """If upstream renames 'data' to 'items', keep working rather than break."""
    renamed = {"items": CATEGORIES_PAYLOAD["data"], "meta": CATEGORIES_PAYLOAD["meta"]}
    stub_json(monkeypatch, {"/v2/categories": renamed})

    result = await server.detmir_categories(parent="top")
    assert result.returned == 2


async def test_categories_numeric_parent_uses_parent_id_filter(monkeypatch, no_delay):
    seen: dict[str, str] = {}

    async def capture(url: str, label: str, ctx=None):
        seen["url"] = url
        return CATEGORIES_PAYLOAD

    monkeypatch.setattr(server, "_fetch_json", capture)

    await server.detmir_categories(parent="114677")

    assert "parent_id%3A114677" in seen["url"] or "parent_id:114677" in seen["url"]


@pytest.mark.parametrize("bad_parent", ["igry_i_igrushki", "level:1;withregion:RU-SPE", "../x"])
async def test_categories_rejects_non_numeric_parent(monkeypatch, no_delay, bad_parent):
    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("a rejected parent must never reach the network")

    monkeypatch.setattr(server, "_fetch_json", fail_fetch)

    with pytest.raises(ToolError) as excinfo:
        await server.detmir_categories(parent=bad_parent)

    assert error_payload(excinfo.value)["error"] == "bad_request"


# -------------------------------------------------------------- selfcheck -----


async def test_selfcheck_reports_success_when_every_family_is_healthy(monkeypatch, no_delay):
    stub_json(
        monkeypatch,
        {
            "/v2/products/": CARD_PAYLOAD,
            "/v4/products": CATEGORY_PAYLOAD,
            "/v2/categories": CATEGORIES_PAYLOAD,
        },
    )

    result = await server.detmir_selfcheck()

    assert result.status == "success"
    assert set(result.checks) == {"card", "category", "categories"}
    assert all(entry.state == "healthy" for entry in result.checks.values())
    assert result.tool_count == 4


async def test_selfcheck_is_inconclusive_when_transport_fails(monkeypatch, no_delay):
    """A transport block says nothing about the parsers, so it is not drift."""
    from mcp_core.errors import TransportDownError, raise_tool_error

    async def always_down(url: str, label: str, ctx=None):
        raise_tool_error(TransportDownError("upstream unreachable", provider="detmir"))

    monkeypatch.setattr(server, "_fetch_json", always_down)

    result = await server.detmir_selfcheck()

    assert result.status == "inconclusive"
    assert all(entry.state == "inconclusive" for entry in result.checks.values())


async def test_selfcheck_flags_drift_on_unparseable_payload(monkeypatch, no_delay):
    stub_json(
        monkeypatch,
        {
            "/v2/products/": {"totally": "different"},
            "/v4/products": CATEGORY_PAYLOAD,
            "/v2/categories": CATEGORIES_PAYLOAD,
        },
    )

    result = await server.detmir_selfcheck()

    assert result.status == "drift_detected"
    assert result.checks["card"].state == "drift"


# ------------------------------------------------------------------ parsing ---


def test_parse_product_survives_a_non_dict_input():
    """A tolerant reader must degrade, not raise, on drifted payloads."""
    assert server._parse_product(None).product_id is None
    assert server._parse_product([1, 2, 3]).title == ""


def test_parse_product_handles_string_brands():
    product = server._parse_product({"id": 1, "title": "X", "brand": "LEGO"})
    assert product.brand == "LEGO"


def test_parse_product_synthesises_url_from_id():
    product = server._parse_product({"id": 424242, "title": "X"})
    assert product.url.endswith("/product/index/id/424242/")


def test_body_error_status_only_flags_real_errors():
    assert server._body_error_status({"status": 404}) == 404
    assert server._body_error_status({"status": 200}) is None
    assert server._body_error_status({"item": {}}) is None
    assert server._body_error_status([1, 2]) is None


# ------------------------------------------------------------------ region ----


async def test_card_sends_the_region_as_a_filter_not_a_query_parameter(monkeypatch):
    """?withregion= is silently ignored upstream; only filter=withregion: works.

    Verified live: the query-parameter form returned store_count=0 for every city
    while the filter form returned 152/37/2 for Moscow/St Petersburg/Khabarovsk.
    Sending the wrong form made this tool label region-less data with a region.
    """
    seen = {}

    async def capture(url: str, label: str, ctx=None):
        seen["url"] = url
        return CARD_PAYLOAD

    monkeypatch.setattr(server, "_fetch_json", capture)

    await server.detmir_card(product_id=123, region="RU-SPE")

    assert "filter=withregion" in seen["url"].replace("%3A", ":")
    assert "?withregion=" not in seen["url"]
    assert "RU-SPE" in urllib.parse.unquote(seen["url"])


async def test_card_reports_the_region_it_actually_queried(monkeypatch):
    stub_json(monkeypatch, {"/v2/products/": CARD_PAYLOAD})

    result = await server.detmir_card(product_id=123, region="RU-SPE")
    assert result.region == "RU-SPE"


async def test_card_region_defaults_to_the_configured_one(monkeypatch):
    stub_json(monkeypatch, {"/v2/products/": CARD_PAYLOAD})

    result = await server.detmir_card(product_id=123)
    assert result.region == server._settings.region.upper()


async def test_region_argument_overrides_the_environment(monkeypatch):
    """One session must be able to compare cities without a restart."""
    monkeypatch.setattr(server._settings, "region", "RU-MOW")
    stub_json(monkeypatch, {"/v2/products/": CARD_PAYLOAD})

    result = await server.detmir_card(product_id=123, region="RU-KHA")
    assert result.region == "RU-KHA"


async def test_region_is_normalised_to_upper_case(monkeypatch):
    stub_json(monkeypatch, {"/v2/products/": CARD_PAYLOAD})

    result = await server.detmir_card(product_id=123, region="ru-spe")
    assert result.region == "RU-SPE"


@pytest.mark.parametrize(
    "bad_region",
    ["moscow", "RU_MOW", "'; drop--", "RU-MOW;level:1", "R", "RU-"],
)
async def test_an_invalid_region_is_rejected_before_any_request(monkeypatch, bad_region):
    """The region lands in a semicolon-delimited filter, so it is validated."""

    async def forbidden(url: str, label: str, ctx=None):
        raise AssertionError(f"{bad_region!r} must be rejected before the network")

    monkeypatch.setattr(server, "_fetch_json", forbidden)

    with pytest.raises(ToolError) as excinfo:
        await server.detmir_card(product_id=123, region=bad_region)
    assert error_payload(excinfo.value)["error"] == "bad_request"


async def test_category_listing_passes_the_region_through(monkeypatch):
    seen = {}

    async def capture(url: str, label: str, ctx=None):
        seen["url"] = url
        return CATEGORY_PAYLOAD

    monkeypatch.setattr(server, "_fetch_json", capture)

    result = await server.detmir_category(alias="pups", region="RU-SPE")
    assert result.region == "RU-SPE"

    decoded = urllib.parse.unquote(seen["url"])
    assert "withregion:RU-SPE" in decoded
    assert "categories[].alias:pups" in decoded


async def test_categories_tree_passes_the_region_through(monkeypatch):
    seen = {}

    async def capture(url: str, label: str, ctx=None):
        seen["url"] = url
        return CATEGORIES_PAYLOAD

    monkeypatch.setattr(server, "_fetch_json", capture)

    result = await server.detmir_categories(region="RU-KHA")
    assert result.region == "RU-KHA"

    assert "withregion:RU-KHA" in urllib.parse.unquote(seen["url"])


async def test_different_regions_do_not_share_a_cache_entry(monkeypatch):
    """Region lives in the URL, and the cache keys on URL — so cities stay separate."""
    urls: list[str] = []

    async def capture(url: str, label: str, ctx=None):
        urls.append(url)
        return CARD_PAYLOAD

    monkeypatch.setattr(server, "_fetch_json", capture)

    await server.detmir_card(product_id=123, region="RU-MOW")
    await server.detmir_card(product_id=123, region="RU-SPE")

    assert len(urls) == 2
    assert urls[0] != urls[1], "a St Petersburg request must not be answerable from Moscow's cache"


async def test_all_four_tools_are_still_registered():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert names == {"detmir_card", "detmir_category", "detmir_categories", "detmir_selfcheck"}
