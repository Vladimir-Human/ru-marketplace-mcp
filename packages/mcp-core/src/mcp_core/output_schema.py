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
nested types — that is deliberate, and a test guards it. SDK-wrapped returns
retain ``x-fastmcp-wrap-result`` and require their ``result`` envelope: FastMCP
uses that flag to build ``structuredContent``, not just to describe it. Tool
errors remain MCP ``isError`` replies without successful structured output.
"""
# START_MODULE_CONTRACT
# PURPOSE: Reduce advertised MCP output schemas while preserving SDK result envelopes.
# SCOPE: Schema compaction and local Tool mutation; preserve wrapping, not domain validation or error parsing.
# DEPENDS: FastMCP and fastmcp.tools.Tool; stdlib typing.
# LINKS: M-OUTPUT-SCHEMA; M-MODELS; R-OUTPUT-SCHEMA-WRAPPING; R-TOOL-SCHEMA.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# compact_output_schema: Keep permissive property names plus the SDK's required result wrapper.
# registered_tools: Read the Tool objects this server answers tools/list with.
# apply_compact_output_schemas: Rewrite local Tool schemas and count rewritten tools.
# END_MODULE_MAP

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool

# START_BLOCK_SCHEMA_COMPACTION
# One shared empty-object schema for missing or unknown object schemas.
# FastMCP's non-object/union schemas already carry a result wrapper; keep it.
EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object"}


def compact_output_schema(schema: Any) -> dict[str, Any]:
    """Reduce a FastMCP-generated output schema to top-level field names.

    ``{}`` as a property schema accepts any JSON value without nested types.
    Keep the SDK's wrapping flag and require ``result`` for wrapped returns;
    otherwise FastMCP serializes the raw value under a fictitious wrapper schema.
    """
    if not isinstance(schema, dict):
        return EMPTY_OBJECT_SCHEMA
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, dict):
        return EMPTY_OBJECT_SCHEMA
    compact: dict[str, Any] = {"type": "object", "properties": {name: {} for name in properties}}
    if schema.get("x-fastmcp-wrap-result"):
        compact["x-fastmcp-wrap-result"] = True
        compact["required"] = ["result"]
    return compact


# END_BLOCK_SCHEMA_COMPACTION


# START_BLOCK_REGISTERED_TOOL_COMPACTION
def registered_tools(mcp: FastMCP) -> tuple[Tool, ...]:
    """Every Tool on this server's local provider, in registration order.

    Mounted child providers are not included: this is the local component table
    ``apply_compact_output_schemas`` rewrites and ``list_tools`` reads, not the
    whole ``tools/list`` surface. FastMCP exposes no public enumeration for
    already-registered local components in the installed version, so the table
    is read directly; ``test_output_schema.py`` pins that access.
    """
    provider = getattr(mcp, "_local_provider", None)
    components: dict[str, Any] = getattr(provider, "_components", {})
    return tuple(component for component in components.values() if isinstance(component, Tool))


def apply_compact_output_schemas(mcp: FastMCP) -> int:
    """Re-write every registered tool's ``output_schema`` to the compact form.

    FastMCP exposes no public mutator for already-registered tools in the
    installed version, so this reaches the local provider's component table,
    which ``list_tools`` reads directly at request time. The access is pinned by
    ``test_output_schema.py``: if a FastMCP upgrade renames the internals, that
    test either fails or is the single place to adapt.

    Returns the number of tools re-written so the caller can choose to log it.
    """
    rewritten = 0
    for component in registered_tools(mcp):
        component.output_schema = compact_output_schema(component.output_schema)
        rewritten += 1
    return rewritten


# END_BLOCK_REGISTERED_TOOL_COMPACTION
