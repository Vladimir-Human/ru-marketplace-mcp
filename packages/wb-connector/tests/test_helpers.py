import asyncio
import json
import tomllib
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastmcp.exceptions import ToolError
from mcp_core.cache import TTLCache
from wb_connector import server


def _tool_error_payload(excinfo) -> dict:
    """raise_tool_error serializes a ConnectorError as JSON inside ToolError."""
    return json.loads(str(excinfo.value))


def test_server_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == server.SERVER_VERSION


def test_basket_for_sku_uses_expected_boundaries():
    assert server._basket_for_sku(1) == "basket-01.wbbasket.ru"
    assert server._basket_for_sku(14_300_000) == "basket-01.wbbasket.ru"
    assert server._basket_for_sku(14_400_000) == "basket-02.wbbasket.ru"
    assert server._basket_for_sku(500_000_000) == "basket-28.wbbasket.ru"


def test_recover_search_ids_accepts_live_shapes():
    assert server._recover_search_ids(
        [
            123,
            " 456 ",
            {"id": "789"},
            {"nmId": 101112},
            {"nm_id": "131415"},
        ]
    ) == [123, 456, 789, 101112, 131415]


def test_recover_search_ids_skips_invalid_and_boolean_values():
    assert (
        server._recover_search_ids(
            [
                True,
                False,
                0,
                -1,
                "0",
                "-2",
                "+0",
                "",
                "abc",
                {"id": False},
                {"id": 0},
                {"id": "-3"},
                {"nmId": "+0"},
                {"id": "42x"},
                {"other": 7},
                None,
            ]
        )
        == []
    )


def test_recover_search_ids_rejects_non_lists():
    assert server._recover_search_ids(None) == []
    assert server._recover_search_ids({"id": 1}) == []


def test_wb_card_rejects_non_positive_nm_ids_before_network(monkeypatch):
    async def forbidden_wait():
        raise AssertionError("invalid nm_ids must not reach network path")

    async def scenario():
        monkeypatch.setattr(server, "_polite_wait", forbidden_wait)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_card([-1, 0, 123])
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "bad_request"
        assert "positive integers" in payload["message"]

    asyncio.run(scenario())


def test_safe_get_text_has_wall_clock_timeout(monkeypatch):
    class SlowResponse:
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self, chunk_size):
            await asyncio.sleep(10)
            yield b"{}"

    class FakeClient:
        def stream(self, method, url):
            return SlowResponse()

    async def scenario():
        monkeypatch.setattr(server, "WB_WALL_TIMEOUT", 0.01)
        monkeypatch.setattr(server, "_NET_RETRIES", 0)
        status, text, err = await server._safe_get_text(FakeClient(), "https://example.test")
        assert status == 0
        assert text is None
        assert "timeout" in err.lower()

    asyncio.run(scenario())


def test_safe_get_text_does_not_retry_after_wall_timeout(monkeypatch):
    calls = {"stream": 0}

    class SlowResponse:
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self, chunk_size):
            await asyncio.sleep(10)
            yield b"{}"

    class FakeClient:
        def stream(self, method, url):
            calls["stream"] += 1
            return SlowResponse()

    async def scenario():
        monkeypatch.setattr(server, "WB_WALL_TIMEOUT", 0.01)
        monkeypatch.setattr(server, "_NET_RETRIES", 2)
        status, text, err = await server._safe_get_text(FakeClient(), "https://example.test")
        assert status == 0
        assert text is None
        assert "timeout" in err.lower()
        assert calls["stream"] == 1

    asyncio.run(scenario())


def test_safe_get_text_global_deadline_bounds_transport_retries(monkeypatch):
    calls = {"stream": 0, "sleep": []}

    class FakeClient:
        def stream(self, method, url):
            calls["stream"] += 1
            raise server.httpx.ConnectError("boom")

    async def fake_sleep(delay):
        calls["sleep"].append(delay)

    async def scenario():
        monkeypatch.setattr(server, "WB_WALL_TIMEOUT", 0.01)
        monkeypatch.setattr(server, "_NET_RETRIES", 2)
        monkeypatch.setattr(server, "_NET_BACKOFF_S", 0.8)
        monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
        status, text, err = await server._safe_get_text(FakeClient(), "https://example.test")
        assert status == 0
        assert text is None
        assert "timeout" in err.lower()
        assert calls["stream"] == 1
        assert calls["sleep"] == []

    asyncio.run(scenario())


def test_safe_get_text_retry_passes_through_polite_gate(monkeypatch):
    calls = {"stream": 0, "sleep": [], "polite": 0}

    class FakeClient:
        def stream(self, method, url):
            calls["stream"] += 1
            raise server.httpx.ConnectError("boom")

    async def fake_sleep(delay):
        calls["sleep"].append(delay)

    async def fake_polite_wait():
        calls["polite"] += 1

    async def scenario():
        monkeypatch.setattr(server, "WB_WALL_TIMEOUT", 10)
        monkeypatch.setattr(server, "_NET_RETRIES", 1)
        monkeypatch.setattr(server, "_NET_BACKOFF_S", 0.01)
        monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(server, "_polite_wait", fake_polite_wait)
        status, text, err = await server._safe_get_text(FakeClient(), "https://example.test")
        assert status == 0
        assert text is None
        assert "network" in err.lower()
        assert calls["stream"] == 2
        assert calls["sleep"] == [0.01]
        assert calls["polite"] == 1

    asyncio.run(scenario())


