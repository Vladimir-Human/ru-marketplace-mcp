import asyncio
import json
import tomllib
from datetime import datetime
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
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
