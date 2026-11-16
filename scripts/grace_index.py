#!/usr/bin/env python3
"""Generate static GRACE traceability from explicit pytest requirement markers.

START_MODULE_CONTRACT
PURPOSE: Generate deterministic AST traceability and tree-bound local gate evidence.
SCOPE: --write/--check keep the static index contract; --evidence runs local CI gates
       and writes schema-2 machine facts; --check-evidence checks recorded exits and
       the filtered tracked working-tree digest without running gates or comparing dates.
DEPENDS: Git, local gate tools, version_sites.root_version; standard-library argparse,
         ast, dataclasses, datetime, hashlib, json, os, pathlib, re, subprocess, sys, tempfile, time, xml.
LINKS: M-GRACE-INDEX, R-GRACE-INDEX, R-GRACE-INDEX.evidence.
END_MODULE_CONTRACT
START_MODULE_MAP
  read_registry: Map every declared ID to its requirement or rule parent.
  test_files: Discover package/tests and script tests without tool/cache trees.
  read_tests: Read function, method and class markers without importing tests.
  generate: Compute requirement states, minimal gap references and sorted XML bytes.
  tree_digest: Hash sorted tracked modes, filtered working-tree blobs and raw paths.
  run_gates: Execute the ordered local CI steps and record bounded output summaries.
  write_evidence: Write schema-2 gate results, version, digest and index counts.
  check_evidence: Validate schema, recorded exits and the current digest; show moved paths.
  main: Dispatch index generation/checking or evidence recording/checking.
END_MODULE_MAP
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# START_BLOCK_GATE_CATALOG

# Local lint/test steps in CI order, using the current venv rather than invoking
# uv (which could install tools or dependencies). No PR, live or setup commands.
GATES: dict[str, list[str]] = {
    "Ruff (lint)": [sys.executable, "-m", "ruff", "check", "."],
    "Ruff (format check)": [sys.executable, "-m", "ruff", "format", "--check", "."],
    "mypy (host platform)": [sys.executable, "-m", "mypy"],
    "mypy (cross-platform)": [sys.executable, "-m", "mypy", "--platform", "win32"],
    "mypy (cross-platform): darwin": [sys.executable, "-m", "mypy", "--platform", "darwin"],
    "No stdout writes in connector code": [sys.executable, "scripts/check_no_print.py"],
    "Generated tool reference is current": [sys.executable, "scripts/gen_tools_doc.py", "--check"],
    "Fixture pins describe their fixtures": [sys.executable, "scripts/check_provenance.py"],
    "uv.lock revision is not downgraded": [sys.executable, "scripts/check_lock_revision.py"],
    "MCP wire-cost baseline": [
        sys.executable,
        "scripts/mcp_wire.py",
        "compare-mcp",
        "decision-mcp",
        "marketplace-mcp",
        "--baseline",
        "work/performance/wire-baseline.json",
        "--max-token-regression-percent",
        "10",
    ],
    "One version, everywhere": [sys.executable, "scripts/check_versions.py"],
    "Generated GRACE verification index is current": [sys.executable, "scripts/grace_index.py", "--check"],
    "Architecture structure gate": [sys.executable, "scripts/check_arch.py"],
    "Run offline test suite": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-m",
        "not live and not cdp",
        "--basetemp=.tmp/evidence/offline-suite",
        "-p",
        "no:cacheprovider",
    ],
    "Operational gate regression tests": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "scripts",
        "--basetemp=.tmp/evidence/script-tests",
        "-p",
        "no:cacheprovider",
    ],
}
GATES_SKIPPED: dict[str, str] = {
    "Install uv": "Tool installation requires the network; use the ready local venv.",
    "Set up Python": "Interpreter installation requires the network; use the current interpreter.",
    "Lockfile is up to date": "uv lock --check can resolve registry metadata; no network in this local run.",
    "Sync workspace": "Dependency installation requires the network; use the ready local venv.",
    "CHANGELOG fragment": "Requires pull-request context, changed-file input and PR metadata.",
    "Set up Node for jsdom-backed DOM tests": "Hosted tool setup requires the network; use available local Node.",
    "Install jsdom": "npm ci installs dependencies from the network; use available local jsdom.",
    "Set up Python ${{ matrix.python }}": "Interpreter matrix setup requires the network/other environments.",
}
DIGEST_EXCLUDES = (".grace/evidence/", "changes/")
MISSING_BLOB = b"MISSING"

# END_BLOCK_GATE_CATALOG

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

# START_BLOCK_EVIDENCE


def git_output(root: Path, argv: list[str], data: bytes = b"") -> bytes:
    """Use file-backed stdio, including stdin, without sandbox-denied pipes."""
    stdio_dir = root / ".tmp"
    stdio_dir.mkdir(exist_ok=True)
    with (
        tempfile.TemporaryFile(dir=stdio_dir) as input_stream,
        tempfile.TemporaryFile(dir=stdio_dir) as output_stream,
        tempfile.TemporaryFile(dir=stdio_dir) as error_stream,
    ):
        input_stream.write(data)
        input_stream.seek(0)
        result = subprocess.run(
            ["git", *argv],
            cwd=root,
            stdin=input_stream,
            stdout=output_stream,
            stderr=error_stream,
            check=False,
        )
        if result.returncode:
            error_stream.seek(0)
            raise ValueError(f"git {' '.join(argv)}: {error_stream.read().decode('utf-8', errors='replace').strip()}")
        output_stream.seek(0)
        return output_stream.read()


def quote_git_path(path: bytes) -> bytes:
    """Git --stdin-paths takes newline-delimited, optionally C-quoted paths."""
    escaped = bytearray(b'"')
    for byte in path:
        if byte in (34, 92):
            escaped.extend(b"\\" + bytes([byte]))
        elif byte < 32 or byte == 127:
            escaped.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            escaped.append(byte)
    escaped.extend(b'"\n')
    return bytes(escaped)


# START_CONTRACT: tree_digest
# PURPOSE: Bind evidence to tracked working-tree bytes, not commits or staged blobs.
# INPUTS: Repo root; stage-0 modes/raw paths from git ls-files -s -z.
# OUTPUTS: SHA-256 of sorted <mode> <filtered blob or MISSING> <raw path> LF lines.
# SIDE_EFFECTS: Read files/Git filters; transient stdio files only, never stages files.
def tree_digest(root: Path) -> str:
    entries: list[tuple[bytes, bytes]] = []
    excludes = tuple(prefix.encode("utf-8") for prefix in DIGEST_EXCLUDES)
    for record in git_output(root, ["ls-files", "-s", "-z"]).split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, _index_blob, stage = metadata.split()
        if stage == b"0" and not path.startswith(excludes):
            entries.append((mode, path))
    present = [path for _mode, path in entries if (root / os.fsdecode(path)).exists()]
    blobs: dict[bytes, bytes] = {}
    if present:
        hashes = git_output(root, ["hash-object", "--stdin-paths"], b"".join(map(quote_git_path, present))).splitlines()
        if len(hashes) != len(present):
            raise ValueError("git hash-object returned the wrong number of working-tree blobs")
        blobs = dict(zip(present, hashes, strict=True))
    lines = [mode + b" " + blobs.get(path, MISSING_BLOB) + b" " + path + b"\n" for mode, path in entries]
    return hashlib.sha256(b"".join(sorted(lines))).hexdigest()


def run_gates(root: Path) -> list[dict[str, object]]:
    stdio_dir = root / ".tmp"
    # pytest creates --basetemp but not its parent; the pytest gates use .tmp/evidence/<gate>.
    (stdio_dir / "evidence").mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for name, cmd in GATES.items():
        started = time.monotonic()
        try:
            with tempfile.TemporaryFile(dir=stdio_dir) as stream:
                result = subprocess.run(cmd, cwd=root, stdout=stream, stderr=subprocess.STDOUT, check=False)
                exit_code = result.returncode
                stream.seek(0)
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
                summary = next((line.strip() for line in reversed(lines) if line.strip()), "")[:200]
        except OSError as exc:
            exit_code, summary = 127, str(exc).replace("\n", " ")[:200]
        seconds = round(time.monotonic() - started, 3)
        records.append({"name": name, "cmd": cmd, "exit": exit_code, "summary": summary, "seconds": seconds})
        print(f"[M-GRACE-INDEX][BLOCK_EVIDENCE] {name}: exit={exit_code}; {summary}")
    return records


def write_evidence(root: Path) -> int:
    from version_sites import root_version

    gates = run_gates(root)
    result = generate(root)
    evidence = {
        "schema": 2,
        "version": root_version(root),
        "tree_digest": tree_digest(root),
        "digest_excludes": list(DIGEST_EXCLUDES),
        "gates": gates,
        "index": {
            state: sum(value == state for value in result.states.values()) for state in ("tested", "gap", "untraced")
        },
        "checked_on_utc": datetime.now(UTC).isoformat(),
    }
    path = root / ".grace/evidence/latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    print("[M-GRACE-INDEX][BLOCK_EVIDENCE] wrote .grace/evidence/latest.json")
    return int(any(gate["exit"] != 0 for gate in gates))


def check_evidence(root: Path) -> int:
    path = root / ".grace/evidence/latest.json"
    try:
        evidence = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        print(f"error: .grace/evidence/latest.json cannot be read: {exc}")
        return 1
    if not isinstance(evidence, dict):
        print("error: .grace/evidence/latest.json must be a schema-2 object")
        return 1
    problems: list[str] = []
    if evidence.get("schema") != 2:
        problems.append("evidence schema must be 2; run python scripts/grace_index.py --evidence")
    gates = evidence.get("gates")
    if not isinstance(gates, list) or not gates:
        problems.append("evidence gates must be a non-empty list")
    else:
        for number, gate in enumerate(gates, 1):
            if not isinstance(gate, dict):
                problems.append(f"recorded gate {number} is not an object")
            elif type(gate.get("exit")) is not int or gate["exit"] != 0:
                problems.append(f"recorded gate {gate.get('name', number)}: exit={gate.get('exit')}, expected 0")
    mismatch = evidence.get("tree_digest") != tree_digest(root)
    if mismatch:
        problems.append("tree digest mismatch; run gates and regenerate evidence for the checked tree")
    for problem in problems:
        print(f"error: {problem}")
    if mismatch:
        status = git_output(root, ["status", "--short"]).decode("utf-8", errors="replace").splitlines()
        for line in status[:20]:
            print(line)
    if not problems:
        print("[M-GRACE-INDEX][BLOCK_EVIDENCE] schema=2 gates=ok tree_digest=ok")
    return int(bool(problems))


# END_BLOCK_EVIDENCE

# START_BLOCK_CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--write", action="store_true", help="regenerate .grace/verification/index.xml")
    modes.add_argument("--check", action="store_true", help="reject untraced/unknown IDs and stale generated bytes")
    modes.add_argument("--evidence", action="store_true", help="run local gates and write tree-bound schema-2 evidence")
    modes.add_argument(
        "--check-evidence", action="store_true", help="check recorded gate exits and the working-tree digest"
    )
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repository root (defaults to this script's repository)"
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    index = root / ".grace/verification/index.xml"
    try:
        if args.evidence:
            return write_evidence(root)
        if args.check_evidence:
            return check_evidence(root)
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