def test_safe_get_text_polite_retry_respects_global_deadline(monkeypatch):
    calls = {"stream": 0}

    class FakeClient:
        def stream(self, method, url):
            calls["stream"] += 1
            raise server.httpx.ConnectError("boom")

    async def fake_polite_wait():
        await asyncio.Future()

    async def scenario():
        monkeypatch.setattr(server, "WB_WALL_TIMEOUT", 0.01)
        monkeypatch.setattr(server, "_NET_RETRIES", 1)
        monkeypatch.setattr(server, "_NET_BACKOFF_S", 0)
        monkeypatch.setattr(server, "_polite_wait", fake_polite_wait)
        status, text, err = await server._safe_get_text(FakeClient(), "https://example.test")
        assert status == 0
        assert text is None
        assert "timeout" in err.lower()
        assert calls["stream"] == 1

    asyncio.run(scenario())


def test_safe_get_text_classifies_httpx_timeout_as_timeout(monkeypatch):
    class FakeClient:
        def stream(self, method, url):
            raise server.httpx.ReadTimeout("slow")

    async def scenario():
        monkeypatch.setattr(server, "WB_WALL_TIMEOUT", 10)
        monkeypatch.setattr(server, "_NET_RETRIES", 0)
        status, text, err = await server._safe_get_text(FakeClient(), "https://example.test")
        assert status == 0
        assert text is None
        assert err.startswith("timeout:")

    asyncio.run(scenario())


def test_safe_get_text_does_not_retry_http_status_errors(monkeypatch):
    calls = {"stream": 0, "sleep": 0, "polite": 0}

    class FakeClient:
        def stream(self, method, url):
            calls["stream"] += 1
            request = server.httpx.Request("GET", url)
            response = server.httpx.Response(429, request=request)
            raise server.httpx.HTTPStatusError("too many", request=request, response=response)

    async def fake_sleep(delay):
        calls["sleep"] += 1

    async def fake_polite_wait():
        calls["polite"] += 1

    async def scenario():
        monkeypatch.setattr(server, "_NET_RETRIES", 2)
        monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(server, "_polite_wait", fake_polite_wait)
        status, text, err = await server._safe_get_text(FakeClient(), "https://example.test")
        assert status == 429
        assert text is None
        assert err.startswith("http_status:")
        assert calls == {"stream": 1, "sleep": 0, "polite": 0}

    asyncio.run(scenario())


def test_wb_root_info_rejects_unusable_imt_id(monkeypatch):
    async def fake_safe_get_text(client, url):
        return 200, json.dumps({"imt_id": {"bad": "shape"}}), None

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_root_info(123)
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "parser_drift"
        assert "imt_id unusable" in payload["message"]

    asyncio.run(scenario())


def test_wb_root_info_coerces_string_imt_id(monkeypatch):
    async def fake_safe_get_text(client, url):
        return 200, json.dumps({"data": {"imtId": "1002173489"}}), None

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_root_info(123)
        data = result.model_dump()
        assert data["imt_id"] == 1002173489
        assert data["meta"]["source"] == "wb_root_info"
        assert data["meta"]["healthy"] is True

    asyncio.run(scenario())


def test_wb_card_rejects_non_object_json(monkeypatch):
    async def fake_safe_get_text(client, url):
        return 200, "[]", None

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_card([123])
        assert _tool_error_payload(excinfo)["error"] == "parser_drift"

    asyncio.run(scenario())


def test_kopeck_to_rub_rejects_non_ascii_digit():
    assert server._kopeck_to_rub("12²00") is None
    assert server._kopeck_to_rub("-12300") is None
    assert server._kopeck_to_rub("12abc00") is None
    assert server._kopeck_to_rub("12.00") is None
    assert server._kopeck_to_rub("12300") == 123.0


def test_wb_card_rejects_missing_products_container(monkeypatch):
    async def fake_safe_get_text(client, url):
        return 200, json.dumps({"data": {}}), None

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_card([123])
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "parser_drift"
        assert "products" in payload["message"]

    asyncio.run(scenario())


def test_wb_card_string_zero_quantity_is_not_in_stock(monkeypatch):
    async def fake_safe_get_text(client, url):
        return (
            200,
            json.dumps(
                {
                    "products": [
                        {
                            "id": 123,
                            "name": "x",
                            "brand": "b",
                            "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                            "totalQuantity": "0",
                        }
                    ]
                }
            ),
            None,
        )

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_card([123])
        data = result.model_dump()
        assert data["count"] == 1
        assert data["items"][0]["in_stock"] is False
        assert data["meta"]["source"] == "wb_card"

    asyncio.run(scenario())


