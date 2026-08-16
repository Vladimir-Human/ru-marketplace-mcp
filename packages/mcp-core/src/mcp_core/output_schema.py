"""Wire-frugal output schemas for stdio MCP servers.

FastMCP publishes the full Pydantic schema of every tool return type as
``outputSchema``. That is the single largest constant cost of a mounted MCP
server: measured on the wire for the unified marketplace server, nested output
descriptions account for ~64% (~24,518 tokens) of the ~38,078 tokens sent with
EVERY client request.

The same field names are already documented for the model in each tool's
``## Return Format`` prose, so the advertised schema is reduced to top-level
property names. This keeps the client-side ``structuredContent`` validation
permissive (each property accepts any JSON value) while preserving rename
detection through ``public_contract.json``, which pins the property name set.

The compact form keeps no ``$defs``, no per-field descriptions, defaults or
nested types — that is deliberate, and a test guards it.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool

# One shared empty-object schema. All MCP-visible tools in this repo return an
# object; if a future tool does not, it is wrapped in a permissive object schema
# rather than leaving the raw MCP schema behind.
EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object"}


def compact_output_schema(schema: Any) -> dict[str, Any]:
    """Reduce a FastMCP-generated output schema to top-level field names.

    ``{}`` as a property schema is JSON Schema for "any value", so clients
    that validate ``structuredContent`` accept every shape the models actually
    produce without carrying the nested type machinery.
    """
    if not isinstance(schema, dict):
        return EMPTY_OBJECT_SCHEMA
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, dict):
        return EMPTY_OBJECT_SCHEMA
    return {"type": "object", "properties": {name: {} for name in properties}}


def apply_compact_output_schemas(mcp: FastMCP) -> int:
    """Re-write every registered tool's ``output_schema`` to the compact form.

    FastMCP exposes no public mutator for already-registered tools in the
    installed version, so this reaches the local provider's component table,
    which ``list_tools`` reads directly at request time. The access is pinned by
    ``test_output_schema.py``: if a FastMCP upgrade renames the internals, that
    test either fails or is the single place to adapt.

    Returns the number of tools re-written so the caller can choose to log it.
    """
    # Ignored by mypy because FastMCP's public surface intentionally does not
    # expose the provider internals this constrained patch relies on.
    provider = getattr(mcp, "_local_provider", None)
    components: dict[str, Any] = getattr(provider, "_components", {})

    rewritten = 0
    for component in tuple(components.values()):
        if not isinstance(component, Tool):
            continue
        component.output_schema = compact_output_schema(component.output_schema)
        rewritten += 1
    return rewritten
