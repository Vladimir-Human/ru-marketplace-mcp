"""The batch limits of wb_card belong in the advertised schema (R10).

The outside-in study found that ``[]``, ``[0]`` and 101 ids were all ACCEPTED
by the declared inputSchema and only refused at runtime, after the tool had
already been called: a client reading the schema could not know the rules.

These tests read the schema a client really sees (``tools/list``), validate the
study's batch cases against it, and prove that an invalid batch is refused
before any request goes out — while the valid boundary values still run.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from jsonschema import Draft202012Validator
from wb_connector import server


async def _input_schema(tool_name: str) -> dict:
    tools = await server.mcp.list_tools()
    for tool in tools:
        if tool.name == tool_name:
            return tool.parameters
    raise AssertionError(f"{tool_name} is not advertised")


async def test_the_advertised_schema_states_the_batch_limits() -> None:
    """The limits are machine-readable, not prose in the description."""
    schema = await _input_schema("wb_card")
    nm_ids = schema["properties"]["nm_ids"]

    assert nm_ids["minItems"] == 1
    assert nm_ids["maxItems"] == 100
    assert nm_ids["items"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize(
    ("case", "nm_ids", "accepted"),
    [
        ("empty", [], False),
        ("zero", [0], False),
        ("over_max", list(range(1, 102)), False),
        ("one", [1234], True),
        ("max", list(range(1, 101)), True),
    ],
)
async def test_the_schema_refuses_the_invalid_batches(case: str, nm_ids: list[int], accepted: bool) -> None:
    """The study's own batch cases, decided by the schema alone."""
    schema = await _input_schema("wb_card")

    assert Draft202012Validator(schema).is_valid({"nm_ids": nm_ids}) is accepted, case


async def test_an_invalid_batch_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal happens before the wire, not after it."""
    calls: list[str] = []

    async def forbidden(client, url):
        calls.append(url)
        raise AssertionError("no request may leave for an invalid batch")

    monkeypatch.setattr(server, "_safe_get_text", forbidden)

    async with Client(server.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("wb_card", {"nm_ids": []})

    assert calls == []


async def test_a_valid_boundary_batch_reaches_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control: the boundary values the schema accepts do run."""
    calls: list[str] = []

    async def fake_safe_get_text(client, url):
        calls.append(url)
        return 200, json.dumps({"products": []}), None

    # The API route is the one under test; an exported WB_TRANSPORT=dom must
    # not send this control to the browser.
    monkeypatch.setattr(server._settings, "transport", "api")
    monkeypatch.setattr(server, "_safe_get_text", fake_safe_get_text)

    result = await server.wb_card(nm_ids=list(range(1, 101)))

    assert calls, "a valid batch must reach WB"
    assert result.count == 0