def test_wb_reviews_rejects_non_list_feedbacks(monkeypatch):
    async def fake_safe_get_text(client, url):
        return 200, json.dumps({"feedbacks": {"bad": "shape"}}), None

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_reviews(server._SELFCHECK_IMT)
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "parser_drift"
        assert "feedbacks expected list" in payload["message"]

    asyncio.run(scenario())


def test_wb_reviews_reports_all_review_host_failures(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "feedbacks2.wb.ru" in url:
            return 0, None, "timeout: feedbacks2"
        if "feedbacks1.wb.ru" in url:
            return 0, None, "timeout: feedbacks1"
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)

        with pytest.raises(ToolError) as excinfo:
            await server.wb_reviews(server._SELFCHECK_IMT)

        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "transport_down"
        assert payload["retryable"] is True
        assert "feedbacks2.wb.ru: timeout: feedbacks2" in payload["message"]
        assert "feedbacks1.wb.ru: timeout: feedbacks1" in payload["message"]

    asyncio.run(scenario())


def test_wb_search_reads_products_straight_from_v9(monkeypatch):
    """v9 returns fully-populated products, so one request is enough.

    The old two-step path (search-goods ids -> card/v4) served stale ids: for a
    query whose v9 results were all in stock, every id it returned was a delisted
    SKU with no price. v9 is now primary.
    """
    calls = []

    async def fake_safe_get_text(client, url):
        calls.append(url)
        return (
            200,
            json.dumps(
                {
                    "total": 42,
                    "products": [
                        {
                            "id": 123,
                            "name": "x",
                            "brand": "b",
                            "supplier": "s",
                            "totalQuantity": 57,
                            "sizes": [{"price": {"basic": 200000, "product": 150000}}],
                        }
                    ],
                }
            ),
            None,
        )

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_search("x")
        data = result.model_dump()
        assert data["count"] == 1
        assert data["items"][0]["nm_id"] == 123
        assert data["items"][0]["price_rub"] == 1500.0
        assert data["items"][0]["in_stock"] is True
        assert data["total_ids"] == 42
        # A single upstream call: no card/v4 enrichment round-trip.
        assert len(calls) == 1
        assert "v9/search" in calls[0]

    asyncio.run(scenario())


def test_wb_search_falls_back_to_legacy_path_when_v9_fails(monkeypatch):
    """A stale result beats no result — but the caller must be told."""
    calls = []

    async def fake_safe_get_text(client, url):
        calls.append(url)
        if "v9/search" in url:
            return 503, "", None
        if "search-goods" in url:
            return 200, json.dumps([{"nmId": "123"}]), None
        return (
            200,
            json.dumps({"products": [{"id": 123, "name": "x", "brand": "b", "sizes": [], "totalQuantity": 0}]}),
            None,
        )

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_search("x")
        data = result.model_dump()
        assert data["count"] == 1
        assert data["items"][0]["nm_id"] == 123
        assert any("fallback" in w for w in data["meta"]["warnings"])
        assert data["meta"]["healthy"] is False
        assert any("search-goods" in c for c in calls)

    asyncio.run(scenario())


def test_wb_search_warns_when_no_result_has_a_price(monkeypatch):
    """A page of delisted items is worse than an error if it looks like an answer."""

    async def fake_safe_get_text(client, url):
        return (
            200,
            json.dumps(
                {
                    "total": 5,
                    "products": [
                        {"id": 1, "name": "dead", "brand": "b", "sizes": [{"price": None}], "totalQuantity": 0}
                    ],
                }
            ),
            None,
        )

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_search("x")
        data = result.model_dump()
        assert data["items"][0]["price_rub"] is None
        assert any("no_prices" in w for w in data["meta"]["warnings"])

    asyncio.run(scenario())


def test_wb_search_rate_limit_is_surfaced_not_masked_by_fallback(monkeypatch):
    """A 429 from v9 must raise, not silently degrade to the stale-id path."""

    async def fake_safe_get_text(client, url):
        if "v9/search" in url:
            return 429, "", None
        raise AssertionError("a 429 must not trigger the fallback")

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_search("x")
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "rate_limited"
        assert payload["retryable"] is True

    asyncio.run(scenario())


def test_wb_search_returns_no_results_when_both_paths_are_empty(monkeypatch):
    """Unrecoverable ids used to be parser_drift; with v9 primary they mean 'nothing'.

    v9 answering with an empty product list and the fallback yielding no usable
    ids is a legitimate no-results answer, not a broken parser.
    """

    async def fake_safe_get_text(client, url):
        if "v9/search" in url:
            return 200, json.dumps({"products": [], "total": 0}), None
        return 200, json.dumps([True, {"id": "²"}]), None

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_search("x")
        data = result.model_dump()
        assert data.get("count", 0) == 0 or data.get("items") in (None, [])

    asyncio.run(scenario())


def test_wb_search_empty_ids_returns_no_results_not_error(monkeypatch):
    async def fake_safe_get_text(client, url):
        return 200, json.dumps([]), None

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_search("zzz")
        assert isinstance(result, server.WbNoResultsResponse)
        data = result.model_dump()
        assert data["status"] == "no_results"
        assert data["query"] == "zzz"

    asyncio.run(scenario())


