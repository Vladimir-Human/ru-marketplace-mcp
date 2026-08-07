"""Every mounted tool must document its return shape and its error contract.

The tool description is what an agent reads when deciding whether the tool
answers the question in front of it — and how to read the answer. A response
shape the agent has to guess at, or an error contract it cannot plan around,
costs a wrong decision at runtime. This test pins the two sections the repo's
style already standardised on (``## Return Format`` / ``## Error Format``) so a
new tool cannot ship without them.

Offline by construction: computed from the mounted FastMCP objects.
"""

from __future__ import annotations

import asyncio

from marketplace_connector import server


def test_every_tool_documents_its_return_and_error_format():
    tools = asyncio.run(server.mcp.list_tools())
    assert tools, "no tools mounted"

    missing: list[str] = []
    for tool in tools:
        description = tool.description or ""
        if "## Return Format" not in description:
            missing.append(f"{tool.name}: no '## Return Format' section")
        if "## Error Format" not in description:
            missing.append(f"{tool.name}: no '## Error Format' section")

    assert not missing, "tools shipped without agent-facing format docs:\n" + "\n".join(missing)
