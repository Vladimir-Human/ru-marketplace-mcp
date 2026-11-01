"""Reference resources are served, unique and readable, in every profile.

A guide is worth nothing if the client cannot list it or read it: the tool
description now points at ``guide://<source>/<tool>``, so the URI has to resolve
in a standalone connector, in compare, in decision and in the unified
``marketplace`` profile — and ``marketplace://errors`` must exist exactly once
in each, however many connectors the profile mounts (R-TOOL-RESOURCES).

Offline by construction: in-process FastMCP clients over the imported servers.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastmcp import Client
from fastmcp.resources import Resource
from mcp_core.errors import ErrorCode
from mcp_core.resources import ERROR_REFERENCE_URI, guide_uri

PROFILES = {
    "compare": "compare_connector.server",
    "decision": "compare_connector.decision_server",
    "full": "marketplace_connector.server",
    "wb-connector": "wb_connector.server",
}


async def _listed_uris(module_name: str) -> list[str]:
    module = importlib.import_module(module_name)
    async with Client(module.mcp) as client:
        listed = await client.list_resources()
    return [str(item.uri) for item in listed]


async def _read_text(module_name: str, uri: str) -> str:
    module = importlib.import_module(module_name)
    async with Client(module.mcp) as client:
        contents = await client.read_resource(uri)
    return contents[0].text


def _components(module_name: str) -> list[object]:
    module = importlib.import_module(module_name)
    provider = module.mcp.local_provider
    return list(provider._components.values())


@pytest.mark.parametrize("module_name", sorted(PROFILES.values()))
def test_every_mounted_tool_has_a_listed_guide(module_name: str) -> None:
    module = importlib.import_module(module_name)
    tools = asyncio.run(module.mcp.list_tools())
    assert tools, f"{module_name} mounted no tools"

    uris = asyncio.run(_listed_uris(module_name))
    missing = sorted(guide_uri(tool.name) for tool in tools if guide_uri(tool.name) not in uris)

    assert not missing, f"{module_name}: tools without a guide resource: {missing}"


@pytest.mark.parametrize("module_name", sorted(PROFILES.values()))
def test_every_listed_guide_has_reference_text(module_name: str) -> None:
    """A URI that lists but reads as a silent stub is the failure this catches.

    A tool name whose prefix names one source while ``GUIDES`` keys the tool
    under a name that collides with another connector would still list a URI
    here; the read proves the text behind it is the tool's own reference.
    """
    module = importlib.import_module(module_name)
    guides = sorted(guide_uri(tool.name) for tool in asyncio.run(module.mcp.list_tools()))

    for uri in guides:
        text = asyncio.run(_read_text(module_name, uri))
        tool_name = uri.rsplit("/", 1)[1]
        assert text.startswith(f"# {tool_name}\n\n## Return Format"), (module_name, uri, text[:80])
        assert "## Error Format" in text, (module_name, uri)


@pytest.mark.parametrize("module_name", sorted(PROFILES.values()))
def test_shared_error_reference_is_listed_exactly_once(module_name: str) -> None:
    uris = asyncio.run(_listed_uris(module_name))

    assert uris.count(ERROR_REFERENCE_URI) == 1, uris


@pytest.mark.parametrize("module_name", sorted(PROFILES.values()))
def test_shared_error_reference_lists_every_error_code(module_name: str) -> None:
    text = asyncio.run(_read_text(module_name, ERROR_REFERENCE_URI))

    missing = sorted(code.value for code in ErrorCode if f"`{code.value}`" not in text)
    assert not missing, f"{module_name}: error reference misses {missing}"


def test_the_core_still_carries_the_wb_never_zero_invariant() -> None:
    """The moved sections carried decision-changing facts; the core keeps them.

    ``price_rub is None ... never 0`` is the rule this repository exists around
    (R-NO-FABRICATION). It sat in ``## Return Format``; after the move it must
    still be in the paid description, next to the pointer, not only in the guide.
    """
    module = importlib.import_module("wb_connector.server")
    tools = {tool.name: (tool.description or "") for tool in asyncio.run(module.mcp.list_tools())}

    core = tools["wb_card"]
    assert "price_rub is None when the SKU has no sellable price — never 0" in core
    assert "NOT an error" in core  # an unknown SKU is not a failure
    assert core.index("never 0") < core.index("Full field list and error codes:")
    # Both halves: the guide still carries the full original wording.
    guide = asyncio.run(_read_text("wb_connector.server", guide_uri("wb_card")))
    assert "price_rub is None\nwhen the SKU has no sellable price — never 0" in guide


def test_a_standalone_connector_keeps_its_own_shared_reference() -> None:
    """Mounting must not strip a child's resource: one process runs four servers.

    The unified server mounts ``wb_connector.server``, so an implementation that
    removed the child's component at mount time would leave this standalone
    surface without the URI. A transform on the mount keeps both correct.
    """
    uris = asyncio.run(_listed_uris("wb_connector.server"))

    assert uris.count(ERROR_REFERENCE_URI) == 1
    local = [item for item in _components("wb_connector.server") if isinstance(item, Resource)]
    assert ERROR_REFERENCE_URI in {str(item.uri) for item in local}
    assert guide_uri("wb_card") in uris