def test_wb_selfcheck_card_missing_products_container_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return 200, json.dumps({"data": {}}), None
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["card"]["state"] == "drift"
        assert data["checks"]["card"]["reason"] == "schema_drift"

    asyncio.run(scenario())


def test_wb_selfcheck_reviews_uses_feedbacks_fallback_host(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000}}],
                                "reviewRating": 4.8,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 0, None, "timeout: feedbacks2"
        if "feedbacks1.wb.ru" in url:
            return 200, json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)

        result = await server.wb_selfcheck()
        data = result.model_dump()

        assert data["server_version"] == server.SERVER_VERSION
        assert isinstance(data["server_started_at"], str)
        assert data["server_started_at"].endswith("Z")
        datetime.fromisoformat(data["server_started_at"].replace("Z", "+00:00"))
        assert isinstance(data["process_id"], int)
        assert data["process_id"] > 0
        assert data["checks"]["reviews"]["state"] == "healthy"
        assert data["checks"]["reviews"]["with_text"] == 1

    asyncio.run(scenario())


def test_wb_search_handles_v9_shape_drift_by_falling_back(monkeypatch):
    """If v9's response shape moves, the legacy path still answers."""
    calls = []

    async def fake_safe_get_text(client, url):
        calls.append(url)
        if "v9/search" in url:
            return 200, json.dumps({"unexpected": "shape"}), None
        if "search-goods" in url:
            return 200, json.dumps([123]), None
        return (
            200,
            json.dumps({"products": [{"id": 123, "name": "y", "brand": "b", "sizes": [], "totalQuantity": 0}]}),
            None,
        )

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_search("x")
        data = result.model_dump()
        assert data["count"] == 1
        assert any("fallback" in w for w in data["meta"]["warnings"])

    asyncio.run(scenario())


def test_wb_search_string_zero_quantity_is_not_in_stock(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "search-goods" in url:
            return 200, json.dumps([123]), None
        return (
            200,
            json.dumps(
                {
                    "products": [
                        {
                            "id": 123,
                            "name": "x",
                            "brand": "b",
                            "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                            "totalQuantity": "0",
                        }
                    ]
                }
            ),
            None,
        )

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_search("x")
        data = result.model_dump()
        assert data["items"][0]["in_stock"] is False

    asyncio.run(scenario())


def test_wb_selfcheck_card_missing_total_quantity_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["card"]["state"] == "drift"
        assert "totalQuantity" in data["checks"]["card"]["missing_fields"]

    asyncio.run(scenario())


def test_wb_selfcheck_reviews_missing_feedbacks_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["reviews"]["state"] == "drift"
        assert data["checks"]["reviews"]["reason"] == "schema_drift"

    asyncio.run(scenario())


def test_wb_selfcheck_reviews_rating_can_appear_after_malformed_first_entry(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "feedbacks": [
                            {"text": "broken first"},
                            {"text": "ok", "productValuation": 5},
                        ]
                    }
                ),
                None,
            )
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["reviews"]["state"] == "healthy"
        assert data["checks"]["reviews"]["with_text"] == 2

    asyncio.run(scenario())


def test_wb_selfcheck_null_roots_are_drift(monkeypatch):
    async def no_wait():
        return None

    async def run_case(marker):
        async def fake_safe_get_text(client, url):
            if "card.wb.ru" in url:
                if marker == "card":
                    return 200, "null", None
                return (
                    200,
                    json.dumps(
                        {
                            "products": [
                                {
                                    "id": server._SELFCHECK_NM,
                                    "name": "x",
                                    "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                    "reviewRating": 4.5,
                                    "feedbacks": 1,
                                    "totalQuantity": 1,
                                }
                            ]
                        }
                    ),
                    None,
                )
            if "feedbacks2.wb.ru" in url:
                if marker == "reviews":
                    return 200, "null", None
                return 200, json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]}), None
            if "search-goods.wildberries.ru" in url:
                if marker == "search_goods":
                    return 200, "null", None
                return 200, json.dumps([server._SELFCHECK_NM]), None
            if "wbbasket.ru" in url:
                return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
            return 500, "", "unexpected url"

        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"][marker]["state"] == "drift"

    asyncio.run(run_case("card"))
    asyncio.run(run_case("reviews"))
    asyncio.run(run_case("search_goods"))


def test_wb_selfcheck_reviews_200_invalid_json_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 200, "{not-json", None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["reviews"]["state"] == "drift"
        assert data["checks"]["reviews"]["reason"] == "parse_error"

    asyncio.run(scenario())


def test_wb_selfcheck_search_goods_200_invalid_json_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]}), None
        if "search-goods.wildberries.ru" in url:
            return 200, "{not-json", None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["search_goods"]["state"] == "drift"
        assert data["checks"]["search_goods"]["reason"] == "parse_error"

    asyncio.run(scenario())


