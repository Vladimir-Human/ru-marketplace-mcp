"""The mounted MCP surface stays cheap by construction.

Two measured levers are pinned here:

1. Operator ``*_selfcheck`` diagnostics must not be MCP tools — they exist for
   ``marketplace-mcp doctor`` and cost ~7.5k wire tokens when advertised.
2. ``outputSchema`` must stay compact (top-level field names only): full
   Pydantic output trees were measured at ~24.5k tokens, 64% of the unified
   server's original per-request cost.
"""

from __future__ import annotations

import asyncio

from marketplace_connector import server


def test_no_operator_selfcheck_is_registered_as_a_tool():
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}

    leaked = sorted(name for name in names if name.endswith("_selfcheck"))
    assert not leaked, f"operator-only selfchecks leaked into the MCP surface: {leaked}"
    # The count changed from 45 to 34 on purpose: 11 selfchecks left.
    assert len(tools) == 34


def test_every_output_schema_is_wire_frugal():
    tools = asyncio.run(server.mcp.list_tools())

    for tool in tools:
        schema = tool.output_schema or {}
        assert schema.get("type") == "object", tool.name
        assert set(schema) <= {"type", "properties"}, (tool.name, sorted(schema))
        properties = schema.get("properties", {})
        assert all(value == {} for value in properties.values()), (tool.name, properties)
