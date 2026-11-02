"""The MPStats limits belong in the advertised schema (R10).

The outside-in study found that the batch bounds and the marketplace enum were
described in prose only: the schema accepted an empty SKU list, a
non-positive SKU and a marketplace MPStats does not have, and the refusal came
only at runtime.

These tests read the schema a client really sees (``tools/list``), validate the
cases against it, and prove that an invalid call is refused before the request
— while the valid boundary values still run.
"""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from jsonschema import Draft202012Validator
from mpstats_connector import server

TOOLS = ("mpstats_item", "mpstats_warehouses")


async def _input_schema(tool_name: str) -> dict:
    tools = await server.mcp.list_tools()
    for tool in tools:
        if tool.name == tool_name:
            return tool.parameters
    raise AssertionError(f"{tool_name} is not advertised")


@pytest.mark.parametrize("tool_name", TOOLS)
async def test_the_advertised_schema_states_the_batch_limits(tool_name: str) -> None:
    """The limits are machine-readable, not prose in the description."""
    schema = await _input_schema(tool_name)
    skus = schema["properties"]["skus"]

    assert skus["minItems"] == 1
    assert skus["maxItems"] == server.MAX_SKUS
    assert skus["items"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize("tool_name", TOOLS)
async def test_the_advertised_schema_states_the_marketplace_enum(tool_name: str) -> None:
    """The closed set of marketplaces is an enum in the schema."""
    schema = await _input_schema(tool_name)
    place = schema["properties"]["place"]

    assert sorted(place["enum"]) == ["ozon", "wildberries"]


@pytest.mark.parametrize("tool_name", TOOLS)
@pytest.mark.parametrize(
    ("case", "skus", "place", "accepted"),
    [
        ("empty", [], "ozon", False),
        ("zero", [0], "ozon", False),
        ("over_max", list(range(1, 102)), "ozon", False),
        ("unknown_place", [1], "lamoda", False),
        ("one", [1], "ozon", True),
        ("max", list(range(1, 101)), "wildberries", True),
    ],
)
async def test_the_schema_refuses_the_invalid_calls(
    tool_name: str, case: str, skus: list[int], place: str, accepted: bool
) -> None:
    """The invalid cases are decided by the schema alone."""
    schema = await _input_schema(tool_name)

    assert Draft202012Validator(schema).is_valid({"skus": skus, "place": place}) is accepted, case


async def test_an_invalid_call_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal happens before the wire, not after it."""
    calls: list[dict] = []

    async def forbidden(body, *, label):
        calls.append(body)
        raise AssertionError("no request may leave for an invalid call")

    monkeypatch.setattr(server, "_call", forbidden)
    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")

    async with Client(server.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("mpstats_item", {"skus": [], "place": "ozon"})

    assert calls == []


async def test_a_valid_boundary_batch_reaches_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control: the boundary values the schema accepts do run."""
    calls: list[dict] = []

    async def fake_call(body, *, label):
        calls.append(body)
        return {
            "code": 200,
            "days": 30,
            "items": {str(sku): {"Sku": sku, "Orders": [1], "ordersGraph": [1]} for sku in body["Sku"]},
        }

    monkeypatch.setattr(server, "_call", fake_call)
    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")

    result = await server.mpstats_item(list(range(1, 101)), place="ozon")

    assert calls, "a valid batch must reach MPStats"
    assert result.count == 100