def test_wb_selfcheck_search_goods_nonpositive_ids_are_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([0, "-1", {"id": "+0"}]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["search_goods"]["state"] == "drift"

    asyncio.run(scenario())


def test_wb_selfcheck_card_product_non_object_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return 200, json.dumps({"products": [None]}), None
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({"feedbacks": [{"text": "ok", "productValuation": 5}]}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["card"]["state"] == "drift"
        assert data["checks"]["card"]["reason"] == "schema_drift"

    asyncio.run(scenario())


def test_wb_selfcheck_feedbacks_non_list_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({"feedbacks": {"bad": "shape"}}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["reviews"]["state"] == "drift"
        assert data["checks"]["reviews"]["reason"] == "schema_drift"

    asyncio.run(scenario())


def test_wb_selfcheck_rich_text_feedback_body_is_drift(monkeypatch):
    async def fake_safe_get_text(client, url):
        if "card.wb.ru" in url:
            return (
                200,
                json.dumps(
                    {
                        "products": [
                            {
                                "id": server._SELFCHECK_NM,
                                "name": "x",
                                "sizes": [{"price": {"product": 10000, "basic": 12000}}],
                                "reviewRating": 4.5,
                                "feedbacks": 1,
                                "totalQuantity": 1,
                            }
                        ]
                    }
                ),
                None,
            )
        if "feedbacks2.wb.ru" in url:
            return 200, json.dumps({"feedbacks": [{"text": {"rich": "object"}, "productValuation": 5}]}), None
        if "search-goods.wildberries.ru" in url:
            return 200, json.dumps([server._SELFCHECK_NM]), None
        if "wbbasket.ru" in url:
            return 200, json.dumps({"imt_id": server._SELFCHECK_IMT}), None
        return 500, "", "unexpected url"

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        result = await server.wb_selfcheck()
        data = result.model_dump()
        assert data["checks"]["reviews"]["state"] == "drift"
        assert data["checks"]["reviews"]["with_text"] == 0

    asyncio.run(scenario())


def _clear_wb_cache():
    server._cache.clear()


def test_cache_serves_a_repeated_successful_read(monkeypatch):
    """An agent walks the same SKU repeatedly; the second look must not re-hit WB."""
    calls = {"n": 0}

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self, chunk_size):
            yield b'{"ok":true}'

    class FakeClient:
        def stream(self, method, url):
            calls["n"] += 1
            return FakeResponse()

    async def no_wait():
        return None

    async def scenario():
        _clear_wb_cache()
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        url = "https://cache-hit.test/a"
        first = await server._safe_get_text(FakeClient(), url)
        second = await server._safe_get_text(FakeClient(), url)
        assert first == second == (200, '{"ok":true}', None)
        assert calls["n"] == 1, "second read must come from the cache"

    asyncio.run(scenario())
    _clear_wb_cache()


def test_cache_does_not_remember_a_transient_failure(monkeypatch):
    """Caching a blip would turn one bad moment into a TTL-long outage."""
    attempts = {"n": 0}

    class FailingResponse:
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            raise httpx.ConnectError("refused")

        async def __aexit__(self, *args):
            return None

    class OkResponse:
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self, chunk_size):
            yield b'{"recovered":true}'

    class FlakyClient:
        def stream(self, method, url):
            attempts["n"] += 1
            return FailingResponse() if attempts["n"] == 1 else OkResponse()

    async def no_wait():
        return None

    async def scenario():
        _clear_wb_cache()
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        monkeypatch.setattr(server, "_NET_RETRIES", 0)
        url = "https://transient.test/a"
        status, text, err = await server._safe_get_text(FlakyClient(), url)
        assert err is not None and text is None
        # A retry after the blip must reach the network again, not replay the error.
        status, text, err = await server._safe_get_text(FlakyClient(), url)
        assert (status, text, err) == (200, '{"recovered":true}', None)
        assert attempts["n"] == 2

    asyncio.run(scenario())
    _clear_wb_cache()


def test_cache_does_not_remember_a_rate_limit(monkeypatch):
    """A cached 429 would keep reporting rate-limited after the limit lifted."""
    calls = {"n": 0}

    class RateLimited:
        status_code = 429
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self, chunk_size):
            yield b"slow down"

    class FakeClient:
        def stream(self, method, url):
            calls["n"] += 1
            return RateLimited()

    async def no_wait():
        return None

    async def scenario():
        _clear_wb_cache()
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        url = "https://ratelimited.test/a"
        assert (await server._safe_get_text(FakeClient(), url))[0] == 429
        assert (await server._safe_get_text(FakeClient(), url))[0] == 429
        assert calls["n"] == 2, "a 429 must never be served from cache"

    asyncio.run(scenario())
    _clear_wb_cache()


def test_cache_can_be_disabled_by_ttl_zero(monkeypatch):
    """WB_CACHE_TTL=0 means every read goes upstream."""
    calls = {"n": 0}

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self, chunk_size):
            yield b"{}"

    class FakeClient:
        def stream(self, method, url):
            calls["n"] += 1
            return FakeResponse()

    async def no_wait():
        return None

    async def scenario():
        monkeypatch.setattr(server, "_polite_wait", no_wait)
        monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=0))
        url = "https://nocache.test/a"
        await server._safe_get_text(FakeClient(), url)
        await server._safe_get_text(FakeClient(), url)
        assert calls["n"] == 2

    asyncio.run(scenario())


