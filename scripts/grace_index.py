#!/usr/bin/env python3
"""Generate static GRACE traceability from explicit pytest requirement markers.

START_MODULE_CONTRACT
PURPOSE: Replace hand-maintained test mappings with deterministic AST declarations.
SCOPE: Requirement/rule/invariant registry, test sources and declared manual gaps;
       --write generates index.xml, --check gates unknown IDs, untraced IDs and stale bytes.
       This is static traceability, not execution evidence; evidence flags are a later job.
DEPENDS: Standard-library argparse, ast, dataclasses, pathlib, re, xml.etree.ElementTree.
LINKS: M-GRACE-INDEX, R-GRACE-INDEX.
END_MODULE_CONTRACT
START_MODULE_MAP
  read_registry: Map every declared ID to its requirement or rule parent.
  test_files: Discover package/tests and script tests without tool/cache trees.
  read_tests: Read function, method and class markers without importing tests.
  generate: Compute requirement states, minimal gap references and sorted XML bytes.
  main: Write the index or report each gate failure without changing inputs.
END_MODULE_MAP
"""

from __future__ import annotations

import argparse
import ast
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# START_BLOCK_DECLARATIONS


@dataclass(frozen=True)
class Test:
    file: str
    name: str
    ids: tuple[str, ...]
    title: str | None


@dataclass(frozen=True)
class Generation:
    content: bytes
    states: dict[str, str]
    gaps: dict[str, tuple[str, ...]]
    problems: tuple[str, ...]
    mappings: int
    unmarked_tests: int


def read_registry(path: Path) -> tuple[str, dict[str, str], dict[str, str]]:
    document = ET.parse(path).getroot()
    kinds: dict[str, str] = {}
    parents: dict[str, str] = {}
    for entry in document.iter():
        if entry.tag not in {"requirement", "rule"}:
            continue
        requirement_id = entry.attrib["id"]
        if requirement_id in parents and (
            parents[requirement_id] != requirement_id or kinds.get(requirement_id) != entry.tag
        ):
            raise ValueError(f"conflicting registry ID: {requirement_id}")
        kinds[requirement_id] = entry.tag
        parents[requirement_id] = requirement_id
        for invariant in entry.iter("invariant"):
            invariant_id = invariant.attrib["id"]
            if invariant_id in parents and parents[invariant_id] != requirement_id:
                raise ValueError(f"conflicting registry ID: {invariant_id}")
            parents[invariant_id] = requirement_id
    return document.get("project", ""), kinds, parents


def test_files(root: Path) -> list[Path]:
    paths: list[Path] = []

    def fail_on_unreadable_directory(error: OSError) -> None:
        raise error

    for directory, subdirectories, files in root.walk(on_error=fail_on_unreadable_directory):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if not name.startswith((".", "pytest-cache-files-"))
            and name not in {"node_modules", "venv", "env", "build", "dist", "tmp", "scratch", "__pycache__"}
        )
        relative = directory.relative_to(root)
        if "tests" in relative.parts or relative == Path("scripts"):
            paths.extend(directory / name for name in files if name.startswith("test_") and name.endswith(".py"))
    return sorted(paths)


def marker_ids(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, location: str) -> set[str]:
    ids: set[str] = set()
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "req"
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark"
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "pytest"
        ):
            continue
        if decorator.keywords or not decorator.args:
            raise ValueError(f"{location}: req marker needs positional literal IDs")
        for argument in decorator.args:
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str) or not argument.value:
                raise ValueError(f"{location}: req marker needs nonempty literal string IDs")
            ids.add(argument.value)
    return ids


def read_tests(path: Path, root: Path) -> list[Test]:
    relative = path.relative_to(root).as_posix()
    tree = ast.parse(path.read_bytes(), filename=relative)
    tests: list[Test] = []

    def visit(nodes: list[ast.stmt], prefix: str = "", inherited: frozenset[str] = frozenset()) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                name = f"{prefix}{node.name}::"
                ids = inherited | marker_ids(node, f"{relative}::{name.rstrip(':')}")
                visit(node.body, name, frozenset(ids))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                name = f"{prefix}{node.name}"
                ids = inherited | marker_ids(node, f"{relative}::{name}")
                docstring = ast.get_docstring(node)
                tests.append(Test(relative, name, tuple(sorted(ids)), docstring.splitlines()[0] if docstring else None))

    visit(tree.body)
    return tests


# END_BLOCK_DECLARATIONS

# START_BLOCK_GENERATION


