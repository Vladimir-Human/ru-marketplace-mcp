#!/usr/bin/env python3
"""Generate docs/TOOLS.md and the README context-cost blocks from the tool schemas.

The tables in ``README.md`` describe tools by hand, so they drift from the surface a
client actually pays for. This script imports the three MCP profiles in-process,
calls their public ``list_tools()`` (after output-schema compaction), and renders:

* every tool exactly once, grouped by its name prefix, with parameters taken from
  the input JSON schema and the first paragraph of the description;
* the measured ``tools/list`` payload size and an approximate token count per
  profile;
* the two ``<!-- tools-cost:start -->`` blocks in ``README.md`` (Russian first,
  English second), so the published cost figures come from the same measurement.

The output is deterministic: sorted tools and groups, no timestamps, no paths, no
Python-version-specific text, and LF line endings. ``--check`` regenerates in
memory and fails when either file differs; CI runs that mode.

Run: uv run python scripts/gen_tools_doc.py [--check]
"""

# START_MODULE_CONTRACT
# PURPOSE: Generate docs/TOOLS.md and the README cost blocks from the three MCP profiles.
# SCOPE: Offline in-process list_tools introspection; JSON-schema parameter rendering; deterministic output.
# DEPENDS: FastMCP servers; stdlib argparse, asyncio, importlib, json, re; no network.
# LINKS: M-TOOLSDOC; M-MARKETPLACE; M-COMPARE; M-OUTPUT-SCHEMA; R-DOCS-TOOLS.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# Profile: One documented MCP profile (display name, console script, import path).
# Measurement: Collected tools plus the measured tools/list payload for one profile.
# render_type: Render a JSON-schema type, including arrays, enums and nullable unions.
# render_parameters: Render a tool's input schema as "name: type = default" clauses.
# first_paragraph: Take the first description paragraph and make it table-safe.
# render_docs: Render the complete docs/TOOLS.md text.
# render_readme: Replace both README cost blocks with measured figures.
# render_outputs: Collect measurements and render both output files under a root.
# check_outputs: Compare both files on disk with a fresh in-memory generation.
# END_MODULE_MAP

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp_wire

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_PATH = Path("docs/TOOLS.md")
README_PATH = Path("README.md")
DSH_README_PATH = Path("dsh") / "README.md"

STALE_DOCS = "docs/TOOLS.md is stale; run scripts/gen_tools_doc.py"
STALE_README = "README.md is stale (tools-cost block); run scripts/gen_tools_doc.py"
STALE_DSH_README = "dsh/README.md is stale (tools-cost-dsh block); run scripts/gen_tools_doc.py"

COST_BLOCK = re.compile(r"<!-- tools-cost:start -->.*?<!-- tools-cost:end -->", re.DOTALL)
DSH_COST_BLOCK = re.compile(r"<!-- tools-cost-dsh:start -->.*?<!-- tools-cost-dsh:end -->", re.DOTALL)


@dataclass(frozen=True)
class Profile:
    """One documented MCP profile."""

    name: str
    script: str
    module: str


@dataclass(frozen=True)
class Measurement:
    """Collected tools and the wire payload a client pays for."""

    profile: Profile
    tools: tuple[dict[str, Any], ...]
    payload: bytes

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    @property
    def token_estimate(self) -> int:
        # The one estimate the repo uses (mcp_wire.py and the dsh bundle gate):
        # a Cyrillic character is ~1/3 token, anything else ~1/4.
        return mcp_wire.wire_tokens([dict(tool) for tool in self.tools])


PROFILES = (
    Profile(name="compare", script="compare-mcp", module="compare_connector.server"),
    Profile(name="decision", script="decision-mcp", module="compare_connector.decision_server"),
    Profile(name="full", script="marketplace-mcp", module="marketplace_connector.server"),
)


