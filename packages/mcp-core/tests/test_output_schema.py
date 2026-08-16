"""The wire-frugal output-schema reducer is the largest context-cost lever.

Full FastMCP output schemas cost ~24.5k tokens per request when the unified
marketplace server is mounted. These tests pin that the reducer keeps only
top-level property names, that the rewrite survives to the public
``list_tools`` surface, and that the helper is idempotent.
"""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from mcp_core.output_schema import EMPTY_OBJECT_SCHEMA, apply_compact_output_schemas, compact_output_schema
from pydantic import BaseModel, Field


class _Named(BaseModel):
    """A deliberately heavy return model."""

    title: str = Field(default="", description="The domain title.")
    price_rub: float | None = Field(default=None, description="Roubles, or null when absent.")
    meta: dict[str, int] = Field(default_factory=dict, description="Nested seller metadata.")


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
