"""MPStats hands over to its own MCP server (plan item P1-8, R-MPSTATS-OFFICIAL-MCP).

START_MODULE_CONTRACT
PURPOSE: Prove every MPStats result, tool description and guide points to the official server first.
SCOPE: mpstats_item and mpstats_warehouses with a faked upstream call; the registered tool descriptions; the guides.
DEPENDS: pytest; mpstats_connector.server; mcp_core.output_schema,resilience,resources.
LINKS: M-MPSTATS; R-MPSTATS-OFFICIAL-MCP.
END_MODULE_CONTRACT
START_MODULE_MAP
test_every_result_carries_the_official_server_note: Both tools, the info note, healthy unchanged.
test_both_tool_descriptions_point_to_the_official_server_first: The first paragraph of each description.
test_both_guides_point_to_the_official_server: guide://mpstats/<tool>.
END_MODULE_MAP
"""

from __future__ import annotations

import pytest
from mcp_core import resilience as R
from mcp_core.output_schema import registered_tools
from mcp_core.resources import render_guide
from mpstats_connector import server

OFFICIAL = "https://mcp.mpstats.io/mcp?token=<your MPStats token>"
NOTE_CODE = "info:deprecated_use_official_mpstats_mcp"
TOOLS = ("mpstats_item", "mpstats_warehouses")


def _upstream(label: str) -> dict:
    """The smallest upstream answer each tool parses into one row."""
    if label == "mpstats_item":
        return {"code": 200, "days": 30, "items": {"5107857210": {"name": "Item", "price_graph": [100]}}}
    return {"code": 200, "days": 30, "data": {"5107857210": {"stocks": {"fbs": 5, "fbo": []}}}}


@pytest.mark.req("R-MPSTATS-OFFICIAL-MCP")
@pytest.mark.parametrize("tool", TOOLS)
async def test_every_result_carries_the_official_server_note(monkeypatch, tool):
    """R-MPSTATS-OFFICIAL-MCP: the note is an info: note naming the server; it does not touch healthy."""

    async def fake_call(body, *, label):
        return _upstream(label)

    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=x")
    monkeypatch.setattr(server, "_call", fake_call)

    response = await getattr(server, tool)([5107857210], place="ozon")

    notes = [warning for warning in response.meta.warnings if warning.startswith(NOTE_CODE)]
    assert len(notes) == 1
    assert OFFICIAL in notes[0] and "v2.11.0" in notes[0]
    assert R.health_warnings(response.meta.warnings) == []
    assert response.meta.healthy is True


@pytest.mark.req("R-MPSTATS-OFFICIAL-MCP")
def test_both_tool_descriptions_point_to_the_official_server_first():
    """R-MPSTATS-OFFICIAL-MCP: the agent reads the pointer before anything else in the description."""
    tools = {tool.name: tool for tool in registered_tools(server.mcp)}
    for name in TOOLS:
        paragraphs = (tools[name].description or "").split("\n\n")
        assert "Deprecated" in paragraphs[1] and OFFICIAL.replace("<your MPStats token>", "") in paragraphs[1], name


@pytest.mark.req("R-MPSTATS-OFFICIAL-MCP")
@pytest.mark.parametrize("tool", TOOLS)
def test_both_guides_point_to_the_official_server(tool):
    """R-MPSTATS-OFFICIAL-MCP: guide://mpstats/<tool> names the official server and the note."""
    guide = render_guide(tool)
    assert OFFICIAL in guide
    assert NOTE_CODE in guide
