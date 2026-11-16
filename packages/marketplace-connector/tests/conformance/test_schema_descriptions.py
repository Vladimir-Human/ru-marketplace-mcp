"""Registry-wide schema descriptions state source coverage and price provenance."""
# START_MODULE_CONTRACT
# PURPOSE: Prove compare source descriptions and shared marketplace discount wording.
# SCOPE: Offline tools/list input schemas and original SDK output schemas for every registry server.
# DEPENDS: FastMCP; pytest; mcp_core.output_schema/sources; workspace connector servers.
# LINKS: M-SOURCES; M-COMPARE; R-COMPARE-SOURCE-SELECTION.descriptions; R-MARKETPLACE-PRICE-WORDING.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _schema_objects: Walk nested schemas, including definitions and union branches.
# test_compare_descriptions_name_every_compare_source: Check input and offer source descriptions against the registry.
# test_every_marketplace_discount_field_states_its_provenance: Check all tool output discount properties once.
# END_MODULE_MAP

from __future__ import annotations

import importlib
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.tools import Tool
from mcp_core.output_schema import registered_tools
from mcp_core.sources import SOURCES

# START_BLOCK_SCHEMA_WALK


def _schema_objects(schema: Any, path: str = "$schema") -> Iterator[tuple[str, dict[str, Any]]]:
    """Include definitions: compact schemas intentionally omit their descriptions."""
    if isinstance(schema, dict):
        yield path, schema
        for key, value in schema.items():
            yield from _schema_objects(value, f"{path}.{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            yield from _schema_objects(value, f"{path}[{index}]")


# END_BLOCK_SCHEMA_WALK


# START_BLOCK_DESCRIPTION_CONFORMANCE
@pytest.mark.req("R-COMPARE-SOURCE-SELECTION.descriptions")
@pytest.mark.parametrize("surface", ["input", "offer"])
async def test_compare_descriptions_name_every_compare_source(surface: str) -> None:
    """R-COMPARE-SOURCE-SELECTION.descriptions: name exactly the queryable sources."""
    module = importlib.import_module("compare_connector.server")
    if surface == "input":
        async with Client(module.mcp) as client:
            tool = next(tool for tool in await client.list_tools() if tool.name == "compare_prices")
        descriptions = [tool.inputSchema["properties"]["sources"]["description"]]
    else:
        tool = next(tool for tool in registered_tools(module.mcp) if tool.name == "compare_prices")
        original = Tool.from_function(tool.fn).output_schema
        descriptions = [
            node["properties"]["source"]["description"]
            for _path, node in _schema_objects(original)
            if {"source", "product_id", "price_rub"} <= node.get("properties", {}).keys()
        ]
    assert descriptions, surface
    expected = {source.name for source in SOURCES if source.card_tool is not None and source.text_search}
    excluded = {source.name for source in SOURCES if source.kind == "marketplace"} - expected
    for description in descriptions:
        words = set(re.findall(r"\b[a-z_]+\b", description.lower()))
        assert expected <= words, (surface, sorted(expected - words), description)
        assert not excluded & words, (surface, sorted(excluded & words), description)


@pytest.mark.req("R-MARKETPLACE-PRICE-WORDING.shared")
def test_every_marketplace_discount_field_states_its_provenance() -> None:
    """R-MARKETPLACE-PRICE-WORDING: every source's shown discounts are not verified history."""
    phrase = "as the marketplace shows it; not a verified previous price"
    modules = sorted({source.module for source in SOURCES} | {"compare_connector.decision_server"})
    checked: set[str] = set()
    failures: list[str] = []
    for module_name in modules:
        module = importlib.import_module(module_name)
        tools = registered_tools(module.mcp)
        assert tools, module_name
        for tool in tools:
            # Reuse test_output_schema.py's SDK reconstruction: advertised compact
            # output schemas have no descriptions, while their typed originals do.
            original = Tool.from_function(tool.fn).output_schema
            for path, node in _schema_objects(original):
                properties = node.get("properties", {})
                for name in ("old_price_rub", "discount_percent"):
                    if name not in properties:
                        continue
                    location = f"{module_name}.{tool.name}:{path}.{name}"
                    checked.add(location)
                    description = properties[name].get("description", "")
                    if phrase not in description:
                        failures.append(f"{location}: {description!r}")
    assert checked, "No marketplace discount fields were reached"
    assert not failures, "Discount wording missing:\n" + "\n".join(failures)


# END_BLOCK_DESCRIPTION_CONFORMANCE
