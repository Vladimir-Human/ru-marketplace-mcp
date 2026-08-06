"""Pin the public contract of every mounted tool against a committed snapshot.

The agent-facing surface — tool names, argument names and types, response field
names and types — must not change silently: an agent that learned the contract
breaks invisibly when a field renames or retypes while the suite stays green.
This test compares the live contract of the unified server against
``fixtures/public_contract.json`` and fails on any structural difference.

Prose (``description`` and ``title``) is deliberately not part of the snapshot:
wording may improve while structure is pinned.

Regenerate the snapshot only when a contract change is an intentional owner
decision, never to make this test pass:

    uv run python packages/marketplace-connector/tests/test_public_contract_snapshot.py

Offline by construction: the projection is computed from the mounted FastMCP
objects, no network involved.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

from marketplace_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "public_contract.json"

# Wording keys are prose: improving them must not read as a contract break.
_PROSE_KEYS = {"description", "title"}

# Schema containers whose keys are NAMES, not JSON-schema keywords: a response
# may legitimately carry a field called "title" or "description" (most product
# cards do), and stripping those would blind the snapshot to exactly the
# rename it exists to catch.
_NAME_CONTAINERS = {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}


def _strip_prose(node: Any, parent_key: str | None = None) -> Any:
    if isinstance(node, dict):
        keep_names = parent_key in _NAME_CONTAINERS
        return {key: _strip_prose(value, key) for key, value in node.items() if keep_names or key not in _PROSE_KEYS}
    if isinstance(node, list):
        return [_strip_prose(item, parent_key) for item in node]
    return node


def _project(tools: list[Any]) -> dict[str, Any]:
    """The contract a client can rely on: per tool, argument and response shape."""
    return {
        tool.name: {
            "parameters": _strip_prose(tool.parameters),
            "output": _strip_prose(tool.output_schema),
        }
        for tool in tools
    }


def _live_contract() -> dict[str, Any]:
    return _project(asyncio.run(server.mcp.list_tools()))


def _contract_diff(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Human-readable drift between two contracts; empty means identical."""
    diffs: list[str] = []
    for name in sorted(expected.keys() - actual.keys()):
        diffs.append(f"tool removed: {name}")
    for name in sorted(actual.keys() - expected.keys()):
        diffs.append(f"tool added: {name}")
    for name in sorted(expected.keys() & actual.keys()):
        if expected[name] != actual[name]:
            before = json.dumps(expected[name], sort_keys=True, ensure_ascii=False)
            after = json.dumps(actual[name], sort_keys=True, ensure_ascii=False)
            diffs.append(f"tool changed: {name}\n  snapshot: {before[:1000]}\n  live:     {after[:1000]}")
    return diffs


def test_the_live_contract_matches_the_committed_snapshot():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    diffs = _contract_diff(expected, _live_contract())

    assert not diffs, (
        "the public contract drifted from fixtures/public_contract.json:\n"
        + "\n".join(diffs)
        + "\nIf the change is an intentional owner decision, regenerate the snapshot:"
        "\n  uv run python packages/marketplace-connector/tests/test_public_contract_snapshot.py"
    )


def test_the_snapshot_projection_is_deterministic():
    """An unstable projection would produce false drift; pin that here."""
    assert _live_contract() == _live_contract()


def test_a_renamed_tool_is_reported():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(snapshot)
    mutated["wb_search_renamed"] = mutated.pop("wb_search")

    diffs = _contract_diff(mutated, _live_contract())

    assert any("wb_search" in diff for diff in diffs)


def test_a_retyped_argument_is_reported():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(snapshot)
    mutated["wb_card"]["parameters"]["properties"]["nm_ids"]["type"] = "string"

    diffs = _contract_diff(mutated, _live_contract())

    assert any(diff.startswith("tool changed: wb_card") for diff in diffs)


def test_a_removed_response_field_is_reported():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(snapshot)
    mutated["avito_card"]["output"]["properties"].pop("price_rub")

    diffs = _contract_diff(mutated, _live_contract())

    assert any(diff.startswith("tool changed: avito_card") for diff in diffs)


def test_prose_is_not_part_of_the_contract():
    """Rewording a field must not read as drift — only structure is pinned."""
    assert _strip_prose({"type": "string", "description": "old wording"}) == _strip_prose(
        {"type": "string", "description": "new wording"}
    )


def test_fields_named_like_prose_keys_are_still_pinned():
    """Regression: a response field called ``title`` or ``description`` is a
    domain field, not wording. The strip must be positional — keying off the
    schema container — or the most visible field of a product card silently
    disappears from the snapshot and can be renamed without the test firing."""
    live = _live_contract()

    assert "title" in live["avito_card"]["output"]["properties"]
    assert "description" in live["avito_card"]["output"]["properties"]

    mutated = copy.deepcopy(live)
    mutated["avito_card"]["output"]["properties"].pop("title")
    assert any(diff.startswith("tool changed: avito_card") for diff in _contract_diff(live, mutated))


if __name__ == "__main__":
    snapshot = json.dumps(_live_contract(), indent=2, sort_keys=True, ensure_ascii=False)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(snapshot + "\n", encoding="utf-8")
