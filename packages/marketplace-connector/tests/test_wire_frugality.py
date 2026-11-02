"""The mounted MCP surface stays cheap without breaking SDK output wrapping.

Operator selfchecks remain unadvertised. Output schemas keep only permissive
top-level names plus the required SDK result envelope when wrapping is needed.
"""
# START_MODULE_CONTRACT
# PURPOSE: Pin mounted output-schema cost without losing the required SDK envelope.
# SCOPE: Offline mounted tool enumeration, compact schema keywords and operator-tool exclusion.
# DEPENDS: marketplace_connector.server.
# LINKS: M-MARKETPLACE; M-OUTPUT-SCHEMA; R-OUTPUT-SCHEMA-WRAPPING; R-TOOL-SCHEMA.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_no_operator_selfcheck_is_registered_as_a_tool: Pin the mounted public surface.
# test_every_output_schema_is_wire_frugal: Reject nested schemas and unneeded keywords.
# END_MODULE_MAP

from __future__ import annotations

import asyncio

from marketplace_connector import server

# START_BLOCK_WIRE_BUDGET_TESTS


def test_no_operator_selfcheck_is_registered_as_a_tool():
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}

    leaked = sorted(name for name in names if name.endswith("_selfcheck"))
    assert not leaked, f"operator-only selfchecks leaked into the MCP surface: {leaked}"
    # The count changed from 45 to 34 on purpose (11 selfchecks left), from
    # 34 to 36 when the aliexpress connector (2 tools) joined the mount, and
    # from 37 to 39 when the cian connector (2 tools) joined, then to 40 with
    # the comparison browser snapshot tool, and to 41 when the server gained its
    # second own tool, marketplace_health. It is 39 in the default test
    # environment because the two mpstats tools are not mounted without
    # MPSTATS_MP_AUTH (R-MPSTATS-MOUNT-GATE).
    assert len(tools) == 39


def test_every_output_schema_is_wire_frugal():
    tools = asyncio.run(server.mcp.list_tools())

    for tool in tools:
        schema = tool.output_schema or {}
        assert schema.get("type") == "object", tool.name
        allowed = {"type", "properties"}
        properties = schema.get("properties", {})
        if schema.get("x-fastmcp-wrap-result") is True:
            allowed |= {"x-fastmcp-wrap-result", "required"}
            assert properties == {"result": {}}, (tool.name, properties)
            assert schema["required"] == ["result"], tool.name
        assert set(schema) <= allowed, (tool.name, sorted(schema))
        assert all(value == {} for value in properties.values()), (tool.name, properties)


# END_BLOCK_WIRE_BUDGET_TESTS
