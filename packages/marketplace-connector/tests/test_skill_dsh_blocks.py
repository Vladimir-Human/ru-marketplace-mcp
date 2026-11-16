"""Every skill's DSH block must name the tools the default profile really exposes.

The default DeepSeek Harness profile mounts the cheap compare server
(``compare-mcp``); the middle profile adds one card inspector and the full
profile mounts every marketplace. The skills used to promise «only
``compare_prices`` and ``compare_sources``» — two of the four compare tools —
so an agent that read a skill believed half of the default surface did not
exist, and a renamed or added compare tool left fourteen skills stale.

The tool sets here are computed from the code — the compare server's own
registrations, the decision profile's and the unified server's — never from a
list typed into this file, so the blocks are checked against what the mounts
really publish.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS = REPO_ROOT / "skills"

_HEADING_RE = re.compile(r"^##\s+DSH activation\s*$", re.MULTILINE)
_TOOL_NAME_RE = re.compile(r"`([a-z][a-z0-9_]*)`")

# The compare-prices skill describes the default mount itself, so it has no
# activation block: its tools are the ones the default profile exposes.
_SKILLS_WITHOUT_BLOCK = {"compare-prices"}


def _tool_names(server: object) -> set[str]:
    """The tools a mounted server publishes."""
    return {tool.name for tool in asyncio.run(server.list_tools())}  # type: ignore[attr-defined]


def _compare_tools() -> set[str]:
    """The default profile's tools: the cheap compare mount."""
    from compare_connector.server import mcp

    return _tool_names(mcp)


def _decision_tools() -> set[str]:
    """The middle profile's tools: the compare mount plus one card inspector."""
    from compare_connector.decision_server import mcp

    return _tool_names(mcp)


def _unified_tools() -> set[str]:
    """The full profile's tools: every marketplace plus the server's own two."""
    from marketplace_connector.server import mcp

    return _tool_names(mcp)


def _dsh_block(text: str) -> str:
    """The text of the '## DSH activation' section, or an empty string."""
    match = _HEADING_RE.search(text)
    if match is None:
        return ""
    return text[match.end() :].strip()


def _first_paragraph(block: str) -> str:
    """The first paragraph of a block — the one that describes the default."""
    for chunk in re.split(r"\n\s*\n", block):
        text = chunk.strip()
        if text:
            return text
    return ""


def _default_mount_tools_named(block: str) -> set[str]:
    """The tool names the block's default-profile paragraph names."""
    return set(_TOOL_NAME_RE.findall(_first_paragraph(block)))


def _skills_with_a_dsh_block() -> list[Path]:
    found = []
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        if path.parent.name in _SKILLS_WITHOUT_BLOCK:
            continue
        if _dsh_block(path.read_text(encoding="utf-8")):
            found.append(path)
    return found


SKILLS_WITH_BLOCK = _skills_with_a_dsh_block()


def test_the_gate_covers_every_skill_that_needs_an_activation_block():
    """Guard the guard: an empty list would make every check below vacuous."""
    every_skill = [path for path in sorted(SKILLS.glob("*/SKILL.md")) if path.parent.name not in _SKILLS_WITHOUT_BLOCK]

    assert len(SKILLS_WITH_BLOCK) == len(every_skill), (
        f"skills without a DSH activation block: "
        f"{[path.parent.name for path in every_skill if path not in SKILLS_WITH_BLOCK]}"
    )
    assert len(SKILLS_WITH_BLOCK) >= 12


@pytest.mark.req("R-SKILL-DSH-BLOCK")
@pytest.mark.parametrize("path", SKILLS_WITH_BLOCK, ids=lambda path: path.parent.name)
def test_the_dsh_block_names_exactly_the_default_mount_tools(path: Path):
    """The default profile's tools, named one by one, with their real count."""
    block = _dsh_block(path.read_text(encoding="utf-8"))
    named = _default_mount_tools_named(block)
    compare_tools = _compare_tools()

    assert named == compare_tools, (
        f"{path.parent.name}: the DSH block names {sorted(named)} instead of the "
        f"default mount's {sorted(compare_tools)}"
    )
    assert f"{len(compare_tools)} tools" in _first_paragraph(block), (
        f"{path.parent.name}: the DSH block must state the real count ({len(compare_tools)} tools)"
    )


@pytest.mark.req("R-SKILL-DSH-BLOCK")
@pytest.mark.parametrize("path", SKILLS_WITH_BLOCK, ids=lambda path: path.parent.name)
def test_the_dsh_block_names_no_tool_that_does_not_exist(path: Path):
    """A block that names a ghost tool sends the agent after a name that never
    resolves, which reads as the marketplace being down."""
    block = _dsh_block(path.read_text(encoding="utf-8"))
    real = _compare_tools() | _decision_tools() | _unified_tools()
    mentioned = set(_TOOL_NAME_RE.findall(block))

    ghosts = sorted(name for name in mentioned if name not in real)
    assert not ghosts, f"{path.parent.name}: the DSH block names tools that do not exist: {ghosts}"


@pytest.mark.req("R-SKILL-DSH-BLOCK")
def test_the_gate_rejects_a_block_that_names_only_two_compare_tools():
    """A gate that cannot fail is worse than no gate at all."""
    stale = (
        "In DeepSeek Harness, the default profile exposes only `compare_prices` and\n"
        "`compare_sources` through the cheap compare mount."
    )

    assert _default_mount_tools_named(stale) != _compare_tools()


@pytest.mark.req("R-SKILL-DSH-BLOCK")
def test_the_default_mount_really_is_the_compare_mount():
    """The blocks are about the compare mount because that is what the shipped
    DSH patch enables when neither the full nor the decision switch is set."""
    from marketplace_connector.cli import _dsh_patch_block

    patch, _note = _dsh_patch_block()
    guards = re.findall(
        r'- id: ru-marketplace-(compare|decision|full)\s+.*?disabled: !!js "([^"]+)"',
        patch,
        flags=re.DOTALL,
    )
    env = {"DIR": "checkout", "DECISION": "", "FULL": ""}
    enabled = []
    for name, expression in guards:
        disabled = []
        for term in expression.split(" || "):
            match = re.fullmatch(r"(!{1,2})process\.env\.RU_MARKETPLACE_MCP_(DIR|DECISION|FULL)", term)
            assert match, f"unsupported DSH guard: {term}"
            value = bool(env[match.group(2)])
            disabled.append(value if match.group(1) == "!!" else not value)
        if not any(disabled):
            enabled.append(name)

    assert enabled == ["compare"]
