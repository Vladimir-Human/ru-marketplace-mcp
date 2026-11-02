"""Compact output schemas stay cheap without changing SDK result envelopes.

Full FastMCP output schemas cost ~24.5k tokens per request when the unified
marketplace server is mounted. Keep permissive top-level property names and
SDK wrapping, including across repeated compaction and real MCP calls.
"""
# START_MODULE_CONTRACT
# PURPOSE: Verify compact schemas and SDK result wrapping through in-process MCP.
# SCOPE: Offline object/union/list/scalar serialization and every shipped compacter caller.
# DEPENDS: FastMCP; jsonschema; pydantic; mcp_core.output_schema/sources; workspace connectors.
# LINKS: M-OUTPUT-SCHEMA; R-OUTPUT-SCHEMA-WRAPPING; V-M-OUTPUT-SCHEMA.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _heavy_tool: Build a named-model positive control with a heavy schema.
# test_wrapped_outputs_survive_compaction_through_mcp: Validate real replies against tools/list.
# test_every_compacted_tool_preserves_its_sdk_output_contract: Compare actual MCP schemas to SDK originals.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import importlib
from dataclasses import asdict, is_dataclass

from fastmcp import Client, FastMCP
from fastmcp.tools import Tool
from jsonschema import validate
from mcp_core.output_schema import (
    EMPTY_OBJECT_SCHEMA,
    apply_compact_output_schemas,
    compact_output_schema,
    registered_tools,
)
from mcp_core.sources import SOURCES
from pydantic import BaseModel, Field

# START_BLOCK_TEST_MODELS


class _Named(BaseModel):
    """A deliberately heavy return model."""

    title: str = Field(default="", description="The domain title.")
    price_rub: float | None = Field(default=None, description="Roubles, or null when absent.")
    meta: dict[str, int] = Field(default_factory=dict, description="Nested seller metadata.")


class _NoResults(BaseModel):
    status: str = "no_results"


def _heavy_tool() -> FastMCP:
    app = FastMCP("unittest")

    @app.tool(name="named")
    async def named() -> _Named:
        """Return a named thing.

        ## Return Format

        _Named: {title, price_rub, meta}.
        """

        return _Named(title="t", price_rub=1.5)

    return app


# END_BLOCK_TEST_MODELS


# START_BLOCK_COMPACTION_TESTS
def test_compact_keeps_only_top_level_names():
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Drop me"},
            "meta": {"$ref": "#/$defs/Meta", "description": "Drop me"},
        },
        "$defs": {"Meta": {"type": "object"}},
    }

    assert compact_output_schema(schema) == {"type": "object", "properties": {"title": {}, "meta": {}}}


def test_compact_falls_back_to_permissive_object_for_unknown_shapes():
    assert compact_output_schema(None) == EMPTY_OBJECT_SCHEMA
    assert compact_output_schema({"type": "array"}) == EMPTY_OBJECT_SCHEMA
    assert compact_output_schema({"type": "object"}) == EMPTY_OBJECT_SCHEMA


def test_registered_tool_reaches_clients_with_a_compact_schema():
    app = _heavy_tool()

    assert apply_compact_output_schemas(app) == 1

    tools = asyncio.run(app.list_tools())
    assert len(tools) == 1
    schema = tools[0].output_schema
    assert schema == {"type": "object", "properties": {"title": {}, "price_rub": {}, "meta": {}}}


def test_apply_is_idempotent():
    app = _heavy_tool()

    assert apply_compact_output_schemas(app) == 1
    assert apply_compact_output_schemas(app) == 1

    tools = asyncio.run(app.list_tools())
    assert tools[0].output_schema == {"type": "object", "properties": {"title": {}, "price_rub": {}, "meta": {}}}


# END_BLOCK_COMPACTION_TESTS


# START_BLOCK_MCP_WRAPPING_TESTS
async def test_wrapped_outputs_survive_compaction_through_mcp():
    """R-OUTPUT-SCHEMA-WRAPPING: compaction preserves SDK serialization and decoding."""
    app = _heavy_tool()

    @app.tool
    async def union(empty: bool = False) -> _Named | _NoResults:
        return _NoResults() if empty else _Named(title="t", price_rub=1.5)

    @app.tool
    async def sequence() -> list[_Named]:
        return [_Named(title="t", price_rub=1.5)]

    @app.tool
    async def scalar() -> str:
        return "ok"

    assert apply_compact_output_schemas(app) == 4
    assert apply_compact_output_schemas(app) == 4
    named_payload = {"title": "t", "price_rub": 1.5, "meta": {}}
    cases = [
        ("named", {}, named_payload),
        ("union", {"empty": False}, named_payload),
        ("union", {"empty": True}, {"status": "no_results"}),
        ("sequence", {}, [named_payload]),
        ("scalar", {}, "ok"),
    ]

    async with Client(app) as client:
        schemas = {tool.name: tool.output_schema for tool in await client.list_tools()}
        for name, arguments, payload in cases:
            result = await client.call_tool(name, arguments)
            expected = payload if name == "named" else {"result": payload}
            assert result.structured_content == expected, (name, result.structured_content)
            validate(result.structured_content, schemas[name])
            data = asdict(result.data) if is_dataclass(result.data) else result.data
            assert data == payload, name
            if name != "named":
                assert schemas[name] == {
                    "type": "object",
                    "properties": {"result": {}},
                    "x-fastmcp-wrap-result": True,
                    "required": ["result"],
                }
                assert result.meta["fastmcp"]["wrap_result"] is True


# END_BLOCK_MCP_WRAPPING_TESTS


# START_BLOCK_ALL_CALLER_WRAPPING_TESTS
async def test_every_compacted_tool_preserves_its_sdk_output_contract():
    """R-OUTPUT-SCHEMA-WRAPPING: no local or mounted caller loses SDK wrapping."""
    modules = sorted({source.module for source in SOURCES} | {"compare_connector.decision_server"})
    checked = 0
    for module_name in modules:
        module = importlib.import_module(module_name)
        local_tools = registered_tools(module.mcp)
        assert local_tools, module_name
        async with Client(module.mcp) as client:
            advertised = {tool.name: tool.output_schema for tool in await client.list_tools()}
        for tool in local_tools:
            original = Tool.from_function(tool.fn).output_schema or {}
            schema = advertised[tool.name]
            assert bool(schema.get("x-fastmcp-wrap-result")) == bool(original.get("x-fastmcp-wrap-result")), (
                module_name,
                tool.name,
            )
            assert set(schema.get("properties", {})) == set(original.get("properties", {})), (
                module_name,
                tool.name,
            )
            if original.get("x-fastmcp-wrap-result"):
                assert schema["required"] == original["required"] == ["result"], (module_name, tool.name)
            checked += 1
    assert checked


# END_BLOCK_ALL_CALLER_WRAPPING_TESTS