# START_BLOCK_COLLECT
def collect_profile(profile: Profile) -> Measurement:
    """Import one profile and measure its public ``list_tools`` surface.

    ``to_mcp_tool`` converts the FastMCP object to the MCP wire schema, including
    the compacted output schema. The payload is the compact, key-sorted JSON the
    documentation reports; sorting makes the byte count stable across platforms.
    """
    module = importlib.import_module(profile.module)
    listed = asyncio.run(module.mcp.list_tools())
    tools = sorted(
        (tool.to_mcp_tool().model_dump(mode="json", by_alias=True, exclude_none=True) for tool in listed),
        key=lambda tool: str(tool["name"]),
    )
    payload = json.dumps(
        {"tools": tools},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Measurement(profile=profile, tools=tuple(tools), payload=payload)


def collect_profiles() -> tuple[Measurement, ...]:
    return tuple(collect_profile(profile) for profile in PROFILES)


def format_tokens(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


# END_BLOCK_COLLECT


# START_BLOCK_RENDER
def format_default(value: object) -> str:
    """Render a JSON-schema default without Python reprs (single quotes)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def render_type(schema: dict[str, Any]) -> str:
    """Render a JSON-schema type, flattening nullable unions and arrays."""
    for union in ("anyOf", "oneOf"):
        variants = schema.get(union)
        if isinstance(variants, list):
            concrete = [
                variant for variant in variants if not (isinstance(variant, dict) and variant.get("type") == "null")
            ]
            if len(concrete) == 1:
                return render_type(concrete[0])
            return " | ".join(render_type(variant) for variant in concrete) or "null"

    enum = schema.get("enum")
    if isinstance(enum, list):
        values = ", ".join(format_default(value) for value in enum)
        kind = schema.get("type", "string")
        return f"{kind} (one of {values})"

    kind = schema.get("type")
    if kind == "array":
        items = schema.get("items")
        return f"array[{render_type(items)}]" if isinstance(items, dict) else "array"
    if isinstance(kind, str):
        return kind
    if "const" in schema:
        return format_default(schema["const"])
    return "any"


def render_parameters(schema: dict[str, Any]) -> str:
    """Render ``name: type`` with ``= default`` for every non-required input."""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return "—"
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()

    parts: list[str] = []
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            definition = {}
        rendered = f"{name}: {render_type(definition)}"
        if name not in required_names:
            if "default" in definition:
                rendered += f" = {format_default(definition['default'])}"
            else:
                rendered += " = null"
        parts.append(rendered)
    return ", ".join(parts).replace("|", "\\|")


def first_paragraph(description: object) -> str:
    """Return the first paragraph, collapsed to one line and safe inside a table."""
    if not isinstance(description, str) or not description.strip():
        return "—"
    paragraph = re.split(r"\n\s*\n", description.strip(), maxsplit=1)[0]
    line = re.sub(r"\s+", " ", " ".join(part.strip() for part in paragraph.splitlines())).strip()
    return line.replace("|", "\\|")


def source_prefix(tool_name: str) -> str:
    return tool_name.partition("_")[0]


def render_profile_table(measurements: tuple[Measurement, ...]) -> list[str]:
    lines = [
        "## Profile cost",
        "",
        "| Profile | Console script | Tools | `tools/list` bytes | Tokens (≈) |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for measurement in measurements:
        profile = measurement.profile
        lines.append(
            f"| {profile.name} | `{profile.script}` | {measurement.tool_count} | "
            f"{len(measurement.payload)} | ≈{format_tokens(measurement.token_estimate)} |"
        )
    return lines


def render_tool_section(measurement: Measurement) -> list[str]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for tool in measurement.tools:
        groups.setdefault(source_prefix(str(tool["name"])), []).append(tool)

    lines = [
        f"## {measurement.profile.name} — `{measurement.profile.script}`",
        "",
        f"{measurement.tool_count} tools, {len(measurement.payload)} `tools/list` bytes "
        f"(≈{format_tokens(measurement.token_estimate)} tokens).",
        "",
    ]
    for prefix in sorted(groups):
        tools = sorted(groups[prefix], key=lambda tool: str(tool["name"]))
        noun = "tool" if len(tools) == 1 else "tools"
        lines.extend(
            [
                f"### {prefix} — `{prefix}_*` ({len(tools)} {noun})",
                "",
                "| Tool | Parameters | Description |",
                "| --- | --- | --- |",
            ]
        )
        for tool in tools:
            parameters = render_parameters(tool.get("inputSchema") or {})
            lines.append(f"| `{tool['name']}` | `{parameters}` | {first_paragraph(tool.get('description'))} |")
        lines.append("")
    return lines


def render_docs(measurements: tuple[Measurement, ...]) -> str:
    """Render the complete generated tool reference."""
    lines = [
        "<!-- Generated by scripts/gen_tools_doc.py. Do not edit by hand. -->",
        "# Tool reference",
        "",
        "This file is generated from the in-process `list_tools()` surface of the three",
        "MCP profiles — the same schemas a client receives after output-schema",
        "compaction. Regenerate it with `uv run python scripts/gen_tools_doc.py`; CI",
        "runs the same command with `--check` and fails when this file or the README",
        "cost block is stale.",
        "",
        "`tools/list` bytes are the UTF-8 length of the compact, key-sorted",
        '`{"tools": [...]}` result with no whitespace. Token counts are the estimate',
        "`scripts/mcp_wire.py` and the dsh bundle gate share: each tool's compact JSON,",
        "a Cyrillic character as 1/3 token and any other character as 1/4.",
        "",
    ]
    lines.extend(render_profile_table(measurements))
    lines.append("")
    for measurement in measurements:
        lines.extend(render_tool_section(measurement))
    return "\n".join(lines).rstrip() + "\n"


def render_cost_block(language: str, measurements: tuple[Measurement, ...]) -> str:
    tokens = {measurement.profile.name: format_tokens(measurement.token_estimate) for measurement in measurements}
    if language == "ru":
        body = (
            "Так сделано потому, что смонтированный сервер платится в каждом запросе:\n"
            f"рекомендуемый режим сравнения цен стоит ≈{tokens['compare']} токенов, режим с инспекцией\n"
            f"карточки — ≈{tokens['decision']}, полный набор — ≈{tokens['full']} (оценка по `tools/list`;\n"
            "подробности в [docs/TOOLS.md](docs/TOOLS.md))."
        )
    else:
        body = (
            "A mounted server is paid on every request — "
            f"≈{tokens['compare']} tokens for the\n"
            "recommended price-comparison profile, "
            f"≈{tokens['decision']} for the decision profile and\n"
            f"≈{tokens['full']} for the full set (estimated from `tools/list`; details in\n"
            "[docs/TOOLS.md](docs/TOOLS.md)) — so opting in is left to you."
        )
    return f"<!-- tools-cost:start -->\n{body}\n<!-- tools-cost:end -->"


def render_readme(readme: str, measurements: tuple[Measurement, ...]) -> str:
    """Rewrite both marked blocks in place: Russian first, then English."""
    matches = COST_BLOCK.findall(readme)
    if len(matches) != 2:
        raise ValueError(f"README.md must contain exactly two tools-cost blocks, found {len(matches)}")
    blocks = iter((render_cost_block("ru", measurements), render_cost_block("en", measurements)))
    return COST_BLOCK.sub(lambda _match: next(blocks), readme)


DSH_MODE_LABELS = {
    "compare": "`compare-mcp` (recommended)",
    "decision": "`decision-mcp` (shortlist inspection)",
    "full": "`marketplace-mcp` (full)",
}


def render_dsh_cost_block(measurements: tuple[Measurement, ...]) -> str:
    """The dsh README mode table; a table cannot hold a comment, so the block holds all of it."""
    lines = [
        "<!-- tools-cost-dsh:start -->",
        "| Mode | Cost while mounted | Model-facing tools |",
        "|---|---|---|",
        "| Skills only (default) | 15 skill catalog entries; no MCP schemas | 0 |",
    ]
    for measurement in measurements:
        label = DSH_MODE_LABELS[measurement.profile.name]
        lines.append(
            f"| {label} | ~{format_tokens(measurement.token_estimate)} tokens per request | {measurement.tool_count} |"
        )
    lines.append("<!-- tools-cost-dsh:end -->")
    return "\n".join(lines)


def render_dsh_readme(readme: str, measurements: tuple[Measurement, ...]) -> str:
    """Rewrite the one marked mode table in dsh/README.md."""
    matches = DSH_COST_BLOCK.findall(readme)
    if len(matches) != 1:
        raise ValueError(f"dsh/README.md must contain exactly one tools-cost-dsh block, found {len(matches)}")
    return DSH_COST_BLOCK.sub(lambda _match: render_dsh_cost_block(measurements), readme)


# END_BLOCK_RENDER


# START_BLOCK_IO
def render_outputs(root: Path) -> dict[Path, str]:
    """Render both generated files for ``root`` without writing anything."""
    measurements = collect_profiles()
    readme = (root / README_PATH).read_text(encoding="utf-8")
    dsh_readme = (root / DSH_README_PATH).read_text(encoding="utf-8")
    return {
        root / DOCS_PATH: render_docs(measurements),
        root / README_PATH: render_readme(readme, measurements),
        root / DSH_README_PATH: render_dsh_readme(dsh_readme, measurements),
    }


def write_outputs(outputs: dict[Path, str]) -> None:
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def check_outputs(root: Path) -> list[str]:
    """Return stale-file messages; an empty list means both files are current."""
    problems: list[str] = []
    expected = render_outputs(root)
    for path, text in expected.items():
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            actual = None
        if actual != text:
            stale = {root / DOCS_PATH: STALE_DOCS, root / README_PATH: STALE_README}
            problems.append(stale.get(path, STALE_DSH_README))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail instead of writing when either file is stale")
    args = parser.parse_args(argv)

    if args.check:
        problems = check_outputs(REPO_ROOT)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0

    outputs = render_outputs(REPO_ROOT)
    write_outputs(outputs)
    for path in outputs:
        print(f"wrote {path.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# END_BLOCK_IO
