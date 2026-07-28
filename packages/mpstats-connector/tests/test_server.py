"""Offline tests for the MPStats connector.

Never touches the network: every test monkeypatches the fetch seam (``_call``)
or the auth seam (``_cookie_header``) and asserts the contract an agent sees —
which error code, which warning, which fields. Live behaviour is verified
separately by the live selfcheck; these tests pin the parser and the
error/warning contract so a future upstream shape change is caught here, not
silently in production.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from mpstats_connector import server
from mpstats_connector.models_output import MpStatsItemResponse, MpStatsWarehousesResponse


def _tool_error_payload(excinfo) -> dict:
    """raise_tool_error serializes a ConnectorError as JSON inside ToolError."""
    return json.loads(str(excinfo.value))


def _item_payload(sku: int = 5107857210) -> dict:
    """A trimmed-but-structurally-faithful MPStats item analytics response.

    Built from a live capture (Jul 2026), trimmed to the fields the parser
    reads so a structural change still surfaces. Graphs are short for
    readability — the parser handles any length.
    """
    return {
        "code": 200,
        "days": 30,
        "items": {
            str(sku): {
                "Sku": sku,
                "Count": 500,
                "DaysOnStocks": 10,
                "OrdersPerDay": 1.3,
                "Seller": "Acme Trading Co.",
                "SellerId": 4131180,
                "Brand": "",
                "Totals": {"orders": 13, "sum": 52806, "sumPrev": 40000},
                "rubricsGraph": [0, 0, 1],
                "pricesGraph": [0, 3957, 4062],
                "countGraph": [0, 500, 500],
                "Orders": [0, 1, 2],
                "ordersGraph": [0, 1, 2],
            }
        },
    }


def _warehouses_payload(sku: int = 5107857210) -> dict:
    """A faithful MPStats warehouses response, trimmed."""
    return {
        "code": 200,
        "days": 30,
        "data": {
            str(sku): {
                "stocks": {"fbs": 500, "fbo": []},
                "last_update": "2026-07-27 06:22:08",
            }
        },
    }


# --------------------------------------------------------------------------- #
# Tool registration / metadata
# --------------------------------------------------------------------------- #


def test_server_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == server.SERVER_VERSION


@pytest.mark.asyncio
async def test_three_tools_registered():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert names == {"mpstats_item", "mpstats_warehouses", "mpstats_selfcheck"}


# --------------------------------------------------------------------------- #
# Parser unit tests (no network, no auth)
# --------------------------------------------------------------------------- #


def test_parse_item_entry_extracts_fields():
    raw = _item_payload()["items"]["5107857210"]
    item = server._parse_item_entry(raw, "ozon")
    assert item is not None
    assert item.sku == 5107857210
    assert item.seller == "Acme Trading Co."
    assert item.seller_id == 4131180
    assert item.days_on_stocks == 10
    assert item.orders_per_day == 1.3
    assert item.totals.orders == 13
    assert item.totals.sum == 52806.0
    assert item.totals.sum_prev == 40000.0
    # last non-zero price-graph cell -> current price
    assert item.price_avg_rub == 4062.0
    # last non-zero count-graph cell -> current stock
    assert item.stock_now == 500
    assert item.prices_graph == [0, 3957, 4062]
    assert item.count_graph == [0, 500, 500]
    assert item.place == "ozon"


def test_parse_item_entry_all_zero_graphs_return_none_not_zero():
    """A delisted item (all-zero graphs) reports None price/stock, never 0.0."""
    raw = {
        "Sku": 1,
        "Totals": {"orders": 0, "sum": 0, "sumPrev": 0},
        "rubricsGraph": [0, 0],
        "pricesGraph": [0, 0],
        "countGraph": [0, 0],
        "Orders": [0, 0],
        "ordersGraph": [0, 0],
    }
    item = server._parse_item_entry(raw, "ozon")
    assert item is not None
    assert item.price_avg_rub is None
    # stock: all-zero graph is a real "zero stock now" reading, not "no data"
    assert item.stock_now == 0


def test_parse_item_entry_empty_graphs_return_none():
    raw = {"Sku": 1, "Totals": {}, "ordersGraph": [], "pricesGraph": [], "countGraph": [], "rubricsGraph": []}
    item = server._parse_item_entry(raw, "ozon")
    assert item is not None
    assert item.price_avg_rub is None
    assert item.stock_now is None
    assert item.totals.orders is None


def test_parse_item_entry_non_dict_returns_none():
    assert server._parse_item_entry("not a dict", "ozon") is None
    assert server._parse_item_entry(None, "ozon") is None


def test_parse_item_entry_rejects_ambiguous_totals():
    """coerce_int/coerce_price refuse ambiguous forms (sign, price range)."""
    raw = {
        "Sku": 1,
        "Totals": {"orders": "-3", "sum": "1 999 ₽ 2 999 ₽", "sumPrev": -50},
        "ordersGraph": [],
        "pricesGraph": [],
        "countGraph": [],
        "rubricsGraph": [],
    }
    item = server._parse_item_entry(raw, "ozon")
    assert item is not None
    assert item.totals.orders is None  # signed -> None, not fabricated 3
    assert item.totals.sum is None  # price range (two numbers) -> None
    assert item.totals.sum_prev is None  # negative -> None, never -50.0


def test_parse_warehouses_entry_collapses_fbo_list_to_total():
    raw = {"stocks": {"fbs": 12, "fbo": [{"warehouse_a": 30}, {"warehouse_b": 7}]}, "last_update": "2026-07-27"}
    row = server._parse_warehouses_entry(raw)
    assert row is not None
    assert row.stocks.fbs == 12
    assert row.stocks.fbo == 37
    assert row.stocks.fbo_warehouses == [{"warehouse_a": 30}, {"warehouse_b": 7}]
    assert row.stocks.last_update == "2026-07-27"


def test_parse_warehouses_entry_empty_fbo_list_yields_none_total():
    raw = {"stocks": {"fbs": 500, "fbo": []}, "last_update": "2026-07-27"}
    row = server._parse_warehouses_entry(raw)
    assert row is not None
    assert row.stocks.fbs == 500
    assert row.stocks.fbo is None
    assert row.stocks.fbo_warehouses == []


def test_parse_warehouses_entry_non_dict_returns_none():
    assert server._parse_warehouses_entry(None) is None
    assert server._parse_warehouses_entry([1, 2]) is None


def test_last_nonzero_skips_zeros_from_the_end():
    assert server._last_nonzero([0, 0, 5, 7, 0]) == 7.0
    assert server._last_nonzero([0, 0, 0]) is None
    assert server._last_nonzero([]) is None
    assert server._last_nonzero(None) is None  # type: ignore[arg-type]


def test_int_graph_coerces_and_zero_fills():
    assert server._int_graph([1, "2", 3]) == [1, 2, 3]
    # ambiguous cells drop to 0, preserving window length
    assert server._int_graph([1, "-3", "1.2K", None, 4]) == [1, 0, 0, 0, 4]
    assert server._int_graph(None) == []  # type: ignore[arg-type]
    assert server._int_graph("oops") == []  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_validate_skus_rejects_empty(monkeypatch):
    async def forbidden():
        raise AssertionError("must not reach network")

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_item([], place="ozon")
    payload = _tool_error_payload(excinfo)
    assert payload["error"] == "bad_request"


@pytest.mark.asyncio
async def test_validate_skus_rejects_too_many(monkeypatch):
    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_item(list(range(1, server.MAX_SKUS + 2)), place="ozon")
    assert _tool_error_payload(excinfo)["error"] == "bad_request"


@pytest.mark.asyncio
async def test_validate_skus_rejects_non_positive_and_bool(monkeypatch):
    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    for bad in ([0], [-1], [True], [1, "abc"]):  # type: ignore[list-item]
        with pytest.raises(ToolError) as excinfo:
            await server.mpstats_item(bad, place="ozon")  # type: ignore[arg-type]
        assert _tool_error_payload(excinfo)["error"] == "bad_request"


@pytest.mark.asyncio
async def test_invalid_place_rejected(monkeypatch):
    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_item([1], place="lamoda")
    assert _tool_error_payload(excinfo)["error"] == "bad_request"


# --------------------------------------------------------------------------- #
# Auth gate (the contract: no token -> auth_missing, never a network call)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_item_auth_missing_without_token(monkeypatch):
    """Without mp_auth the real ``_call`` raises auth_missing before any HTTP,
    so the network client is never built. Monkeypatch the client builder to
    prove the auth gate short-circuits upstream."""

    async def forbidden_client(*a, **k):
        raise AssertionError("must not build an HTTP client without mp_auth")

    monkeypatch.setattr(server, "_cookie_header", lambda: "")
    monkeypatch.setattr(server, "_client", forbidden_client)
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_item([1], place="ozon")
    payload = _tool_error_payload(excinfo)
    assert payload["error"] == "auth_missing"
    assert payload["retryable"] is False


@pytest.mark.asyncio
async def test_warehouses_auth_missing_without_token(monkeypatch):
    monkeypatch.setattr(server, "_cookie_header", lambda: "")
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_warehouses([1], place="ozon")
    assert _tool_error_payload(excinfo)["error"] == "auth_missing"


@pytest.mark.asyncio
async def test_inner_code_403_maps_to_auth_missing(monkeypatch):
    """MPStats returns code:403 behind HTTP 200 for a bad token — must surface
    as auth_missing, not as a generic transport error."""

    async def fake_call(body, *, label):
        from mcp_core.errors import AuthMissingError, raise_tool_error

        raise_tool_error(AuthMissingError("mp_auth token rejected (code 403)", provider="mpstats"))

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=stale")
    monkeypatch.setattr(server, "_call", fake_call)
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_item([1], place="ozon")
    assert _tool_error_payload(excinfo)["error"] == "auth_missing"


# --------------------------------------------------------------------------- #
# Happy path (monkeypatch the fetch seam, assert the agent-facing contract)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_item_happy_path(monkeypatch):
    payload = _item_payload(5107857210)

    async def fake_call(body, *, label):
        return payload

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    resp = await server.mpstats_item([5107857210], place="ozon")
    assert isinstance(resp, MpStatsItemResponse)
    assert resp.place == "ozon"
    assert resp.count == 1
    assert resp.items[0].sku == 5107857210
    assert resp.items[0].price_avg_rub == 4062.0
    assert resp.items[0].stock_now == 500
    assert resp.meta.healthy is True
    assert resp.meta.warnings == []


@pytest.mark.asyncio
async def test_item_batch_preserves_request_order_and_warns_missing(monkeypatch):
    """The upstream keys items by SKU id, not by request order; the connector
    walks the request list so ordering is deterministic, and a missing SKU
    becomes a warning rather than a silent gap."""
    payload = _item_payload(111)
    payload["items"]["333"] = _item_payload(333)["items"]["333"]

    async def fake_call(body, *, label):
        return payload

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    resp = await server.mpstats_item([333, 111, 999], place="ozon")
    assert [it.sku for it in resp.items] == [333, 111]
    assert any("999" in w for w in resp.meta.warnings)
    assert resp.meta.healthy is False


@pytest.mark.asyncio
async def test_item_no_results_raises_not_found(monkeypatch):
    async def fake_call(body, *, label):
        return {"code": 200, "days": 30, "items": {}}

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_item([1], place="ozon")
    assert _tool_error_payload(excinfo)["error"] == "not_found"


@pytest.mark.asyncio
async def test_item_shape_drift_raises_parser_drift(monkeypatch):
    async def fake_call(body, *, label):
        return {"code": 200, "days": 30, "items": []}  # items must be an object

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_item([1], place="ozon")
    assert _tool_error_payload(excinfo)["error"] == "parser_drift"


@pytest.mark.asyncio
async def test_warehouses_happy_path(monkeypatch):
    async def fake_call(body, *, label):
        return _warehouses_payload(5107857210)

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    resp = await server.mpstats_warehouses([5107857210], place="ozon")
    assert isinstance(resp, MpStatsWarehousesResponse)
    assert resp.count == 1
    assert resp.items[0].sku == 5107857210
    assert resp.items[0].stocks.fbs == 500
    assert resp.items[0].stocks.last_update == "2026-07-27 06:22:08"
    assert resp.meta.healthy is True


@pytest.mark.asyncio
async def test_warehouses_stamps_sku_from_request_when_upstream_omits_it(monkeypatch):
    """The warehouses endpoint does not echo the SKU in each entry, so the
    connector stamps it from the request — verified against the live capture."""
    payload = {"code": 200, "days": 30, "data": {"42": {"stocks": {"fbs": 9, "fbo": []}, "last_update": "t"}}}

    async def fake_call(body, *, label):
        return payload

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    resp = await server.mpstats_warehouses([42], place="ozon")
    assert resp.items[0].sku == 42


@pytest.mark.asyncio
async def test_warehouses_shape_drift_raises_parser_drift(monkeypatch):
    async def fake_call(body, *, label):
        return {"code": 200, "days": 30, "data": None}

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    with pytest.raises(ToolError) as excinfo:
        await server.mpstats_warehouses([1], place="ozon")
    assert _tool_error_payload(excinfo)["error"] == "parser_drift"


# --------------------------------------------------------------------------- #
# Selfcheck tri-state
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_selfcheck_inconclusive_without_token(monkeypatch):
    monkeypatch.setattr(server, "_cookie_header", lambda: "")
    resp = await server.mpstats_selfcheck()
    assert resp.status == "inconclusive"
    assert resp.healthy is None
    assert resp.checks["auth"].state == "inconclusive"


@pytest.mark.asyncio
async def test_selfcheck_success_on_healthy_payload(monkeypatch):
    async def fake_call(body, *, label):
        return _item_payload(server._SELFCHECK_SKU)

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    resp = await server.mpstats_selfcheck()
    assert resp.status == "success"
    assert resp.healthy is True
    assert resp.checks["item"].state == "healthy"
    assert resp.tool_count == 3


@pytest.mark.asyncio
async def test_selfcheck_drift_when_anchors_missing(monkeypatch):
    payload = _item_payload(server._SELFCHECK_SKU)
    # Remove an anchor field the parser reads -> drift, not success.
    del payload["items"][str(server._SELFCHECK_SKU)]["ordersGraph"]

    async def fake_call(body, *, label):
        return payload

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    resp = await server.mpstats_selfcheck()
    assert resp.status == "drift_detected"
    assert resp.healthy is False
    assert resp.checks["item"].reason == "anchors_missing"


@pytest.mark.asyncio
async def test_selfcheck_inconclusive_on_transport_failure(monkeypatch):
    async def fake_call(body, *, label):
        from mcp_core.errors import TransportDownError, raise_tool_error

        raise_tool_error(TransportDownError("timeout", provider="mpstats"))

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)
    resp = await server.mpstats_selfcheck()
    # A transport failure is inconclusive — never a parser verdict (drift).
    assert resp.status == "inconclusive"
    assert resp.healthy is None
