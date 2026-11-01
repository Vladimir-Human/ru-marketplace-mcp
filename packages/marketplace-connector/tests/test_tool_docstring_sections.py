"""Every mounted tool keeps a core description and names its own guide resource.

The tool description is what an agent reads when deciding whether the tool
answers the question in front of it — and how to read the answer. A response
shape the agent has to guess at, or an error contract it cannot plan around,
costs a wrong decision at runtime, so the repo still requires both.

What changed (owner's order, 2026-11-01) is the channel, not the requirement.
The field list and the error mapping are reference text the agent needs only
while parsing one specific answer, and every description is paid on every
request; they now live in the per-tool resource `guide://<source>/<tool>` and in
the shared `marketplace://errors` (R-TOOL-RESOURCES). The docstring keeps the
core — what the tool answers and the invariants that change the decision — and
one line naming its guide, so a tool cannot ship without either half.

Offline by construction: computed from the mounted FastMCP objects.
"""

from __future__ import annotations

import asyncio

from marketplace_connector import server

# The wording is fixed by mcp_core.resources.guide_pointer; this file spells it
# out so the test fails if the wording and the registration ever disagree.
POINTER = "Full field list and error codes: `guide://{source}/{tool}`."


def _guide_pointer(tool_name: str) -> str:
    return POINTER.format(source=tool_name.partition("_")[0], tool=tool_name)


def test_every_tool_keeps_a_core_description_and_names_its_guide() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    assert tools, "no tools mounted"

    missing: list[str] = []
    for tool in tools:
        description = tool.description or ""
        core = description.split("\n\n", 1)[0].strip()
        if len(core) < 20:
            missing.append(f"{tool.name}: no core description ({description[:60]!r})")
        if _guide_pointer(tool.name) not in description:
            missing.append(f"{tool.name}: does not name {_guide_pointer(tool.name)!r}")

    assert not missing, "tools shipped without an agent-facing core or guide:\n" + "\n".join(missing)


def test_reference_prose_left_the_descriptions_for_the_guides() -> None:
    """The moved sections must not come back into tools/list one tool at a time."""
    tools = asyncio.run(server.mcp.list_tools())
    leaked = sorted(
        tool.name
        for tool in tools
        if "## Return Format" in (tool.description or "") or "## Error Format" in (tool.description or "")
    )
    assert not leaked, f"reference sections are back in the paid channel: {leaked}"