def test_proxy_prefers_the_connector_specific_variable(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://generic:8080")
    monkeypatch.setattr(server._settings, "proxy", "http://explicit:9090")
    assert server._proxy() == "http://explicit:9090"


def test_proxy_falls_back_to_standard_variables(monkeypatch):
    monkeypatch.setattr(server._settings, "proxy", "")
    monkeypatch.delenv("WB_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://generic:8080")
    assert server._proxy() == "http://generic:8080"


def test_client_is_built_without_following_redirects():
    """WB answers some datacenter requests with a self-referential 307."""
    client = server._wb_client()
    assert client.follow_redirects is False


# ------------------------------------------------------------- wb_questions ----


def _questions_payload(count, items):
    return 200, json.dumps({"questions": items, "count": count, "err": None}), None


def _question(qid, text, answer_text=None, nm_id=5535526, name="покупатель"):
    raw = {
        "id": qid,
        "text": text,
        "createdDate": "2026-06-30T10:10:26.155795075Z",
        "nmId": nm_id,
        "wbUserDetails": {"name": name, "country": "ru"},
    }
    if answer_text is not None:
        raw["answer"] = {"text": answer_text, "createDate": "2026-07-01T09:00:00Z", "supplierId": 1}
    return raw


def _patch_questions(monkeypatch, responder):
    async def no_wait():
        return None

    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "_safe_get_text", responder)


def test_questions_returns_pairs_and_marks_answered(monkeypatch):
    async def responder(client, url):
        assert "imtId=1002173489" in url
        # take and skip are mandatory upstream; omitting either yields a silent empty.
        assert "take=30" in url and "skip=0" in url
        return _questions_payload(
            2,
            [
                _question("q1", "10 ампер или 16 ампер?", "Максимальный ток 10 А"),
                _question("q2", "есть ли гарантия?"),
            ],
        )

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_questions(imt_id=1002173489, limit=10)

        assert result.total_available == 2
        assert result.returned == 2
        assert result.answered_count == 1
        first, second = result.questions
        assert first.answered is True
        assert first.answer_text == "Максимальный ток 10 А"
        assert first.nm_id == 5535526
        assert second.answered is False
        assert second.answer_text == ""
        assert second.answer_date is None

    asyncio.run(scenario())


def test_questions_treats_null_questions_as_empty(monkeypatch):
    """A product nobody has asked about returns questions: null, not []."""

    async def responder(client, url):
        return 200, json.dumps({"questions": None, "count": 0, "err": None}), None

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_questions(imt_id=12027820)

        assert result.returned == 0
        assert result.total_available == 0
        assert result.has_more is False
        assert result.meta.healthy is True

    asyncio.run(scenario())


def test_questions_paginates_past_the_upstream_take_cap(monkeypatch):
    """take is capped at 30 upstream, so limit=45 must walk two pages."""
    seen_skips = []

    async def responder(client, url):
        skip = int(url.split("skip=")[1])
        seen_skips.append(skip)
        items = [_question(f"q{skip + i}", f"вопрос {skip + i}") for i in range(30)]
        return _questions_payload(70, items)

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_questions(imt_id=1, limit=45)

        assert seen_skips == [0, 30]
        assert result.returned == 45
        assert len({q.question_id for q in result.questions}) == 45
        assert result.has_more is True

    asyncio.run(scenario())


def test_questions_stops_at_a_short_page(monkeypatch):
    """A page smaller than the cap means the pool is exhausted."""
    calls = {"n": 0}

    async def responder(client, url):
        calls["n"] += 1
        return _questions_payload(5, [_question(f"q{i}", f"вопрос {i}") for i in range(5)])

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_questions(imt_id=1, limit=100)

        assert calls["n"] == 1, "a short page must not trigger another request"
        assert result.returned == 5
        assert result.has_more is False

    asyncio.run(scenario())


def test_questions_answered_only_keeps_filling_across_pages(monkeypatch):
    """Filtering after fetching must not silently shrink the caller's result."""

    async def responder(client, url):
        skip = int(url.split("skip=")[1])
        # Page 1 is mostly unanswered; the answers live on page 2.
        if skip == 0:
            items = [_question(f"a{i}", "без ответа") for i in range(29)]
            items.append(_question("answered-1", "с ответом", "да"))
        else:
            items = [_question(f"b{i}", "с ответом", "да") for i in range(30)]
        return _questions_payload(60, items)

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_questions(imt_id=1, limit=5, answered_only=True)

        assert result.returned == 5
        assert all(q.answered for q in result.questions)

    asyncio.run(scenario())


def test_questions_warns_when_nothing_is_answered_yet(monkeypatch):
    async def responder(client, url):
        return _questions_payload(3, [_question(f"q{i}", "без ответа") for i in range(3)])

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_questions(imt_id=1, limit=10, answered_only=True)

        assert result.returned == 0
        assert result.meta.healthy is False
        assert any("seller answer" in w for w in result.meta.warnings)

    asyncio.run(scenario())


