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
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from marketplace_connector import server

from .conformance.test_output_size import build_full_server

# START_MODULE_CONTRACT
# PURPOSE: Preserve guide pointers and guard R-INTERFACE-GUARDS.descriptions.
# SCOPE: Full registry-mounted tools/list, non-empty input descriptions and 1600-character tool descriptions; offline only.
# DEPENDS: FastMCP; pytest; marketplace server; conformance full-profile assembly.
# LINKS: M-INTERFACE-GUARDS; M-SOURCES; R-INTERFACE-GUARDS.descriptions.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _guide_pointer: The existing per-tool guide wording.
# assert_description_guards: Check every listed parameter and the full tool description length.
# test_*: Existing guide checks, full-profile description guards, and planted failures.
# END_MODULE_MAP

# START_BLOCK_GUIDE_CHECKS

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


# END_BLOCK_GUIDE_CHECKS
# START_BLOCK_DESCRIPTION_GUARDS
DESCRIPTION_CAP = 1_600


def assert_description_guards(tools: list[Any]) -> None:
    """Check the actual tools/list schema, not function signatures or a snapshot."""
    assert tools, "no tools mounted in the full profile"
    failures = []
    for tool in tools:
        description = tool.description or ""
        if len(description) > DESCRIPTION_CAP:
            failures.append(
                f"{tool.name}: description has {len(description)} characters, exceeds {DESCRIPTION_CAP}; move reference text to its guide"
            )
        for name, prop in tool.input_schema.get("properties", {}).items():
            text = prop.get("description")
            if not isinstance(text, str) or not text.strip():
                failures.append(f"{tool.name}.{name}: undocumented parameter (non-empty description required)")
    assert not failures, "interface description guards failed:\n" + "\n".join(failures)


@pytest.mark.req("R-INTERFACE-GUARDS.descriptions")
async def test_every_full_profile_parameter_is_documented_and_descriptions_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_full_server(monkeypatch)
    async with Client(app) as client:
        assert_description_guards(await client.list_tools())


@pytest.mark.req("R-INTERFACE-GUARDS.descriptions")
async def test_description_guard_rejects_a_planted_undocumented_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_full_server(monkeypatch)
    # A copy-on-mount overlay leaves the real full server and its tools unchanged.
    copied = FastMCP("interface-guards-copy")
    copied.mount(app)

    @copied.tool
    def planted_undocumented(value: str) -> str:
        """Throwaway negative control; its input intentionally has no description."""
        return value

    async with Client(copied) as client:
        tools = await client.list_tools()
    assert any(tool.name == "yandex_search" for tool in tools), "the copy must retain real mounted tools"
    with pytest.raises(AssertionError, match=r"planted_undocumented\.value: undocumented parameter"):
        assert_description_guards(tools)


@pytest.mark.req("R-INTERFACE-GUARDS.descriptions")
async def test_description_guard_rejects_a_planted_long_tool_description(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_full_server(monkeypatch)
    copied = FastMCP("interface-guards-long-description-copy")
    copied.mount(app)

    @copied.tool(description="x" * (DESCRIPTION_CAP + 1))
    def planted_long_description() -> str:
        return "offline"

    async with Client(copied) as client:
        tools = await client.list_tools()
    with pytest.raises(AssertionError, match="planted_long_description: description has 1601 characters"):
        assert_description_guards(tools)


# END_BLOCK_DESCRIPTION_GUARDS
