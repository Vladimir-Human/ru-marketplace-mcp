"""R-GUIDE-CONTRACTS: resources/read and public tools/call must tell the same story.

Only upstream transports are replaced. Real tools, parsers, comparison adapters,
MCP serialization and resource registration run unchanged. Captured HTML/JSON
fixtures are replayed offline alongside small synthetic API/DOM answers.
"""
# START_MODULE_CONTRACT
# PURPOSE: Verify every guide against actual MCP replies and synthetic source failures.
# SCOPE: Serialized fields/aliases/envelopes, conditional and partial prices, AliExpress challenges.
# DEPENDS: pytest; FastMCP Client; connector servers; existing offline fixtures.
# LINKS: M-RESOURCES; R-GUIDE-CONTRACTS; R-OFFLINE; R-NO-FABRICATION.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _prepare: Replace upstream reads, never public tool implementations.
# _check_fields: Compare documented keys/types with concrete MCP structured content.
# test_each_guide_matches_public_tool_answer: Exercise every reference resource and its real tool.
# test_aliexpress_challenge_guide_matches_wire_error: Check challenge code and healthy recovery.
# test_price_and_partial_notes_match_wire: Check Wallet, unknown, partial and metadata semantics.
# END_MODULE_MAP

from __future__ import annotations

import copy
import importlib
import json
import re
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from mcp_core.cache import TTLCache
from mcp_core.resources import GUIDES, guide_uri
from mcp_core.transport import browser_handoff, chrome_cdp

PACKAGES = Path(__file__).resolve().parents[2]
CALLS = {
    "aliexpress_card": {"item_id_or_url": "1005010003103368"},
    "aliexpress_search": {"query": "laptop"},
    "avito_card": {"item_id_or_url": "4612345678"},
    "avito_search": {"query": "laptop"},
    "avito_seller": {"seller_id_or_url": "seller123"},
    "cian_card": {"offer_id_or_url": "331002424"},
    "cian_search": {"deal": "sale"},
    "citilink_card": {"product_url": "1234567"},
    "citilink_search": {"query": "laptop"},
    "compare_browser_snapshot": {"handoff_id": "synthetic_handle_123", "include_image": True},
    "compare_prices": {"query": "laptop", "sources": ["wildberries"]},
    "compare_sources": {},
    "compare_verify_offer": {
        "source": "ozon",
        "product_id_or_url": "1234",
        "expected_price_rub": 1234,
        "expected_identity": {"brand": "Demo"},
    },
    "decision_inspect": {"source": "ozon", "product_id_or_url": "1234"},
    "detmir_card": {"product_id": 1234},
    "detmir_categories": {},
    "detmir_category": {"alias": "toys"},
    "dns_card": {"product_url": "b7a1667f9b19ed20"},
    "dns_search": {"query": "laptop"},
    "lamoda_card": {"sku_or_url": "MP002XM1RMM3"},
    "lamoda_search": {"query": "laptop"},
    "marketplace_health": {},
    "marketplace_sources": {},
    "megamarket_card": {"item_id_or_url": "1234567"},
    "megamarket_search": {"query": "laptop"},
    "mpstats_item": {"skus": [1234], "place": "ozon"},
    "mpstats_warehouses": {"skus": [1234], "place": "ozon"},
    "ozon_card": {"sku_or_path": "1234"},
    "ozon_reviews": {"sku_or_path": "1234", "limit": 2},
    "ozon_search": {"query": "laptop"},
    "taobao_card": {"item_id_or_url": "123456789012"},
    "taobao_search": {"query": "laptop"},
    "wb_card": {"nm_ids": [1234]},
    "wb_categories": {},
    "wb_category_products": {"shard": "electronic", "query": "cat=1"},
    "wb_questions": {"imt_id": 1234},
    "wb_reviews": {"imt_id": 1234},
    "wb_root_info": {"nm_id": 1234},
    "wb_search": {"query": "laptop"},
    "wb_seller": {"supplier_id": 1234},
    "yandex_card": {"product_id": "4315891968"},
    "yandex_search": {"query": "laptop"},
}