def test_questions_collapses_newlines_in_answers(monkeypatch):
    """Seller answers contain literal newlines, which break single-line rendering."""

    async def responder(client, url):
        return _questions_payload(1, [_question("q1", "вопрос\nв две строки", "ответ\nв\tдве строки")])

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_questions(imt_id=1)

        question = result.questions[0]
        assert question.text == "вопрос в две строки"
        assert question.answer_text == "ответ в две строки"

    asyncio.run(scenario())


def test_questions_raises_drift_when_count_is_missing(monkeypatch):
    """A missing count means the contract changed; a zero count is legitimate."""

    async def responder(client, url):
        return 200, json.dumps({"questions": []}), None

    async def scenario():
        _patch_questions(monkeypatch, responder)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_questions(imt_id=1)
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "parser_drift"

    asyncio.run(scenario())


def test_questions_raises_drift_when_questions_is_not_a_list(monkeypatch):
    async def responder(client, url):
        return 200, json.dumps({"questions": {"unexpected": "dict"}, "count": 1}), None

    async def scenario():
        _patch_questions(monkeypatch, responder)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_questions(imt_id=1)
        assert _tool_error_payload(excinfo)["error"] == "parser_drift"

    asyncio.run(scenario())


def test_questions_surfaces_rate_limiting(monkeypatch):
    async def responder(client, url):
        return 429, "", None

    async def scenario():
        _patch_questions(monkeypatch, responder)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_questions(imt_id=1)
        assert _tool_error_payload(excinfo)["error"] == "rate_limited"

    asyncio.run(scenario())


def test_questions_rejects_an_out_of_range_limit(monkeypatch):
    async def forbidden(client, url):
        raise AssertionError("validation must happen before any network call")

    async def scenario():
        _patch_questions(monkeypatch, forbidden)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_questions(imt_id=1, limit=500)
        assert _tool_error_payload(excinfo)["error"] == "bad_request"

    asyncio.run(scenario())


def test_questions_uses_the_dedicated_host_not_a_feedbacks_mirror(monkeypatch):
    """feedbacks*.wb.ru answers any questions-ish path with a misleading empty stub."""
    seen = {}

    async def responder(client, url):
        seen["url"] = url
        return _questions_payload(0, None)

    async def scenario():
        _patch_questions(monkeypatch, responder)
        await server.wb_questions(imt_id=1)

        assert "questions.wildberries.ru" in seen["url"]
        assert "feedbacks" not in seen["url"]

    asyncio.run(scenario())


def test_questions_is_registered_as_a_tool():
    async def scenario():
        names = {tool.name for tool in await server.mcp.list_tools()}
        assert "wb_questions" in names
        # The v1.0.0 contract must remain intact alongside the addition.
        assert {
            "wb_search",
            "wb_card",
            "wb_root_info",
            "wb_reviews",
            "wb_seller",
            "wb_categories",
            "wb_selfcheck",
        } <= names

    asyncio.run(scenario())


# ------------------------------------------------- wb_category_products ----


def _catalog_payload(count=2, start=1000):
    products = [
        {
            "id": start + i,
            "name": f"товар {i}",
            "brand": "БРЕНД",
            "supplier": "продавец",
            "supplierId": 7,
            "reviewRating": 4.5,
            "feedbacks": 10,
            "totalQuantity": 5,
            "sizes": [{"price": {"product": 150000, "basic": 200000}}],
        }
        for i in range(count)
    ]
    return 200, json.dumps({"data": {"products": products}}), None


def test_category_products_lists_a_page(monkeypatch):
    seen = {}

    async def responder(client, url):
        seen["url"] = url
        return _catalog_payload(count=3)

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_category_products(shard="electronic58", query="cat=9845")

        assert "catalog.wb.ru/catalog/electronic58/v4/catalog" in seen["url"]
        assert "cat=9845" in seen["url"]
        assert "dest=" in seen["url"] and "appType=1" in seen["url"]
        assert result.count == 3
        assert result.shard == "electronic58"
        assert result.dest == server.WB_DEFAULT_DEST
        first = result.items[0]
        assert first.nm_id == 1000
        assert first.price_rub == 1500.0
        assert first.price_original_rub == 2000.0
        assert first.in_stock is True

    asyncio.run(scenario())


