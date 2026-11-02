"""The room-count enum belongs in the advertised schema (R10).

Cian's room counts are a closed set (1..6, plus the site's own codes for a free
layout and a studio), and the rule was described in prose only: the schema
accepted any integer and the refusal came at runtime.

These tests read the schema a client really sees (``tools/list``) and check
that the closed set is machine-readable.
"""

from __future__ import annotations

import pytest
from cian_connector import server
from fastmcp import Client
from fastmcp.exceptions import ToolError
from jsonschema import Draft202012Validator

VALID_ROOMS = [1, 2, 3, 4, 5, 6, 7, 9]


async def _input_schema(tool_name: str) -> dict:
    tools = await server.mcp.list_tools()
    for tool in tools:
        if tool.name == tool_name:
            return tool.parameters
    raise AssertionError(f"{tool_name} is not advertised")


async def test_the_advertised_schema_states_the_room_enum() -> None:
    """The closed set of room counts is an enum in the schema."""
    schema = await _input_schema("cian_search")
    rooms = schema["properties"]["rooms"]

    array_branch = next(branch for branch in rooms["anyOf"] if branch.get("type") == "array")
    assert sorted(array_branch["items"]["enum"]) == VALID_ROOMS


@pytest.mark.parametrize(
    ("case", "rooms", "accepted"),
    [
        ("one_to_six", [1, 2, 3, 4, 5, 6], True),
        ("free_layout", [7], True),
        ("studio", [9], True),
        ("eight", [8], False),
        ("zero", [0], False),
        ("ten", [10], False),
    ],
)
async def test_the_schema_refuses_an_unknown_room_count(case: str, rooms: list[int], accepted: bool) -> None:
    """An unknown room count is refused by the schema alone."""
    schema = await _input_schema("cian_search")

    assert Draft202012Validator(schema).is_valid({"deal": "sale", "rooms": rooms}) is accepted, case


async def test_an_unknown_room_count_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal happens before the wire, not after it."""
    calls: list[object] = []

    async def forbidden(query, ctx):
        calls.append(query)
        raise AssertionError("no request may leave for an invalid room count")

    monkeypatch.setattr(server, "_fetch_search", forbidden)

    async with Client(server.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("cian_search", {"deal": "sale", "rooms": [8]})

    assert calls == []