def declared_gaps(path: Path, parents: dict[str, str]) -> dict[str, tuple[str, ...]]:
    gaps: dict[str, set[str]] = {}
    if path.exists():
        for gap in ET.parse(path).getroot().iter("gap"):
            for requirement_id in re.split(r"[\s,]+", gap.get("requirements", "").strip()):
                if requirement_id in parents:
                    gaps.setdefault(parents[requirement_id], set()).add(gap.attrib["id"])
    return {requirement_id: tuple(sorted(ids)) for requirement_id, ids in gaps.items()}


# START_CONTRACT: generate
# PURPOSE: Derive static traceability and all gate diagnostics from current inputs.
# INPUTS: Repository root with registry, optional manual gaps, and test sources.
# OUTPUTS: Deterministic LF XML, states, gap references, diagnostics and counts.
# SIDE_EFFECTS: Read only; never imports tests or writes execution evidence.
def generate(root: Path) -> Generation:
    project, kinds, parents = read_registry(root / ".grace/context/requirements.xml")
    gaps = declared_gaps(root / ".grace/verification/manual.xml", parents)
    by_requirement: dict[str, list[Test]] = {requirement_id: [] for requirement_id in kinds}
    problems: list[str] = []
    mappings = unmarked = 0
    for path in test_files(root):
        try:
            tests = read_tests(path, root)
        except (OSError, SyntaxError, ValueError) as exc:
            problems.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        for test in tests:
            if not test.ids:
                unmarked += 1
            declared: dict[str, list[str]] = {}
            for requirement_id in test.ids:
                if requirement_id not in parents:
                    problems.append(f"{test.file}::{test.name}: unknown ID {requirement_id}")
                    continue
                mappings += 1
                declared.setdefault(parents[requirement_id], []).append(requirement_id)
            for requirement_id, ids in declared.items():
                by_requirement[requirement_id].append(Test(test.file, test.name, tuple(ids), test.title))

    document = ET.Element("verification", {"project": project, "method": "AST pytest.mark.req declarations"})
    document.append(
        ET.Comment(" Generated by scripts/grace_index.py (write mode); static traceability, not execution evidence. ")
    )
    states: dict[str, str] = {}
    for requirement_id in sorted(kinds):
        tests = by_requirement[requirement_id]
        state = "tested" if tests else "gap" if requirement_id in gaps else "untraced"
        states[requirement_id] = state
        attributes = {"id": requirement_id, "kind": kinds[requirement_id], "state": state}
        if requirement_id in gaps:
            attributes["gaps"] = ",".join(gaps[requirement_id])
        entry = ET.SubElement(document, "requirement", attributes)
        for test in sorted(tests, key=lambda test: (test.file, test.name, test.ids)):
            attributes = {"file": test.file, "name": test.name, "requirements": ",".join(test.ids)}
            if test.title is not None:
                attributes["title"] = test.title
            ET.SubElement(entry, "test", attributes)
    ET.indent(document, space="  ")
    content = ET.tostring(document, encoding="utf-8", xml_declaration=True) + b"\n"
    return Generation(content, states, gaps, tuple(sorted(problems)), mappings, unmarked)


# END_BLOCK_GENERATION

# START_BLOCK_CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--write", action="store_true", help="regenerate .grace/verification/index.xml")
    modes.add_argument("--check", action="store_true", help="reject untraced/unknown IDs and stale generated bytes")
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repository root (defaults to this script's repository)"
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    index = root / ".grace/verification/index.xml"
    try:
        result = generate(root)
        problems = list(result.problems)
        if args.write:
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_bytes(result.content)
            print("[M-GRACE-INDEX][BLOCK_CLI] wrote .grace/verification/index.xml")
        else:
            problems.extend(
                f"{key}: untraced (no test or declared gap)"
                for key, state in result.states.items()
                if state == "untraced"
            )
            if not index.exists() or index.read_bytes() != result.content:
                problems.append(".grace/verification/index.xml is stale; run python scripts/grace_index.py --write")
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        print(f"[M-GRACE-INDEX][BLOCK_CLI] error: {exc}")
        return 1
    for requirement_id, state in result.states.items():
        if state != "tested" or requirement_id in result.gaps:
            references = ",".join(result.gaps.get(requirement_id, ())) or "no declared gap"
            print(f"{requirement_id}: {state}; {references}")
    for problem in problems:
        print(f"error: {problem}")
    counts = {state: sum(value == state for value in result.states.values()) for state in ("tested", "gap", "untraced")}
    print(
        f"[M-GRACE-INDEX][BLOCK_CLI] tested={counts['tested']} gap={counts['gap']} untraced={counts['untraced']} "
        f"mappings={result.mappings} unmarked_tests={result.unmarked_tests}"
    )
    return int(bool(problems))


# END_BLOCK_CLI

if __name__ == "__main__":
    raise SystemExit(main())