def test_category_products_refuses_the_blackhole_shard_without_a_request(monkeypatch):
    """An empty list would claim the category has no products, which is false."""

    async def forbidden(client, url):
        raise AssertionError("an unlistable category must not cost a request")

    async def scenario():
        _patch_questions(monkeypatch, forbidden)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_category_products(shard="blackhole", query="cat=9455")
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "bad_request"
        assert "not directly listable" in payload["message"]
        # The message has to point somewhere useful, not just refuse.
        assert "wb_search" in payload["message"] or "wb_categories" in payload["message"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad_query",
    [
        "cat=1&extra=2",  # smuggling a second parameter
        "../../etc/passwd",
        "cat=abc",  # non-numeric selector
        "cat=",  # empty value
        "",
    ],
)
def test_category_products_rejects_an_unsafe_selector(monkeypatch, bad_query):
    """The selector is appended to an outbound URL, so it is validated not trusted."""

    async def forbidden(client, url):
        raise AssertionError(f"{bad_query!r} must be rejected before any network call")

    async def scenario():
        _patch_questions(monkeypatch, forbidden)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_category_products(shard="electronic58", query=bad_query)
        assert _tool_error_payload(excinfo)["error"] == "bad_request"

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_shard", ["", "has spaces", "shard/../evil", "a"])
def test_category_products_rejects_an_unsafe_shard(monkeypatch, bad_shard):
    async def forbidden(client, url):
        raise AssertionError("must be rejected before any network call")

    async def scenario():
        _patch_questions(monkeypatch, forbidden)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_category_products(shard=bad_shard, query="cat=9845")
        assert _tool_error_payload(excinfo)["error"] == "bad_request"

    asyncio.run(scenario())


def test_category_products_rejects_an_unknown_sort(monkeypatch):
    async def forbidden(client, url):
        raise AssertionError("must be rejected before any network call")

    async def scenario():
        _patch_questions(monkeypatch, forbidden)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_category_products(shard="electronic58", query="cat=9845", sort="cheapest")
        assert _tool_error_payload(excinfo)["error"] == "bad_request"

    asyncio.run(scenario())


def test_category_products_maps_a_404_to_not_found(monkeypatch):
    """A real shard answering 404 means a stale selector, not a dead connector."""

    async def responder(client, url):
        return 404, "", None

    async def scenario():
        _patch_questions(monkeypatch, responder)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_category_products(shard="electronic58", query="cat=9845")
        payload = _tool_error_payload(excinfo)
        assert payload["error"] == "not_found"
        assert "wb_categories" in payload["message"]

    asyncio.run(scenario())


def test_category_products_reports_has_more_on_a_full_page(monkeypatch):
    async def responder(client, url):
        return _catalog_payload(count=server.WB_CATEGORY_PAGE_SIZE)

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_category_products(shard="electronic58", query="cat=9845")
        assert result.count == server.WB_CATEGORY_PAGE_SIZE
        assert result.has_more is True

    asyncio.run(scenario())


def test_category_products_reports_no_more_on_a_short_page(monkeypatch):
    async def responder(client, url):
        return _catalog_payload(count=7)

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_category_products(shard="electronic58", query="cat=9845")
        assert result.has_more is False

    asyncio.run(scenario())


def test_category_products_honours_an_explicit_region(monkeypatch):
    seen = {}

    async def responder(client, url):
        seen["url"] = url
        return _catalog_payload()

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_category_products(shard="electronic58", query="cat=9845", dest="-1123300")
        assert "dest=-1123300" in seen["url"]
        assert result.dest == "-1123300"

    asyncio.run(scenario())


def test_category_products_raises_drift_on_an_unexpected_payload(monkeypatch):
    async def responder(client, url):
        return 200, json.dumps({"data": {"products": "not a list"}}), None

    async def scenario():
        _patch_questions(monkeypatch, responder)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_category_products(shard="electronic58", query="cat=9845")
        assert _tool_error_payload(excinfo)["error"] == "parser_drift"

    asyncio.run(scenario())


def test_category_products_treats_cloudflare_html_as_transport_down(monkeypatch):
    async def responder(client, url):
        return 200, "<html><body>Attention Required</body></html>", None

    async def scenario():
        _patch_questions(monkeypatch, responder)
        with pytest.raises(ToolError) as excinfo:
            await server.wb_category_products(shard="electronic58", query="cat=9845")
        assert _tool_error_payload(excinfo)["error"] == "transport_down"

    asyncio.run(scenario())


def test_category_item_shape_matches_wb_card(monkeypatch):
    """A category walk and a text search must be directly comparable."""

    async def responder(client, url):
        return _catalog_payload(count=1)

    async def scenario():
        _patch_questions(monkeypatch, responder)
        result = await server.wb_category_products(shard="electronic58", query="cat=9845")
        assert set(result.items[0].model_dump()) == set(server.WbCardItem().model_dump())

    asyncio.run(scenario())


def test_card_item_dict_never_calls_an_unpriced_listing_in_stock():
    """A quantity with no price is unsellable; calling it available would rank it cheapest."""
    mapped = server._card_item_dict({"id": 1, "totalQuantity": 10, "sizes": [{"price": {}}]})
    assert mapped["price_rub"] is None
    assert mapped["in_stock"] is False


def test_category_products_is_registered_and_v1_tools_are_intact():
    async def scenario():
        names = {tool.name for tool in await server.mcp.list_tools()}
        assert "wb_category_products" in names
        assert {
            "wb_search",
            "wb_card",
            "wb_root_info",
            "wb_reviews",
            "wb_seller",
            "wb_categories",
            "wb_selfcheck",
        } <= names

    asyncio.run(scenario())
