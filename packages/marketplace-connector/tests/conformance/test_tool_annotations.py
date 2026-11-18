"""Registry-driven MCP ToolAnnotations and wire title deduplication conformance.

START_MODULE_CONTRACT
PURPOSE: Prove every shipped MCP tool advertises accurate ToolAnnotations and a single canonical Tool.title.
SCOPE: All registry connector servers plus compare, decision and unified marketplace servers; offline Client(mcp) only.
DEPENDS: pytest; importlib; fastmcp.Client; mcp_core.sources.
LINKS: M-OUTPUT-SCHEMA; M-SOURCES; M-RUNTIME; R-TOOL-SCHEMA.
END_MODULE_CONTRACT
START_MODULE_MAP
SERVER_MODULES: Every standalone and mounted MCP server module in the workspace.
NON_IDEMPOTENT_TOOLS: Tools whose call reads/consumes expiring live browser state.
LOCAL_ONLY_TOOLS: Introspection/health tools that send zero marketplace network traffic.
test_every_advertised_tool_has_complete_deduplicated_annotations: Verify Tool.title and all four annotation hints on wire dicts.
END_MODULE_MAP
"""

from __future__ import annotations

import importlib

import pytest
from fastmcp import Client
from mcp_core.sources import SOURCES
from pydantic import SecretStr

# START_BLOCK_SERVER_INVENTORY
SERVER_MODULES: tuple[str, ...] = tuple(
    sorted({spec.module for spec in SOURCES} | {"compare_connector.decision_server", "marketplace_connector.server"})
)
NON_IDEMPOTENT_TOOLS = frozenset({"compare_browser_snapshot"})
LOCAL_ONLY_TOOLS = frozenset({"compare_sources", "marketplace_sources", "marketplace_health"})
# END_BLOCK_SERVER_INVENTORY


# START_BLOCK_CONFORMANCE_TESTS
@pytest.mark.req("R-TOOL-SCHEMA")
@pytest.mark.parametrize("module_name", SERVER_MODULES)
async def test_every_advertised_tool_has_complete_deduplicated_annotations(module_name: str, monkeypatch) -> None:
    module = importlib.import_module(module_name)
    if module_name == "mpstats_connector.server":
        monkeypatch.setattr(module._settings, "mp_auth", SecretStr("offline-test-placeholder"))
    async with Client(module.mcp) as client:
        tools = await client.list_tools()
    assert tools, f"{module_name} advertised zero tools"

    for tool in tools:
        label = f"{module_name}:{tool.name}"
        assert isinstance(tool.title, str) and tool.title.strip(), f"{label} missing top-level Tool.title"
        assert tool.annotations is not None, f"{label} missing ToolAnnotations"
        assert tool.annotations.title is None, f"{label} still duplicates title inside annotations.title"
        assert tool.annotations.readOnlyHint is True, f"{label} must set readOnlyHint=True"
        assert tool.annotations.destructiveHint is False, f"{label} must set destructiveHint=False"
        assert isinstance(tool.annotations.idempotentHint, bool), f"{label} missing explicit bool idempotentHint"
        assert tool.annotations.idempotentHint is (tool.name not in NON_IDEMPOTENT_TOOLS), label
        assert isinstance(tool.annotations.openWorldHint, bool), f"{label} missing explicit bool openWorldHint"
        assert tool.annotations.openWorldHint is (tool.name not in LOCAL_ONLY_TOOLS), label

        wire = tool.model_dump(by_alias=True, exclude_none=True)
        assert wire.get("title") == tool.title, label
        assert "title" not in wire.get("annotations", {}), f"{label} serialized duplicate annotations.title on wire"


# END_BLOCK_CONFORMANCE_TESTS
