"""Fixture-measured budgets for the full unified server, never live traffic.

START_MODULE_CONTRACT
PURPOSE: Guard R-INTERFACE-GUARDS.output-size on real MCP structured content.
SCOPE: Registry-mounted listing/card tools, committed fixtures, declared coverage gaps;
       transport-only fakes, no manufactured full pages, no runtime cutting changes.
DEPENDS: FastMCP; pytest; mcp_core.sources/cache/domtest; existing conformance adapters.
LINKS: M-INTERFACE-GUARDS; M-SOURCES; R-INTERFACE-GUARDS.output-size.
END_MODULE_CONTRACT
START_MODULE_MAP
build_full_server: Copy the unified assembly with every registry mount, without a key.
Sample/Budget: Fixture inputs and dated per-tool measurements with explicit gaps.
install_sample: Reuse offline transports and real DOM extractors/parsers.
observe_tool: Call each measured tool through the unified MCP client.
assert_output_fits: Measure the complete structured JSON, including SDK envelopes.
test_*: Check inventory/budgets, fixture outputs, and a planted oversize answer.
END_MODULE_MAP
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from functools import wraps
from math import ceil
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from mcp_core.cache import TTLCache
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.output_schema import registered_tools
from mcp_core.sources import BY_NAME, SOURCES

from .test_selfcheck_gates_price import ADAPTERS, fixture_path

# START_BLOCK_FULL_PROFILE


def build_full_server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """Use the shipped mount path on a fresh server, not an environment subset.

    No key or token is configured, and all global assembly state is restored.
    """
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    marketplace = importlib.import_module("marketplace_connector.server")
    app = FastMCP("interface-guards-full")
    monkeypatch.setattr(marketplace, "mcp", app)
    for attr, value in (("_MOUNTED", []), ("_SKIPPED", {}), ("_MOUNTED_MODULES", {})):
        monkeypatch.setattr(marketplace, attr, value)
    marketplace._mount_all()
    assert not marketplace._SKIPPED, marketplace._SKIPPED
    assert set(marketplace._MOUNTED) == {s.mount for s in SOURCES if s.mount is not None}
    app.add_tool(marketplace.marketplace_sources)
    app.add_tool(marketplace.marketplace_health)
    return app


# END_BLOCK_FULL_PROFILE
# START_BLOCK_MEASUREMENTS
MEASURED_ON = "2026-11-17"
HARD_CAP = 40_000
ADAPTER_BY_SOURCE = {adapter.source: adapter for adapter in ADAPTERS}


@dataclass(frozen=True)
class Sample:
    source: str
    tool: str
    fixture: str
    args: dict[str, Any]
    kind: str = "listing"


@dataclass(frozen=True)
class Budget:
    measured: int | None
    fixture: str
    gap: str
    measured_on: str = MEASURED_ON

    @property
    def cap(self) -> int | None:
        return min(HARD_CAP, ceil(self.measured * 1.25)) if self.measured is not None else None


# Only committed successful fixture responses are measured. Refusal/removed-page
# captures yield ToolError, not structured_content; they cannot bound a success.
# Never multiply rows or synthesize missing card bodies to claim a maximum page.
LISTING_FIXTURES = {
    "wildberries": ("search_v9_live.json",),
    "ozon": ("search_composer_live.json",),
    "yandex_market": ("search_washer.html", "search_iphone.html", "search_kettle.html", "search_telefon_zone.html"),
    "detsky_mir": ("category_live.json",),
    "avito": ("js_items_live.json", "js_items_reserved.json", "js_items_sale.json"),
    "taobao": ("search_grid_live.html", "search_grid_iphone15_live.html", "search_grid.html"),
    "megamarket": ("search_live.json",),
    "lamoda": ("search_grid_live.html", "search_grid.html"),
    "dns": ("search_grid.html",),
    "citilink": ("search_grid.html",),
    "aliexpress": ("search_grid.html",),
    "cian": ("search_sale_live.json", "search_rent_live.json", "search_daily_live.json"),
}


def listing_samples() -> tuple[Sample, ...]:
    samples = []
    for source in SOURCES:
        for name in LISTING_FIXTURES.get(source.name, ()):
            args = {"query": "fixture query"}
            if source.name == "detsky_mir":
                args = {"alias": "pups", "region": "RU-MOW"}
            elif source.name == "cian":
                args = {"deal": "sale" if "sale" in name else "rent"}
                if "daily" in name:
                    args["deal"] = "daily"
            assert source.listing_tool is not None
            samples.append(Sample(source.name, source.listing_tool, name, args))
    return tuple(samples)


CARD_SAMPLES = (
    Sample("wildberries", "wb_card", "card_v4_live.json", {}, "wb-api"),
    Sample(
        "yandex_market", "yandex_card", "card_washer.html", {"product_id": "763970960", "include_reviews": True}, "card"
    ),
    Sample(
        "yandex_market",
        "yandex_card",
        "card_no_rating.html",
        {"product_id": "763970960", "include_reviews": True},
        "card",
    ),
    Sample("detsky_mir", "detmir_card", "card_live.json", {"product_id": 1000, "region": "RU-MOW"}, "card"),
    Sample("taobao", "taobao_card", "item_card_live.html", {"item_id_or_url": "1048008062319"}, "card"),
    Sample("taobao", "taobao_card", "item_card.html", {"item_id_or_url": "123456789012"}, "card"),
    Sample(
        "dns",
        "dns_card",
        "card.html",
        {"product_url": "https://www.dns-shop.ru/product/b7a1667f9b19ed20/noutbuk-huawei/"},
        "card",
    ),
    Sample(
        "citilink",
        "citilink_card",
        "card.html",
        {"product_url": "https://www.citilink.ru/product/noutbuk-lenovo-loq-2166619/"},
        "card",
    ),
    Sample(
        "citilink",
        "citilink_card",
        "card_out_of_stock.html",
        {"product_url": "https://www.citilink.ru/product/noutbuk-lenovo-loq-2166619/"},
        "card",
    ),
    Sample("aliexpress", "aliexpress_card", "card.html", {"item_id_or_url": "1005010003103368"}, "card"),
    *(Sample("cian", "cian_card", f"card_{deal}_live.json", {}, "card") for deal in ("sale", "rent", "daily")),
    *(
        Sample("avito", "avito_card", f"{stem}_{item_id}.html", {"item_id_or_url": str(item_id), "photos": 0}, "card")
        for stem, item_id in (
            ("item_page", 7924452716),
            ("item_page_dom", 7931870037),
            ("item_details_company", 9000000033),
            ("item_details_private", 9000000018),
            ("item_state_active", 9000000012),
            ("item_state_reserved", 9000000011),
            ("item_photos_active", 9000000020),
            ("item_sale", 9000000041),
        )
    ),
)
EXTRA_SAMPLES = (
    Sample(
        "wildberries",
        "wb_category_products",
        "card_v4_live.json",
        {"shard": "electronic58", "query": "cat=9845"},
        "category",
    ),
    Sample(
        "avito",
        "avito_seller",
        "seller_profile_brands_27696b66.html",
        {"seller_id_or_url": "https://www.avito.ru/brands/27696b66"},
        "seller",
    ),
)
SAMPLES = listing_samples() + CARD_SAMPLES + EXTRA_SAMPLES

# The sole budget table: tool -> largest observed fixture size -> ceil(size*1.25),
# capped at 40000. None is an honest unmeasured gap, NEVER a default 40000 cap.
# Measurements are of actual structured_content (aliases and SDK wrappers intact).
BUDGETS: dict[str, Budget] = {
    "wb_search": Budget(
        2246,
        "search_v9_live.json",
        "3 captured rows; page=20 replays a first-page capture, not a captured last/full page.",
    ),
    "wb_card": Budget(
        805,
        "card_v4_live.json",
        "API capture has 1 row, below schema maxItems=100; DOM fixtures contain only other-seller fragments, not complete measurable cards.",
    ),
    "wb_category_products": Budget(
        915,
        "card_v4_live.json",
        "Card/v4 capture reused as in test_cache_validated; no captured category/full 100-item page.",
    ),
    "ozon_search": Budget(
        2144,
        "search_composer_live.json",
        "3 captured rows; schema has no numeric page maximum (prose says 10); use page=10, no last/full-page capture.",
    ),
    "ozon_card": Budget(None, "", "No committed card fixture; cache tests synthesize composer/card responses."),
    "yandex_search": Budget(
        2131,
        "search_washer.html",
        "Captures do not reach limit=48 or represent page=30; replay the available rows without padding.",
    ),
    "yandex_card": Budget(
        3327,
        "card_washer.html",
        "Reviews limited to the captured server-rendered sample, not every possible card/review length.",
    ),
    "detmir_category": Budget(
        1972, "category_live.json", "3 captured rows, not limit=60; offset=10000 replays the captured first page."
    ),
    "detmir_card": Budget(696, "card_live.json", "One captured product, not all possible card field lengths."),
    "avito_search": Budget(
        1846,
        "js_items_live.json",
        "Short captured listings; page=100 replays first-page fixtures, not a captured last/full page.",
    ),
    "avito_card": Budget(
        2158,
        "item_page_dom_7931870037.html",
        "Structured card fields only; photos=0, no image downloads or maximum-photo evidence.",
    ),
    "avito_seller": Budget(
        393, "seller_profile_brands_27696b66.html", "One captured organization profile; no all-profile bound."
    ),
    "taobao_search": Budget(
        1126,
        "search_grid_iphone15_live.html",
        "Short captured/modelled grids; page=100 has no captured last/full-page evidence.",
    ),
    "taobao_card": Budget(346, "item_card.html", "Captured and pre-existing modelled card; no all-card length bound."),
    "megamarket_search": Budget(
        1532, "search_live.json", "Short captured listing; no page/limit parameter and no full-page fixture."
    ),
    "megamarket_card": Budget(
        None, "", "No committed card fixture; existing cache test uses an invented minimal goods body."
    ),
    "lamoda_search": Budget(
        754,
        "search_grid_live.html",
        "Captured and pre-existing modelled grids; no page/limit parameter or full-page fixture.",
    ),
    "lamoda_card": Budget(
        None, "", "No committed GraphQL card fixture; existing cache tests invent a minimal product."
    ),
    "dns_search": Budget(748, "search_grid.html", "Short captured grid; no page/limit parameter or full-page fixture."),
    "dns_card": Budget(332, "card.html", "One captured card, not all possible card field lengths."),
    "citilink_search": Budget(
        964, "search_grid.html", "Short captured grid; no page/limit parameter or full-page fixture."
    ),
    "citilink_card": Budget(530, "card.html", "Captured in/out-of-stock cards, not all possible card field lengths."),
    "aliexpress_search": Budget(
        1939, "search_grid.html", "Short captured grid; no page/limit parameter or full-page fixture."
    ),
    "aliexpress_card": Budget(
        416, "card.html", "Captured SSR shell has no price module; real tool returns a warned partial card."
    ),
    "cian_search": Budget(
        2584,
        "search_sale_live.json",
        "Short sale/rent/daily captures, not a 28-offer full page; page=100 replays earlier pages.",
    ),
    "cian_card": Budget(
        2362, "card_rent_live.json", "Sale/rent/daily captures only; no all-description/history-length bound."
    ),
    "compare_prices": Budget(
        27818,
        "wildberries/search_v9_live.json + ozon/search_composer_live.json + yandex_market/search_washer.html + avito/js_items_live.json + taobao/search_grid_iphone15_live.html + megamarket/search_live.json + lamoda/search_grid_live.html + dns/search_grid.html + citilink/search_grid.html + aliexpress/search_grid.html",
        "per_source_limit=20, but short native fixtures cannot supply 20 offers each; only the largest-native-fixture combination is measured "
        "(30 offers, about 680 characters each, about 7,200 outside offers): 20 offers from each of 10 sources would be about "
        "143,000 characters, so the answer leaves whole offers out at 40,000 (compare-connector tests/test_answer_size.py).",
    ),
    "compare_verify_offer": Budget(
        2341,
        "item_page_dom_7931870037.html",
        "Only sources with captured native cards are measured; Ozon/Lamoda/Megamarket cards lack fixtures.",
    ),
    "wb_root_info": Budget(None, "", "No committed basket info fixture."),
    "wb_reviews": Budget(None, "", "No committed reviews fixture at limit=100 or any smaller page."),
    "wb_questions": Budget(None, "", "No committed questions fixture at limit=100 or any smaller page."),
    "wb_seller": Budget(None, "", "No committed supplier fixture."),
    "wb_categories": Budget(None, "", "No committed category-tree fixture at max_depth=3."),
    "ozon_reviews": Budget(
        None,
        "",
        "No committed review fixture; existing tests invent review bodies (schema lacks a numeric limit maximum).",
    ),
    "detmir_categories": Budget(None, "", "No committed category-discovery fixture at limit=100."),
    "marketplace_sources": Budget(None, "", "Local inventory, not a fixture-backed listing/card response."),
    "marketplace_health": Budget(
        None, "", "Local/browser diagnostics, excluded: no browser or health probe permitted."
    ),
    "compare_sources": Budget(None, "", "Local inventory, not a fixture-backed listing/card response."),
    "compare_browser_snapshot": Budget(None, "", "Browser/image tool, excluded: no browser or screenshot permitted."),
}

# END_BLOCK_MEASUREMENTS
# START_BLOCK_FIXTURE_TRANSPORTS
DOM_SEARCH_URLS = {
    "taobao": "https://s.taobao.com/search?q=fixture",
    "lamoda": "https://www.lamoda.ru/catalogsearch/result/?q=fixture",
    "dns": "https://www.dns-shop.ru/search/?q=fixture",
    "citilink": "https://www.citilink.ru/catalog/noutbuki/",
    "aliexpress": "https://aliexpress.ru/wholesale?SearchText=fixture",
}


def _extract(module: Any, sample: Sample, page_url: str, constant: str) -> Any:
    try:
        return run_extractor(getattr(module, constant), fixture_path(sample.source, sample.fixture), page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(f"{sample.tool}/{sample.fixture}: unproven DOM measurement: {exc}")


def maximum_arguments(tool: Any, sample: Sample) -> dict[str, Any]:
    args = dict(sample.args)
    properties = tool.input_schema.get("properties", {})
    for name in ("limit", "page", "offset", "per_source_limit"):
        if name in properties and "maximum" in properties[name]:
            args[name] = properties[name]["maximum"]
    # Ozon has no maximum in its input schema. This is a declared schema gap,
    # not a claim that default=1 is the maximum; the current public prose says 10.
    if sample.tool == "ozon_search":
        args["page"] = 10
    return args


def install_sample(sample: Sample, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    module = importlib.import_module(BY_NAME[sample.source].module)
    monkeypatch.setattr(module, "_cache", TTLCache(ttl_s=300, max_entries=64))
    if hasattr(module, "_min_gap"):
        monkeypatch.setattr(module, "_min_gap", 0.0)
    if sample.source == "wildberries":
        monkeypatch.setattr(module._settings, "transport", "api")
        # A page-1 fingerprint left by an earlier test would make the replayed
        # first page at page=20 read as WB wrapping past the end (no_results).
        monkeypatch.setattr(module, "_page1_fingerprints", TTLCache(ttl_s=300, max_entries=16))
    text = fixture_path(sample.source, sample.fixture).read_text(encoding="utf-8")
    payload: Any = json.loads(text) if sample.fixture.endswith(".json") else text
    args = dict(sample.args)
    if sample.kind == "listing":
        if sample.source in DOM_SEARCH_URLS:
            payload = _extract(module, sample, DOM_SEARCH_URLS[sample.source], "_SEARCH_EXTRACT_JS")
        ADAPTER_BY_SOURCE[sample.source].install(module, monkeypatch, payload)
        return args
    if sample.kind == "wb-api":
        args["nm_ids"] = [row["id"] for row in payload.get("products", payload.get("data", {}).get("products", []))]
        assert args["nm_ids"], "the captured batch must contain real ids, never duplicated padding"
    if sample.source in {"taobao", "dns", "citilink", "aliexpress"}:
        url = args.get("product_url") or (
            f"https://item.taobao.com/item.htm?id={args['item_id_or_url']}"
            if sample.source == "taobao"
            else f"https://aliexpress.ru/item/{args['item_id_or_url']}.html"
        )
        payload = _extract(module, sample, url, "_CARD_EXTRACT_JS")

    async def reply(*a: Any, **kw: Any) -> Any:
        if sample.kind in {"wb-api", "category"}:
            return 200, json.dumps(payload), None
        if sample.source == "avito":
            return 200, text, "cdp"
        return payload

    seam = {
        "wildberries": "_safe_get_text" if sample.kind == "wb-api" else "_impersonated_get_text",
        "yandex_market": "_fetch_html",
        "detsky_mir": "_fetch_json",
        "avito": "_fetch_item_page",
        "taobao": "_cdp_render",
        "dns": "_cdp_render",
        "citilink": "_cdp_render",
        "aliexpress": "_cdp_card",
        "cian": "_fetch_card",
    }[sample.source]
    if sample.source == "cian":
        # As in test_cache_validated: the capture is offerData, while the browser
        # seam wraps it with kind=ok. No offer field is fabricated or changed.
        provenance = json.loads(
            fixture_path(sample.source, sample.fixture.replace(".json", ".provenance.json")).read_text(encoding="utf-8")
        )
        args["offer_id_or_url"] = provenance["page"]
        envelope = {"kind": "ok", "offerData": payload["offerData"], "url": provenance["page"]}

        async def card_reply(*a: Any, **kw: Any) -> tuple[int, str, str]:
            return 200, json.dumps(envelope), "cdp"

        monkeypatch.setattr(module, seam, card_reply)
    else:
        monkeypatch.setattr(module, seam, reply)
    return args


# END_BLOCK_FIXTURE_TRANSPORTS
# START_BLOCK_OUTPUT_CHECKS


@dataclass(frozen=True)
class Observation:
    fixture: str
    args: dict[str, Any]
    size: int


def serialized_size(structured_content: Any) -> int:
    assert isinstance(structured_content, dict), "a successful MCP result must have structured_content"
    return len(json.dumps(structured_content, ensure_ascii=False))


def assert_size_fits(tool_name: str, size: int, fixture: str) -> None:
    budget = BUDGETS[tool_name]
    assert budget.cap is not None, f"{tool_name}: no measured fixture budget: {budget.gap}"
    assert size <= budget.cap, (
        f"{tool_name}/{fixture}: {size} characters exceeds cap={budget.cap} (measured={budget.measured}, hard cap={HARD_CAP})"
    )


def assert_output_fits(tool_name: str, structured_content: Any, fixture: str = "planted") -> int:
    size = serialized_size(structured_content)
    assert_size_fits(tool_name, size, fixture)
    return size


async def observe_tool(app: FastMCP, tool_name: str, monkeypatch: pytest.MonkeyPatch) -> list[Observation]:
    observations = []
    if tool_name in {"compare_prices", "compare_verify_offer"}:
        # Re-evaluate real discovery after clearing source selection: another
        # test may have imported compare under a restricted environment earlier.
        module = importlib.import_module(BY_NAME["compare"].module)
        monkeypatch.setattr(module, "SOURCES", module._available_sources())
    async with Client(app) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tool_name in tools
        samples = [sample for sample in SAMPLES if sample.tool == tool_name]
        if tool_name == "compare_prices":
            # Keep every real native parser and compare adapter. Select the
            # largest measured native fixture, not manufactured MarketOffers.
            native_samples = [
                next(s for s in SAMPLES if s.tool == source.listing_tool and s.fixture == BUDGETS[s.tool].fixture)
                for source in SOURCES
                if source.text_search and source.card_tool is not None
            ]
            with monkeypatch.context() as mp:
                for sample in native_samples:
                    install_sample(sample, mp)
                args = {
                    "query": "fixture query",
                    "sources": [s.source for s in native_samples],
                    "per_source_limit": tools[tool_name].input_schema["properties"]["per_source_limit"]["maximum"],
                }
                reply = await client.call_tool(tool_name, args)
                observations.append(
                    Observation(
                        " + ".join(f"{s.source}/{s.fixture}" for s in native_samples),
                        args,
                        serialized_size(reply.structured_content),
                    )
                )
        elif tool_name == "compare_verify_offer":
            samples = [s for s in CARD_SAMPLES if s.source != "cian"]
        for sample in samples:
            with monkeypatch.context() as mp:
                args = install_sample(sample, mp)
                if tool_name == "compare_verify_offer":
                    product_id = next(iter(args.values()))
                    if sample.source == "wildberries":
                        product_id = args["nm_ids"][0]
                    args = {"source": sample.source, "product_id_or_url": str(product_id), "expected_price_rub": 1.0}
                else:
                    args = maximum_arguments(
                        tools[tool_name], Sample(sample.source, sample.tool, sample.fixture, args, sample.kind)
                    )
                reply = await client.call_tool(tool_name, args)
                data = reply.structured_content
                assert isinstance(data, dict)
                body = data.get("result", data)
                if sample.kind in {"listing", "wb-api", "category"} and tool_name != "compare_verify_offer":
                    assert body.get("items"), f"{tool_name}/{sample.fixture}: must measure real nonempty fixture output"
                observations.append(Observation(sample.fixture, args, serialized_size(data)))
    assert observations, f"{tool_name}: a measured budget must have an executable fixture case"
    return observations


@pytest.mark.req("R-INTERFACE-GUARDS.output-size")
async def test_every_listed_tool_has_a_measured_budget_or_declared_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_full_server(monkeypatch)
    async with Client(app) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == BUDGETS.keys(), f"missing={names - BUDGETS.keys()}, stale={BUDGETS.keys() - names}"
    assert {s.listing_tool for s in SOURCES if s.listing_tool} <= names
    assert {s.card_tool for s in SOURCES if s.card_tool} <= names
    for name, budget in BUDGETS.items():
        assert budget.gap.strip(), f"{name}: state the fixture's maximum-page/coverage limitation"
        assert budget.measured_on == MEASURED_ON
        if budget.measured is not None:
            assert 0 < budget.measured <= HARD_CAP, (
                f"STOP: {name}/{budget.fixture} exceeds 40000; report before cutting code"
            )
            assert budget.fixture
            assert budget.cap == min(HARD_CAP, ceil(budget.measured * 1.25))


@pytest.mark.req("R-INTERFACE-GUARDS.output-size")
@pytest.mark.parametrize("tool_name", [name for name, b in BUDGETS.items() if b.measured is not None])
async def test_fixture_output_stays_within_its_measured_budget(tool_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_full_server(monkeypatch)
    for observation in await observe_tool(app, tool_name, monkeypatch):
        assert_size_fits(tool_name, observation.size, f"{observation.fixture} {observation.args}")


@pytest.mark.req("R-INTERFACE-GUARDS.output-size")
async def test_output_guard_rejects_a_planted_oversize_tool_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_full_server(monkeypatch)
    tool_name = "yandex_search"
    sample = next(s for s in SAMPLES if s.tool == tool_name)
    install_sample(sample, monkeypatch)
    module = importlib.import_module(BY_NAME[sample.source].module)
    tool = next(t for t in registered_tools(module.mcp) if t.name == tool_name)
    original = tool.fn
    cap = BUDGETS[tool_name].cap
    assert cap is not None

    @wraps(original)
    async def oversize(*args: Any, **kwargs: Any) -> Any:
        answer = await original(*args, **kwargs)
        return answer.model_copy(update={"query": "x" * (cap + 1)})

    monkeypatch.setattr(tool, "fn", oversize)
    async with Client(app) as client:
        reply = await client.call_tool(tool_name, sample.args)
    with pytest.raises(AssertionError, match=r"yandex_search.*exceeds cap"):
        assert_output_fits(tool_name, reply.structured_content)


# END_BLOCK_OUTPUT_CHECKS
