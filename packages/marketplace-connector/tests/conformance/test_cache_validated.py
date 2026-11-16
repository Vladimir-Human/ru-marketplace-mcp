"""Rejected responses must not poison the next public tool call inside its TTL.

START_MODULE_CONTRACT
PURPOSE: Prove R-CACHE-VALIDATED at transport seams, with real parsing and validation.
SCOPE: Every registry server with a module-level TTLCache; no live I/O or parser fakes.
DEPENDS: pytest; stdlib importlib,json,copy,pathlib; mcp_core.cache,sources.
LINKS: M-SOURCES; M-CACHE; R-CACHE-VALIDATED.
END_MODULE_CONTRACT
START_MODULE_MAP
Case: Describe one source's public tool and offline transport responses.
responses: Build captured API bodies or representative extracted DOM payloads.
test_rejected_response_is_not_cached: Bad then good, and a cached positive control.
test_every_response_cache_has_a_case: Fail closed when a registry cache lacks coverage.
END_MODULE_MAP
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from mcp_core.cache import TTLCache
from mcp_core.sources import BY_NAME, SOURCES
from pydantic import SecretStr

# START_BLOCK_CASES
ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class Case:
    source: str
    tool: str
    transport: str
    args: dict[str, Any]
    error: str
    good_path: tuple[str | int, ...]
    good_value: Any
    variant: str = ""
    sync: bool = False


CASES = (
    Case(
        "avito",
        "avito_search",
        "_sync_curl_get",
        {"query": "ноутбук"},
        "transport_down",
        ("items", 0, "title"),
        "Ноутбук Lenovo ThinkBook 16 G6 Новый",
        sync=True,
    ),
    Case(
        "avito",
        "avito_search",
        "_cdp_fetch",
        {"query": "ноутбук"},
        "transport_down",
        ("items", 0, "title"),
        "Ноутбук Lenovo ThinkBook 16 G6 Новый",
        variant="cdp",
    ),
    Case(
        "aliexpress",
        "aliexpress_card",
        "_cdp_card",
        {"item_id_or_url": "1005010003103368"},
        "parser_drift",
        ("title",),
        "Realme Watch 5 смарт-часы",
    ),
    Case(
        "aliexpress",
        "aliexpress_card",
        "_cdp_card",
        {"item_id_or_url": "1005010003103368"},
        "price_missing",
        ("price_rub",),
        2939.0,
        variant="no-price",
    ),
    Case(
        "aliexpress",
        "aliexpress_card",
        "_cdp_card",
        {"item_id_or_url": "1005010003103368"},
        "title_missing",
        ("title",),
        "Realme Watch 5 смарт-часы",
        variant="no-title",
    ),
    Case(
        "aliexpress",
        "aliexpress_search",
        "_cdp_render_search",
        {"query": "realme watch"},
        "parser_drift",
        ("items", 0, "price_rub"),
        2939.0,
        variant="search",
    ),
    Case(
        "aliexpress",
        "aliexpress_search",
        "_cdp_render_search",
        {"query": "realme watch"},
        "transport_down",
        ("items", 0, "price_rub"),
        2939.0,
        variant="search-invalid-tile",
    ),
    Case(
        "lamoda",
        "lamoda_search",
        "open_page",
        {"query": "sneakers"},
        "parser_drift",
        ("items", 0, "title"),
        "Sneakers",
        variant="search",
    ),
    Case(
        "lamoda",
        "lamoda_card",
        "_graphql_via_https",
        {"sku_or_url": "MP002XM0VY5M"},
        "transport_down",
        ("title",),
        "Sneakers",
        variant="invalid-product",
    ),
    Case(
        "yandex_market",
        "yandex_card",
        "get_text_with_retries",
        {"product_id": "763970960"},
        "parser_drift",
        ("title",),
        "Стиральная машина узкая Tuvio, инверторный мотор, 14 программ, 7 кг, WFS74MW61",
    ),
    Case(
        "wildberries",
        "wb_category_products",
        "_impersonated_get_text",
        {"shard": "electronic58", "query": "cat=9845"},
        "parser_drift",
        ("items", 0, "nm_id"),
        1280469586,
    ),
    Case(
        "ozon",
        "ozon_search",
        "_run_sync_bounded",
        {"query": "iphone"},
        "parser_drift",
        ("items", 0, "sku"),
        3497076095,
    ),
    Case(
        "ozon",
        "ozon_reviews",
        "_run_sync_bounded",
        {"sku_or_path": "123456"},
        "parser_drift",
        ("reviews", 0, "text"),
        "Good review",
        variant="reviews",
    ),
    Case(
        "ozon",
        "ozon_reviews",
        "_run_sync_bounded",
        {"sku_or_path": "123456"},
        "transport_down",
        ("reviews", 0, "text"),
        "Good review",
        variant="invalid-review",
    ),
    Case(
        "detsky_mir",
        "detmir_card",
        "get_text_with_retries",
        {"product_id": 1000},
        "not_found",
        ("product", "price_rub"),
        699.0,
    ),
    Case(
        "cian",
        "cian_card",
        "_cdp_page_config",
        {"offer_id_or_url": "331002424"},
        "parser_drift",
        ("offer_id",),
        331002424,
    ),
    Case(
        "dns",
        "dns_card",
        "_cdp_render",
        {"product_url": "https://www.dns-shop.ru/product/b7a1667f9b19ed20/"},
        "parser_drift",
        ("title",),
        "Ноутбук Lenovo IdeaPad 3",
    ),
    Case(
        "citilink",
        "citilink_card",
        "_cdp_render",
        {"product_url": "https://www.citilink.ru/product/noutbuk-123456/"},
        "parser_drift",
        ("title",),
        "Ноутбук Lenovo IdeaPad 3",
    ),
    Case(
        "taobao", "taobao_card", "_cdp_render", {"item_id_or_url": "123456789012"}, "parser_drift", ("title",), "Phone"
    ),
    Case(
        "lamoda",
        "lamoda_card",
        "_graphql_via_https",
        {"sku_or_url": "MP002XM0VY5M"},
        "parser_drift",
        ("title",),
        "Sneakers",
    ),
    Case(
        "megamarket",
        "megamarket_card",
        "_cdp_post_json",
        {"item_id_or_url": "123456"},
        "not_found",
        ("title",),
        "Phone",
    ),
    Case(
        "mpstats",
        "mpstats_item",
        "_post_json_budgeted",
        {"skus": [5107857210], "place": "ozon"},
        "parser_drift",
        ("items", 0, "sku"),
        5107857210,
    ),
)

# No caching connector is exempt: credentials, Chrome and multi-tier reads can
# all be driven offline at their transport seams. Non-response caches such as
# WB's _page1_fingerprints are metadata, covered by the source's response case.
EXEMPTIONS: dict[str, str] = {}


def fixture(source: str, name: str) -> str:
    return (ROOT / "packages" / BY_NAME[source].package / "tests" / "fixtures" / name).read_text(encoding="utf-8")


def responses(case: Case) -> tuple[Any, Any]:
    """Only HTML is captured markup; DOM dictionaries simulate the transport output."""
    if case.source == "avito":
        return (200, '{"status":"error","error":{"message":"upstream unavailable"}}'), (
            200,
            fixture("avito", "js_items_live.json"),
        )
    if case.source == "aliexpress":
        good = {
            "punish": False,
            "title": "Realme Watch 5 смарт-часы",
            "price_texts": {"attached": ["2 939 ₽", "6 967 ₽"], "other": []},
            "rating_text": "4,7",
            "meta_description": "➜ 2334 заказов",
        }
        bad = {**good, "title": None, "price_texts": {"attached": [], "other": []}}
        if case.variant == "no-price":
            bad["title"] = good["title"]
        if case.variant == "no-title":
            bad["price_texts"] = good["price_texts"]
        if case.variant.startswith("search"):
            good = {
                "punish": False,
                "items": [
                    {
                        "item_id": "1005010003103368",
                        "title": good["title"],
                        "price_texts": good["price_texts"],
                        "rating": "4,7",
                        "orders": "2334",
                        "url": "https://aliexpress.ru/item/1005010003103368.html",
                    }
                ],
            }
            bad = {"punish": False, "items": []}
            if case.variant == "search-invalid-tile":
                bad = deepcopy(good)
                bad["items"][0]["sku_id"] = {"unexpected": "object"}
        return bad, good
    if case.source == "yandex_market":
        good = fixture(case.source, "card_washer.html")
        bad = good.replace('"titles"', '"titlesGone"').replace('"title"', '"titleGone"').replace('"name"', '"nameGone"')
        return (200, bad), (200, good)
    if case.source == "wildberries":
        return (200, "{}", None), (200, fixture(case.source, "card_v4_live.json"), None)
    if case.source == "ozon":
        if case.variant:
            reviews = {"reviews": [{"itemId": 123456, "content": {"score": 5, "comment": "Good review"}}]}
            good = {"widgetStates": {"webListReviews-1": json.dumps(reviews)}}
            bad = {}
            if case.variant == "invalid-review":
                reviews["reviews"][0]["content"]["score"] = {"unexpected": "object"}
                bad = {"widgetStates": {"webListReviews-1": json.dumps(reviews)}}
            return (200, json.dumps(bad)), (200, json.dumps(good))
        return (200, "{}"), (200, fixture(case.source, "search_composer_live.json"))
    if case.source == "detsky_mir":
        return (200, '{"status":404}'), (200, fixture(case.source, "card_live.json"))
    if case.source == "cian":
        captured = json.loads(fixture(case.source, "card_sale_live.json"))
        return (200, '{"kind":"ok","offerData":null}'), (
            200,
            json.dumps({"kind": "ok", "offerData": captured["offerData"]}),
        )
    if case.source in ("dns", "citilink"):
        return {}, {
            "title": "Ноутбук Lenovo IdeaPad 3",
            "price_rub": 52999.0,
            "price_text": "52 999 ₽",
            "price_texts": {"attached": ["52 999 ₽"], "other": []},
            "is_available": True,
        }
    if case.source == "taobao":
        return {}, {"title": "Phone", "price_cny": 1999, "page_title": "淘宝", "description_images": 0}
    if case.source == "lamoda":
        if case.variant == "search":
            return {"items": []}, {
                "items": [
                    {"sku": "MP002XM0VY5M", "title": "Sneakers", "price_texts": {"attached": ["2 999 ₽"], "other": []}}
                ]
            }
        good = {"error": None, "result": [{"sku": "MP002XM0VY5M", "name": "Sneakers", "price_amount": 2999}]}
        bad = {"error": "Internal server error"}
        if case.variant == "invalid-product":
            bad = deepcopy(good)
            bad["result"][0]["name"] = {"unexpected": "object"}
        return (200, json.dumps(bad)), (200, json.dumps(good))
    if case.source == "megamarket":
        return (200, '{"goods":{}}'), (200, '{"goods":{"title":"Phone","price":1999}}')
    if case.source == "mpstats":
        return (200, '{"code":200,"items":null}', None), (
            200,
            '{"code":200,"items":{"5107857210":{"Sku":5107857210,"pricesGraph":[1999]}}}',
            None,
        )
    raise AssertionError(case.source)


# END_BLOCK_CASES
# START_BLOCK_CONFORMANCE


def value_at(value: Any, path: tuple[str | int, ...]) -> Any:
    for key in path:
        value = value[key]
    return value


@pytest.mark.req("R-CACHE-VALIDATED")
@pytest.mark.parametrize("case", CASES, ids=lambda case: f"{case.source}-{case.variant or case.tool}")
async def test_rejected_response_is_not_cached(case: Case, monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module(BY_NAME[case.source].module)
    # A real, enabled cache with the production capacity isolates tests.
    # The expiration counter below rules out accidental TTL-based recovery.
    monkeypatch.setattr(module, "_cache", TTLCache(ttl_s=300, max_entries=module._cache._max))
    if hasattr(module, "_min_gap"):
        monkeypatch.setattr(module, "_min_gap", 0.0)
    if case.source == "mpstats":
        # Not a credential: an inert test setting; the fake POST is the only I/O.
        monkeypatch.setattr(module._settings, "mp_auth", SecretStr("offline-test-placeholder"))
    bad, good = responses(case)
    calls: list[Any] = []

    def reply(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        assert len(calls) <= 2, "a validated response must be cached"
        return deepcopy(bad if len(calls) == 1 else good)

    async def async_reply(*args: Any, **kwargs: Any) -> Any:
        return reply(*args, **kwargs)

    if case.transport == "open_page":

        @asynccontextmanager
        async def fake_page(*args: Any, **kwargs: Any):
            payload = reply(*args, **kwargs)

            class Page:
                async def evaluate(self, script: str) -> str:
                    return json.dumps(payload)

            yield Page()

        monkeypatch.setattr(module, "open_page", fake_page)
    else:
        monkeypatch.setattr(module, case.transport, reply if case.sync else async_reply)
    if case.variant == "cdp":
        # Deliberately unavailable anonymous tier; only CDP supplies payloads.
        def unavailable(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("offline tier-1 unavailable")

        monkeypatch.setattr(module, "_sync_curl_get", unavailable)
    tool = getattr(module, case.tool)
    if case.error in ("price_missing", "title_missing"):
        first = await tool(**case.args)
        if case.error == "price_missing":
            assert first.price_rub is None
            assert any(w.startswith("price_missing:") for w in first.meta.warnings)
        else:
            assert first.title is None  # Preserve the existing partial-card contract.
    else:
        with pytest.raises(ToolError) as caught:
            await tool(**case.args)
        assert json.loads(str(caught.value))["error"] == case.error
    assert len(calls) == 1
    rejected_cache_entries = len(module._cache)

    second = await tool(**case.args)
    data = second.model_dump(by_alias=True)
    actual = value_at(data, case.good_path)
    assert actual == case.good_value
    assert len(calls) == 2, "the repaired response must come from transport inside the TTL"
    assert rejected_cache_entries == 0, "rejected data must never enter the response cache"
    assert len(module._cache) == 1
    third = await tool(**case.args)
    assert value_at(third.model_dump(by_alias=True), case.good_path) == actual
    assert len(calls) == 2, "positive control: validated data must still be cached"
    assert module._cache.stats.expirations == 0, "all calls must remain inside the TTL"


@pytest.mark.req("R-CACHE-VALIDATED")
def test_every_response_cache_has_a_case() -> None:
    covered = {case.source for case in CASES}
    caching = set()
    for source in SOURCES:
        module = importlib.import_module(source.module)
        if any(isinstance(value, TTLCache) for value in vars(module).values()):
            caching.add(source.name)
    assert caching <= covered | EXEMPTIONS.keys(), f"missing cache cases: {caching - covered - EXEMPTIONS.keys()}"
    assert not covered - caching, "a case must exercise a real module-level cache"
    assert all(reason.strip() for reason in EXEMPTIONS.values())
    assert not covered & EXEMPTIONS.keys(), "covered sources need no exemption"


# END_BLOCK_CONFORMANCE