# START_BLOCK_SYNTHETIC_UPSTREAM
class OfflineNetworkGuard(BaseException):
    """A forgotten transport stub must fail, not become an expected tool error."""


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise OfflineNetworkGuard("guide contracts must not open a browser or a network connection")

    monkeypatch.setattr(chrome_cdp, "open_page", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    monkeypatch.setenv("COMPARE_SNAPSHOT_IMAGES", "auto")


def _fixture(source, name):
    return (PACKAGES / f"{source}-connector/tests/fixtures" / name).read_text(encoding="utf-8")


def _stub(monkeypatch, module, name, payload):
    async def answer(*args, **kwargs):
        return copy.deepcopy(payload)

    monkeypatch.setattr(module, name, answer)


def _module(tool_name):
    if tool_name == "decision_inspect":
        return importlib.import_module("compare_connector.decision_server")
    return importlib.import_module(f"{tool_name.split('_', 1)[0]}_connector.server")


def _prepare(monkeypatch, tool_name, mode="normal"):
    module = _module(tool_name)
    prefix = tool_name.split("_", 1)[0]
    if hasattr(module, "_cache"):
        monkeypatch.setattr(module, "_cache", TTLCache(ttl_s=60))
    if hasattr(module, "_polite_wait"):
        _stub(monkeypatch, module, "_polite_wait", None)
    if prefix in ("compare", "decision"):
        _prepare(monkeypatch, "ozon_card", mode)
        _prepare(monkeypatch, "wb_search", mode)
        _stub(
            monkeypatch,
            browser_handoff,
            "snapshot_handoff",
            {
                "image_data": "/9j/2Q==",
                "mime_type": "image/jpeg",
                "width": 1,
                "height": 1,
                "captured_at": "2026-11-02T00:00:00+00:00",
                "handoff_expires_at": "2026-11-02T00:01:00+00:00",
                "operation": "synthetic",
                "page_origin": "https://example.com",
            },
        )
    elif prefix == "wb":
        monkeypatch.setattr(module, "_settings", module._settings.model_copy(update={"transport": "api"}))
        product = {
            "id": 1234,
            "name": "Demo laptop",
            "brand": "Demo",
            "supplier": "Shop",
            "supplierId": 3,
            "totalQuantity": 2,
            "sizes": [{"price": {"product": 123400, "basic": 150000}}],
        }
        if mode == "unknown":
            product["sizes"] = []
            product.pop("totalQuantity")

        async def wb_read(client, url, *args, **kwargs):
            if "feedbacks" in url:
                data = {
                    "feedbacks": [{"productValuation": 5, "text": "Useful", "wbUserDetails": {"name": "Buyer"}}],
                    "feedbackCount": 1,
                    "valuation": 5,
                    "valuationDistribution": {"5": 1},
                }
            elif "questions" in url:
                data = {"count": 1, "questions": [{"id": "q1", "text": "Size?", "answer": {"text": "Large"}}]}
            elif "card.json" in url:
                data = {"imt_id": 1234, "options": [{"name": "Material", "value": "Steel"}]}
            elif "supplier" in url:
                data = {"supplierName": "Shop", "inn": "1234567890"}
            elif "menu" in url:
                data = [{"id": 1, "name": "Electronics", "shard": "electronic", "query": "cat=1", "childs": []}]
            else:
                data = {"products": [product], "total": 1}
            return 200, json.dumps(data), None

        monkeypatch.setattr(module, "_safe_get_text", wb_read)
        if mode == "wallet":
            monkeypatch.setattr(module, "_settings", module._settings.model_copy(update={"transport": "dom"}))
            _stub(
                monkeypatch,
                module.dom_transport,
                "render",
                {
                    "title": "Wildberries",
                    "tiles": [
                        {
                            "nm": "1234",
                            "label": "Demo laptop",
                            "lines": ["1 000 ₽", "1 500 ₽", "с WB Кошельком", "Demo", "/", "Demo laptop"],
                            "del": ["1 500 ₽"],
                            "wallet": [],
                        }
                    ],
                },
            )
    elif prefix == "ozon":
        review = {"itemId": 1234, "score": 4, "content": {"comment": "Useful"}, "author": {"firstName": "Buyer"}}
        paging = {"total": 1}
        if mode == "partial":
            paging = {"total": 2, "nextButton": "?page=2&page_key=abc&sort=published_at_desc"}
        widgets = {
            "webPrice-1": {"price": "1 234 ₽", "cardPrice": "1 000 ₽", "originalPrice": "1 500 ₽", "isAvailable": True},
            "webProductHeading-1": {"title": "Demo laptop"},
            "webListReviews-1": {"paging": paging, "reviews": [review]},
            "webReviewProductScore-1": {"totalScore": 4, "reviewsCount": 1, "score": []},
            "tileGridDesktop-1": {
                "items": [
                    {
                        "sku": 1234,
                        "mainState": [
                            {"type": "textDS", "id": "name", "textDS": {"text": "Demo laptop"}},
                        ],
                    }
                ]
            },
        }
        calls = []

        async def composer(path, ctx):
            calls.append(path)
            if mode == "partial" and len(calls) > 1:
                return 503, "unavailable", "synthetic"
            return 200, json.dumps({"widgetStates": {k: json.dumps(v) for k, v in widgets.items()}}), "synthetic"

        monkeypatch.setattr(module, "_fetch_composer", composer)
    elif prefix == "yandex":
        filename = "card_washer.html" if tool_name.endswith("card") else "search_kettle.html"
        if mode == "empty":
            filename = "search_empty.html"
        page = _fixture("yandex", filename)
        if mode in ("plus", "regular"):
            price = {"mainPrice": {"price": {"value": 900, "currency": "RUR"}}, "oldPrices": []}
            if mode == "regular":
                price["oldPrices"] = [{"type": "regular", "price": {"value": 1500, "currency": "RUR"}}]
            collections = {
                "price": {"p": price},
                "titleV2": {"t": {"title": "Demo laptop"}},
                "allPrices": {"a": {"baobab": {"productId": "123456", "skuId": "sku1"}}},
            }
            page = (
                '<html><noframes data-apiary="patch">' + json.dumps({"collections": collections}) + "</noframes></html>"
            )
        _stub(monkeypatch, module, "_fetch_html", page)
    elif prefix == "detmir":
        product = {"id": 1234, "title": "Demo toy", "price": {"price": 1234, "currency": "RUB"}}
        payload = (
            {"item": product}
            if tool_name.endswith("card")
            else {
                "items": [product],
                "meta": {"length": 1},
            }
        )
        if tool_name.endswith("categories"):
            payload = {"data": [{"id": 1, "alias": "toys", "title": "Toys"}], "meta": {"total": 1}}
        _stub(monkeypatch, module, "_fetch_json", payload)
    elif prefix == "avito":
        product = {
            "@type": "Product",
            "sku": "4612345678",
            "name": "Demo laptop",
            "offers": {"price": "1234", "priceCurrency": "RUB"},
        }
        html = '<script type="application/ld+json">' + json.dumps(product) + "</script>"
        if tool_name.endswith("seller"):
            html = _fixture("avito", "seller_profile_brands_27696b66.html")
        _stub(monkeypatch, module, "_fetch_item_page", (200, html, "cdp"))
        _stub(
            monkeypatch,
            module,
            "_fetch",
            (
                200,
                json.dumps(
                    {
                        "totalCount": 1,
                        "items": [
                            {"id": 4612345678, "title": "Demo laptop", "price": 1234, "seller": {"name": "Shop"}},
                        ],
                    }
                ),
                "synthetic",
            ),
        )
    elif prefix == "cian":
        search = json.loads(_fixture("cian", "search_sale_live.json"))
        card = json.loads(_fixture("cian", "card_sale_live.json"))
        _stub(monkeypatch, module, "_fetch_search", (200, json.dumps(search), "cdp"))
        _stub(
            monkeypatch,
            module,
            "_fetch_card",
            (
                200,
                json.dumps(
                    {
                        "kind": "ok",
                        "title": "Sale",
                        "url": "https://www.cian.ru/sale/flat/331002424/",
                        "offerData": card["offerData"],
                    }
                ),
                "cdp",
            ),
        )
    elif prefix == "mpstats":
        payload = {
            "code": 200,
            "days": 2,
            "items": {
                "1234": {
                    "Sku": 1234,
                    "Count": 2,
                    "Totals": {"orders": 1, "sum": 1234},
                    "pricesGraph": [0, 1234],
                    "countGraph": [0, 2],
                    "ordersGraph": [0, 1],
                    "rubricsGraph": [0, 1],
                }
            },
        }
        if tool_name.endswith("warehouses"):
            payload = {"code": 200, "days": 2, "data": {"1234": {"stocks": {"fbs": 2, "fbo": []}}}}
        _stub(monkeypatch, module, "_call", payload)
    elif prefix == "megamarket":

        async def same_url(url, ctx=None):
            return url

        monkeypatch.setattr(module, "_final_catalog_url", same_url)
        monkeypatch.setattr(module, "_USE_PROFILE_ADDRESS", False)
        item = {"id": 1234567, "title": "Demo laptop", "price": 1234, "isAvailable": True}
        _stub(
            monkeypatch,
            module,
            "_post",
            {"goods": item} if tool_name.endswith("card") else {"items": [item], "total": 1},
        )
    elif prefix == "lamoda":
        _stub(
            monkeypatch,
            module,
            "_graphql_card",
            {
                "sku": "MP002XM1RMM3",
                "name": "Demo shoes",
                "price_amount": 1234,
                "sizes": [{"size": "40", "is_available": True}],
            },
        )
        _stub(
            monkeypatch,
            module,
            "_cdp_render_search",
            {
                "title": "Lamoda",
                "items": [
                    {"sku": "MP002XM1RMM3", "title": "Demo shoes", "price_rub": 1234},
                ],
            },
        )
    elif prefix in ("dns", "citilink"):
        payload = {"title": "Demo laptop", "page_title": "Product", "price_rub": 1234, "is_available": True}
        if tool_name.endswith("search"):
            payload = {
                "title": "Catalog",
                "items": [
                    {
                        "product_id": "1234567",
                        "title": "Demo laptop",
                        "price_meta": "1234",
                        "price_text": "1 234 ₽",
                        "price_texts": {"attached": ["1 234 ₽"], "other": []},
                    }
                ],
            }
        _stub(monkeypatch, module, "_cdp_render", payload)
    elif prefix == "taobao":
        item = {
            "item_id": "123456789012",
            "title": "Demo laptop",
            "price_cny": 1234,
            "url": "https://item.taobao.com/item.htm?id=123456789012",
        }
        payload = {"title": "Product", "page_title": "Product", "price_cny": 1234, "description_images": 2}
        if tool_name.endswith("search"):
            payload = {"title": "Catalog", "grid_loading": False, "items": [item], "anchors_total": 100}
        _stub(monkeypatch, module, "_cdp_render", payload)
    elif prefix == "aliexpress":
        item = {
            "item_id": "1005010003103368",
            "title": "Demo laptop",
            "price_texts": {"attached": ["1 234 ₽", "1 500 ₽"], "other": []},
        }
        payload = {"punish": False, **item, "rating_text": "4,5"}
        if tool_name.endswith("search"):
            payload = {"punish": False, "page_title": "Catalog", "items": [item]}
        if mode == "challenge":
            payload = {"punish": True, "page_title": "Check", "items": []}
        method = "_cdp_card" if tool_name.endswith("card") else "_cdp_render_search"
        _stub(monkeypatch, module, method, payload)
    elif prefix == "marketplace":
        monkeypatch.setattr(
            chrome_cdp,
            "_cdp_http_json",
            lambda path, **kw: {"Browser": "Synthetic Chrome"} if path.endswith("version") else [],
        )
    return module


# END_BLOCK_SYNTHETIC_UPSTREAM


# START_BLOCK_WIRE_FIELD_CONTRACT


def _schema(text, answer):
    match = re.search(r"## Serialized output fields.*?```json\n(.*?)\n```", text, re.S)
    if match is None:
        # On old guides report the real R09 mismatch before the missing generated
        # inventory: aliased/native top-level keys, not an import or API failure.
        section = text.partition("## Error Format")[0]
        inventory = re.search(r"\{([^{}]*)\}", section)
        if inventory:
            fields = {field.strip().removesuffix("[]") for field in inventory[1].split(",")}
            assert fields == set(answer), (
                f"guide/wire top-level mismatch: documented={sorted(fields)}, actual={sorted(answer)}"
            )
        assert match is not None, "guide omits the generated serialized field contract"
    return json.loads(match[1])


def _check_fields(value, schema, root=None, path="$"):
    root = schema if root is None else root
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    if "anyOf" in schema:
        for candidate in schema["anyOf"]:
            try:
                _check_fields(value, candidate, root, path)
                return
            except AssertionError:
                pass
        pytest.fail(f"{path}: no documented response variant agrees with {value!r}")
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    actual_type = (
        "null"
        if value is None
        else (
            "boolean"
            if isinstance(value, bool)
            else "integer"
            if isinstance(value, int)
            else "number"
            if isinstance(value, float)
            else "object"
            if isinstance(value, dict)
            else "array"
            if isinstance(value, list)
            else "string"
        )
    )
    assert not types or actual_type in types or (actual_type == "integer" and "number" in types), (
        path,
        types,
        actual_type,
    )
    if isinstance(value, dict) and "properties" in schema:
        properties = schema["properties"]
        assert set(value) <= set(properties), f"{path}: undocumented fields {set(value) - set(properties)}"
        required = set(schema.get("required", properties))
        # Pydantic serialization includes its default/null fields as well.
        if "additionalProperties" not in schema:
            required = set(properties)
        assert required <= set(value), f"{path}: documented fields absent {required - set(value)}"
        for key, child in value.items():
            _check_fields(child, properties[key], root, f"{path}.{key}")
    elif isinstance(value, list) and "items" in schema:
        for item in value:
            _check_fields(item, schema["items"], root, f"{path}[]")


async def _round_trip(module, tool_name, arguments):
    async with Client(module.mcp) as client:
        text = (await client.read_resource(guide_uri(tool_name)))[0].text
        result = await client.call_tool(tool_name, arguments)
    assert result.structured_content is not None, tool_name
    answer = result.structured_content
    _check_fields(answer, _schema(text, answer))
    return text, answer


@pytest.mark.parametrize("tool_name", sorted(CALLS))
async def test_each_guide_matches_public_tool_answer(monkeypatch, tool_name):
    """R-GUIDE-CONTRACTS: no undocumented or phantom serialized fields."""
    assert set(CALLS) == set(GUIDES), "every guide needs a public synthetic scenario"
    module = _prepare(monkeypatch, tool_name)
    text, answer = await _round_trip(module, tool_name, CALLS[tool_name])
    assert text.startswith(f"# {tool_name}\n"), tool_name
    if tool_name in ("compare_verify_offer", "decision_inspect"):
        assert "meta" in answer["card"] and "_meta" not in answer["card"]
        assert "card.meta" in text
        native = _module("ozon_card")
        _, standalone = await _round_trip(native, "ozon_card", CALLS["ozon_card"])
        assert "_meta" in standalone and "meta" not in standalone
        assert set(standalone) - {"_meta"} == set(answer["card"]) - {"meta"}
    if tool_name == "compare_prices":
        assert answer["cheapest"]["price_rub"] == 1234
        assert answer["cheapest_comparable"]["price_rub"] == 1234
    if tool_name == "wb_search":
        assert set(answer) == {"result"} and answer["result"]["count"] == 1
    if tool_name == "compare_browser_snapshot":
        assert "structured_content" not in answer and "is_error" not in answer and "meta" not in answer


# END_BLOCK_WIRE_FIELD_CONTRACT


# START_BLOCK_SEMANTIC_CONTRACT
@pytest.mark.parametrize("tool_name", ["aliexpress_card", "aliexpress_search"])
async def test_aliexpress_challenge_guide_matches_wire_error(monkeypatch, tool_name):
    """R-GUIDE-CONTRACTS: x5sec is challenge_required, not transport_down."""
    module = _prepare(monkeypatch, tool_name, "challenge")
    async with Client(module.mcp) as client:
        guide = (await client.read_resource(guide_uri(tool_name)))[0].text
        result = await client.call_tool(tool_name, CALLS[tool_name], raise_on_error=False)
        assert result.is_error
        error = json.loads(result.content[0].text)
        assert error["error"] == "challenge_required"
        assert "ChallengeRequiredError" in guide and f"`{error['error']}`" in guide
        assert "TransportDownError on x5sec" not in guide
        assert error["requires_user_action"] is True and error["retryable_now"] is False
        _prepare(monkeypatch, tool_name)
        recovered = await client.call_tool(tool_name, CALLS[tool_name])
        assert not recovered.is_error
        assert (
            recovered.structured_content["price_rub"] == 1234
            if tool_name.endswith("card")
            else recovered.structured_content["count"] == 1
        )


@pytest.mark.parametrize(
    "tool_name,mode",
    [
        ("wb_search", "wallet"),
        ("wb_card", "unknown"),
        ("ozon_reviews", "partial"),
        ("yandex_search", "empty"),
        ("yandex_card", "plus"),
        ("yandex_card", "regular"),
    ],
)
async def test_price_and_partial_notes_match_wire(monkeypatch, tool_name, mode):
    """R-GUIDE-CONTRACTS: conditional, absent, partial and empty are different."""
    module = _prepare(monkeypatch, tool_name, mode)
    guide, answer = await _round_trip(module, tool_name, CALLS[tool_name])
    if mode == "wallet":
        item = answer["result"]["items"][0]
        assert item["price_kind"] == "wallet" and item["price_rub"] == 1000
        assert item["transport"] == "dom" and "Wallet-only" in guide
        compare = _module("compare_prices")
        _, compared = await _round_trip(compare, "compare_prices", CALLS["compare_prices"])
        assert compared["offers"][0]["price_native"] == 1000
        assert compared["offers"][0]["price_rub"] is None and compared["cheapest"] is None
    elif mode == "unknown":
        item = answer["items"][0]
        assert item["price_rub"] is None and item["total_quantity"] is None and item["in_stock"] is False
        assert "unknown stock" in guide and "total_quantity=null" in guide
    elif mode == "partial":
        assert answer["partial"] is True and answer["returned"] == 1 and answer["last_error"] is not None
        assert answer["_meta"]["healthy"] is True and "PARTIAL SUCCESS" in guide
        assert "it can stay true" in guide and "not pagination completeness" in guide
        assert "pages_fetched" in guide and "requested_item_id" in guide
    elif mode in ("plus", "regular"):
        assert answer["price_with_plus"] == 900 and "subscriber (Plus) price" in guide
        assert answer["price_rub"] == (1500 if mode == "regular" else None)
        _, verified = await _round_trip(
            _module("compare_verify_offer"),
            "compare_verify_offer",
            {
                "source": "yandex_market",
                "product_id_or_url": "123456",
                "expected_price_rub": 1500,
                "expected_variant_id": "sku1",
            },
        )
        verification = verified["price_verification"]
        if mode == "plus":
            assert verification["price_kind"] == "subscription"
            assert verification["observed_price_rub"] is None and verification["matches"] is None
        else:
            assert verification["observed_price_rub"] == 1500 and verification["delta_rub"] == 0
            assert verification["matches"] is True
    else:
        assert answer["returned"] == 0 and answer["meta"]["healthy"] is True
        assert any(w.startswith("info:no_results") for w in answer["meta"]["warnings"])
        assert "info:no_results" in guide and "stays healthy" in guide


# END_BLOCK_SEMANTIC_CONTRACT
