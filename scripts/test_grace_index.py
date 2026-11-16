"""Requirement-derived CLI checks on planted trees, never the repository registry.

START_MODULE_CONTRACT
PURPOSE: Prove R-GRACE-INDEX static traceability and R-GRACE-INDEX.evidence tree-bound checks.
SCOPE: Small planted trees and real temporary Git repos; real CI gate-list drift scan.
DEPENDS: pytest, Git; standard-library json, pathlib, re, runpy, subprocess, sys, textwrap, xml.etree.
LINKS: R-GRACE-INDEX, R-GRACE-INDEX.evidence; M-GRACE-INDEX.
END_MODULE_CONTRACT

START_MODULE_MAP
_write: Plant UTF-8/LF inputs in an isolated tree.
_tree: Plant a requirement registry and separate manual declarations.
_cli: Exercise the generator's public CLI with file-backed output.
_index: Read direct requirement groups from the generated XML.
_evidence_tree: Commit a real Git tree with explicit identity and LF configuration.
_evidence_cli: Exercise public evidence modes with only GATES replaced by trivial subprocesses.
test_*: Assert the brief's observable behavior independently of implementation.
END_MODULE_MAP
"""

from __future__ import annotations

import json
import re
import runpy
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("grace_index.py")


# START_BLOCK_PLANTED_TREE


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(textwrap.dedent(content).lstrip("\n").encode("utf-8"))
    return path


def _tree(root: Path, requirements: str, manual: str = "") -> Path:
    _write(root, ".grace/context/requirements.xml", f"<requirements>\n{requirements}\n</requirements>\n")
    _write(root, ".grace/verification/manual.xml", f"<verification>\n{manual}\n</verification>\n")
    return root


def _cli(root: Path, mode: str) -> tuple[int, str]:
    """Real CLI, with no pipes, network, global temp or imports of planted tests."""
    output = root / ".tmp" / "grace-cli.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), mode],
            cwd=root,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    return result.returncode, output.read_text(encoding="utf-8")


def _index(root: Path) -> dict[str, ET.Element]:
    document = ET.parse(root / ".grace/verification/index.xml").getroot()
    assert document.tag == "verification"
    groups = document.findall("requirement")
    assert len(groups) == len(document.findall(".//requirement")), "Requirement groups must be direct children"
    ids = [group.attrib["id"] for group in groups]
    assert len(ids) == len(set(ids)), "A requirement must have exactly one group"
    return dict(zip(ids, groups, strict=True))


def _declared_ids(test: ET.Element) -> set[str]:
    return {value.strip() for value in test.attrib["requirements"].split(",") if value.strip()}


# END_BLOCK_PLANTED_TREE

# START_BLOCK_MARKER_DISCOVERY


@pytest.mark.req("R-GRACE-INDEX")
def test_function_method_and_class_markers_union_without_importing_tests(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        """
        <requirement id="R-Z"><invariant id="R-Z.detail">A precise claim.</invariant></requirement>
        <requirement id="R-A" />
        <requirement id="R-CLASS" />
        <requirement id="R-METHOD" />
        <requirement id="R-UNION" />
        <rule id="R-RULE" />
        """,
    )
    source = "packages/demo/tests/test_markers.py"
    _write(
        root,
        source,
        '''
        import pytest
        raise RuntimeError("The index must parse AST, never import this module")

        @pytest.mark.req("R-Z.detail", "R-A")
        def test_function():
            """First line <&> survives XML escaping.
            R-NOT-AN-ID in this later line is ordinary prose.
            """
            raise AssertionError("Test bodies must never run")

        @pytest.mark.req("R-CLASS", "R-UNION")
        class TestMarkers:
            @pytest.mark.req("R-METHOD", "R-UNION")
            @pytest.mark.req("R-METHOD")
            def test_method(self):
                """Method title."""
                raise AssertionError("Test bodies must never run")

            def test_inherits_class_marker(self):
                raise AssertionError("Test bodies must never run")

        @pytest.mark.req("R-RULE")
        async def test_async_rule():
            raise AssertionError("Test bodies must never run")
        ''',
    )

    code, output = _cli(root, "--write")
    assert code == 0, output
    groups = _index(root)
    assert set(groups) == {"R-A", "R-Z", "R-CLASS", "R-METHOD", "R-UNION", "R-RULE"}
    assert {group.attrib["state"] for group in groups.values()} == {"tested"}
    expected = {
        "R-A": {"test_function"},
        "R-Z": {"test_function"},
        "R-CLASS": {"TestMarkers::test_method", "TestMarkers::test_inherits_class_marker"},
        "R-METHOD": {"TestMarkers::test_method"},
        "R-UNION": {"TestMarkers::test_method", "TestMarkers::test_inherits_class_marker"},
        "R-RULE": {"test_async_rule"},
    }
    for requirement_id, names in expected.items():
        tests = groups[requirement_id].findall("test")
        assert {test.attrib["name"] for test in tests} == names
        assert len(tests) == len(names), "Overlapping class/method markers must not duplicate mappings"
        assert {test.attrib["file"] for test in tests} == {source}
    function = groups["R-Z"].findall("test")[0]
    assert "R-Z.detail" in _declared_ids(function), "Keep the invariant ID instead of replacing it with its parent"
    assert function.attrib["title"] == "First line <&> survives XML escaping."
    method = groups["R-METHOD"].findall("test")[0]
    assert method.attrib["title"] == "Method title."
    code, output = _cli(root, "--check")
    assert code == 0, output


@pytest.mark.req("R-GRACE-INDEX")
def test_discovery_covers_package_and_script_tests_but_not_arbitrary_python(tmp_path: Path) -> None:
    root = _tree(tmp_path, '<requirement id="R-SCOPE" />')
    included = {
        "packages/demo/tests/test_package.py": "test_package",
        "packages/deep/nested/tests/test_nested.py": "test_nested",
        "scripts/test_script.py": "test_script",
    }
    for relative, name in included.items():
        _write(root, relative, f'import pytest\n@pytest.mark.req("R-SCOPE")\ndef {name}():\n    pass\n')
    for relative in (
        "packages/demo/src/demo/test_outside.py",
        "packages/demo/tests/helper.py",
        "scripts/helper.py",
        "docs/test_outside.py",
        ".tmp/gates/tests/test_outside.py",
    ):
        _write(root, relative, 'import pytest\n@pytest.mark.req("R-OUTSIDE")\ndef test_outside():\n    pass\n')

    code, output = _cli(root, "--write")
    assert code == 0, output
    tests = _index(root)["R-SCOPE"].findall("test")
    assert {(test.attrib["file"], test.attrib["name"]) for test in tests} == set(included.items())
    code, output = _cli(root, "--check")
    assert code == 0, output


# END_BLOCK_MARKER_DISCOVERY

# START_BLOCK_GATE_FAILURES


@pytest.mark.req("R-GRACE-INDEX")
def test_each_untraced_requirement_and_rule_is_named_by_check(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        '<requirement id="R-COVERED" /><requirement id="R-MISSING" /><rule id="R-MISSING-RULE" />',
    )
    _write(
        root,
        "packages/demo/tests/test_covered.py",
        'import pytest\n@pytest.mark.req("R-COVERED")\ndef test_covered():\n    pass\n',
    )

    code, output = _cli(root, "--write")
    assert code == 0, output
    groups = _index(root)
    assert groups["R-COVERED"].attrib["state"] == "tested"
    assert groups["R-MISSING"].attrib["state"] == "untraced"
    assert groups["R-MISSING-RULE"].attrib["state"] == "untraced"
    assert not groups["R-MISSING"].findall("test")
    before = (root / ".grace/verification/index.xml").read_bytes()
    code, output = _cli(root, "--check")
    assert code != 0, output
    assert "R-MISSING" in output
    assert "R-MISSING-RULE" in output
    assert (root / ".grace/verification/index.xml").read_bytes() == before, "Check must not repair the index"


@pytest.mark.req("R-GRACE-INDEX")
def test_unknown_requirement_and_invariant_ids_are_all_named(tmp_path: Path) -> None:
    root = _tree(tmp_path, '<requirement id="R-KNOWN" />')
    source = "packages/demo/tests/test_unknown.py"
    _write(root, source, 'import pytest\n@pytest.mark.req("R-KNOWN")\ndef test_unknown():\n    pass\n')
    code, output = _cli(root, "--write")
    assert code == 0, output
    _write(
        root,
        source,
        'import pytest\n@pytest.mark.req("R-KNOWN", "R-UNKNOWN", "R-KNOWN.missing")\ndef test_unknown():\n    pass\n',
    )

    code, output = _cli(root, "--check")
    assert code != 0, output
    assert "R-UNKNOWN" in output
    assert "R-KNOWN.missing" in output


@pytest.mark.req("R-GRACE-INDEX")
def test_docstrings_never_declare_known_or_unknown_ids(tmp_path: Path) -> None:
    root = _tree(tmp_path, '<requirement id="R-TEXT-ONLY" />')
    _write(
        root,
        "packages/demo/tests/test_docstrings.py",
        '''
        """R-TEXT-ONLY and R-UNKNOWN-MODULE are prose, not declarations."""
        def test_prose():
            """R-TEXT-ONLY and R-UNKNOWN-TEST are prose, not declarations."""
            pass
        ''',
    )

    code, output = _cli(root, "--write")
    assert code == 0, output
    group = _index(root)["R-TEXT-ONLY"]
    assert group.attrib["state"] == "untraced"
    assert not group.findall("test")
    code, output = _cli(root, "--check")
    assert code != 0, output
    assert "R-TEXT-ONLY" in output
    assert "R-UNKNOWN-MODULE" not in output
    assert "R-UNKNOWN-TEST" not in output


@pytest.mark.req("R-GRACE-INDEX")
@pytest.mark.parametrize("change", ["extra-byte", "crlf"])
def test_stale_index_bytes_fail_without_rewriting_committed_file(tmp_path: Path, change: str) -> None:
    root = _tree(tmp_path, '<requirement id="R-STABLE" />')
    _write(
        root,
        "scripts/test_stable.py",
        'import pytest\n@pytest.mark.req("R-STABLE")\ndef test_stable():\n    pass\n',
    )
    code, output = _cli(root, "--write")
    assert code == 0, output
    index = root / ".grace/verification/index.xml"
    current = index.read_bytes()
    stale = current + b" " if change == "extra-byte" else current.replace(b"\n", b"\r\n")
    assert stale != current
    index.write_bytes(stale)

    code, output = _cli(root, "--check")
    assert code != 0, output
    assert "index.xml" in output
    assert index.read_bytes() == stale, "Check must report stale bytes, not rewrite them"


# END_BLOCK_GATE_FAILURES

# START_BLOCK_GAPS_AND_DETERMINISM


@pytest.mark.req("R-GRACE-INDEX")
def test_comma_separated_manual_gaps_pass_and_tests_take_precedence(tmp_path: Path) -> None:
    manual = """
    <gap id="G-MANUAL" requirements="R-GAP, R-GAP-RULE, R-TESTED">Not yet proven outside tests.</gap>
    <note>Manual prose is retained in manual.xml, not copied into the generated index.</note>
    """
    root = _tree(
        tmp_path,
        '<requirement id="R-GAP" /><rule id="R-GAP-RULE" /><requirement id="R-TESTED" />',
        manual,
    )
    _write(
        root,
        "scripts/test_tested.py",
        'import pytest\n@pytest.mark.req("R-TESTED")\ndef test_tested():\n    pass\n',
    )
    manual_path = root / ".grace/verification/manual.xml"
    before = manual_path.read_bytes()

    code, output = _cli(root, "--write")
    assert code == 0, output
    groups = _index(root)
    assert {requirement_id: group.attrib["state"] for requirement_id, group in groups.items()} == {
        "R-GAP": "gap",
        "R-GAP-RULE": "gap",
        "R-TESTED": "tested",
    }
    assert not groups["R-GAP"].findall("test")
    assert not groups["R-GAP-RULE"].findall("test")
    generated = ET.parse(root / ".grace/verification/index.xml").getroot()
    assert not generated.findall(".//note")
    assert manual_path.read_bytes() == before
    code, output = _cli(root, "--check")
    assert code == 0, output
    assert manual_path.read_bytes() == before


@pytest.mark.req("R-GRACE-INDEX")
def test_two_writes_are_byte_identical_sorted_and_lf_only(tmp_path: Path) -> None:
    root = _tree(tmp_path, '<requirement id="R-Z" /><requirement id="R-A" />')
    for relative, name in (
        ("scripts/test_z.py", "test_z"),
        ("packages/demo/tests/test_a.py", "test_z"),
        ("packages/demo/tests/test_a.py", "test_a"),
    ):
        path = root / relative
        existing = path.read_text(encoding="utf-8") if path.exists() else "import pytest\n"
        _write(root, relative, existing + f'@pytest.mark.req("R-Z", "R-A")\ndef {name}():\n    pass\n')

    code, output = _cli(root, "--write")
    assert code == 0, output
    index = root / ".grace/verification/index.xml"
    first = index.read_bytes()
    code, output = _cli(root, "--write")
    assert code == 0, output
    assert index.read_bytes() == first
    assert first.endswith(b"\n")
    assert b"\r" not in first
    groups = _index(root)
    assert list(groups) == ["R-A", "R-Z"]
    for group in groups.values():
        keys = [(test.attrib["file"], test.attrib["name"]) for test in group.findall("test")]
        assert len(keys) == 3
        assert keys == sorted(keys)
    code, output = _cli(root, "--check")
    assert code == 0, output


# END_BLOCK_GAPS_AND_DETERMINISM

# START_BLOCK_EVIDENCE


PASS_GATES = {
    "first": [sys.executable, "-c", "print('first ran')"],
    "second": [sys.executable, "-c", "print('x' * 250); print()"],
}


def _git(root: Path, *argv: str) -> str:
    """Real git, with repository-local identity/config and file-backed output."""
    output = root / ".tmp/git.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        result = subprocess.run(
            ["git", *argv], cwd=root, stdout=stream, stderr=subprocess.STDOUT, check=False, timeout=30
        )
    text = output.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, text
    return text.strip()


def _evidence_tree(root: Path) -> Path:
    _tree(root, '<requirement id="R-DEMO" />')
    _write(root, "pyproject.toml", '[project]\nversion = "1.2.3"\n')
    _write(root, "scripts/test_demo.py", 'import pytest\n@pytest.mark.req("R-DEMO")\ndef test_demo():\n    pass\n')
    _write(root, "src/naïve file.txt", "alpha\nbeta\n")
    _write(root, "changes/entry.md", "excluded change\n")
    _write(root, ".grace/evidence/other.json", "{}\n")
    code, output = _cli(root, "--write")
    assert code == 0, output
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Evidence test")
    _git(root, "config", "user.email", "evidence@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", "pyproject.toml", "scripts", "src", "changes", ".grace")
    _git(root, "commit", "-m", "Initial tree")
    return root


def _evidence_cli(root: Path, mode: str, gates: dict[str, list[str]] | None = None) -> tuple[int, str]:
    """Only fake the costly gate list; run the real CLI/digest/git/file operations."""
    runner = (
        "import json,runpy,sys; "
        "from pathlib import Path; "
        "sys.path.insert(0,str(Path(sys.argv[1]).parent)); "
        "ns=runpy.run_path(sys.argv[1]); "
        "ns['main'].__globals__['GATES']=json.loads(sys.argv[4]); "
        "raise SystemExit(ns['main'](['--root',sys.argv[2],sys.argv[3]]))"
    )
    output = root / ".tmp/evidence-cli.log"
    with output.open("wb") as stream:
        result = subprocess.run(
            [sys.executable, "-c", runner, str(SCRIPT), str(root), mode, json.dumps(gates or PASS_GATES)],
            cwd=root,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    return result.returncode, output.read_text(encoding="utf-8", errors="replace")


def _evidence(root: Path) -> dict:
    return json.loads((root / ".grace/evidence/latest.json").read_bytes())


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_evidence_then_check_passes_and_records_only_machine_facts(tmp_path: Path) -> None:
    root = _evidence_tree(tmp_path)
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    path = root / ".grace/evidence/latest.json"
    raw = path.read_bytes()
    evidence = _evidence(root)
    assert set(evidence) == {"schema", "version", "tree_digest", "digest_excludes", "gates", "index", "checked_on_utc"}
    assert evidence["schema"] == 2
    assert evidence["version"] == "1.2.3"
    assert evidence["digest_excludes"] == [".grace/evidence/", "changes/"]
    assert re.fullmatch(r"[0-9a-f]{64}", evidence["tree_digest"])
    assert evidence["index"] == {"tested": 1, "gap": 0, "untraced": 0}
    assert [gate["name"] for gate in evidence["gates"]] == list(PASS_GATES)
    for gate in evidence["gates"]:
        assert set(gate) == {"name", "cmd", "exit", "summary", "seconds"}
        assert gate["cmd"] == PASS_GATES[gate["name"]]
        assert gate["exit"] == 0
        assert gate["seconds"] >= 0
    assert evidence["gates"][0]["summary"] == "first ran"
    assert evidence["gates"][1]["summary"] == "x" * 200
    assert raw == (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode("utf-8")
    # Check must run no gate and must ignore timestamp freshness.
    evidence["checked_on_utc"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    code, output = _evidence_cli(root, "--check-evidence", {"must not run": [sys.executable, "-c", "exit(9)"]})
    assert code == 0, output
    # Missing, malformed and legacy evidence must never count as a checked tree.
    for invalid in ("{", "[]", json.dumps(evidence | {"schema": 1})):
        path.write_text(invalid, encoding="utf-8")
        code, output = _evidence_cli(root, "--check-evidence")
        assert code == 1, output
        assert "error:" in output
    path.unlink()
    code, output = _evidence_cli(root, "--check-evidence")
    assert code == 1, output
    assert "latest.json" in output


@pytest.mark.req("R-GRACE-INDEX.evidence")
@pytest.mark.parametrize("staged", [False, True])
def test_evidence_check_rejects_one_byte_tracked_edit(tmp_path: Path, staged: bool) -> None:
    root = _evidence_tree(tmp_path)
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    source = root / "src/naïve file.txt"
    source.write_bytes(source.read_bytes().replace(b"alpha", b"Alpha"))
    if staged:
        _git(root, "add", "src")
    code, output = _evidence_cli(root, "--check-evidence")
    assert code == 1, output
    assert "digest" in output.lower()
    assert "src/" in output, "Digest mismatches must show git status"


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_evidence_check_rejects_deleted_tracked_file(tmp_path: Path) -> None:
    root = _evidence_tree(tmp_path)
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    (root / "src/naïve file.txt").unlink()
    code, output = _evidence_cli(root, "--check-evidence")
    assert code == 1, output
    assert "digest" in output.lower()


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_evidence_digest_ignores_excluded_edits_and_untracked_files(tmp_path: Path) -> None:
    root = _evidence_tree(tmp_path)
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    original = _evidence(root)["tree_digest"]
    _write(root, "changes/entry.md", "changed\n")
    _write(root, ".grace/evidence/other.json", '{"changed": true}\n')
    _write(root, "src/untracked.txt", "not counted\n")
    code, output = _evidence_cli(root, "--check-evidence")
    assert code == 0, output
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    assert _evidence(root)["tree_digest"] == original


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_branch_evidence_passes_on_main_after_squash_merge(tmp_path: Path) -> None:
    root = _evidence_tree(tmp_path)
    _git(root, "switch", "-c", "feature")
    _write(root, "src/naïve file.txt", "branch tree\n")
    _git(root, "add", "src")
    _git(root, "commit", "-m", "Feature change")
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    _git(root, "add", ".grace/evidence/latest.json")
    _git(root, "commit", "-m", "Record branch evidence")
    branch_commit = _git(root, "rev-parse", "HEAD")
    branch_tree = _git(root, "rev-parse", "HEAD^{tree}")
    _git(root, "switch", "main")
    _git(root, "merge", "--squash", "feature")
    _git(root, "commit", "-m", "Squashed feature")
    assert _git(root, "rev-parse", "HEAD") != branch_commit
    assert _git(root, "rev-parse", "HEAD^{tree}") == branch_tree
    code, output = _evidence_cli(root, "--check-evidence")
    assert code == 0, output


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_gates_find_their_basetemp_parent(tmp_path: Path) -> None:
    """pytest makes --basetemp but not its parent: run_gates must create .tmp/evidence first."""
    root = _evidence_tree(tmp_path)
    probe = "import pathlib,sys; sys.exit(0 if pathlib.Path('.tmp/evidence').is_dir() else 5)"
    code, output = _evidence_cli(root, "--evidence", {"basetemp parent": [sys.executable, "-c", probe]})
    assert code == 0, output
    assert [gate["exit"] for gate in _evidence(root)["gates"]] == [0]


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_failed_gate_is_recorded_and_both_evidence_modes_fail(tmp_path: Path) -> None:
    root = _evidence_tree(tmp_path)
    gates = {"failure": [sys.executable, "-c", "print('deliberate failure'); exit(3)"], **PASS_GATES}
    code, output = _evidence_cli(root, "--evidence", gates)
    assert code == 1, output
    evidence = _evidence(root)
    assert [gate["exit"] for gate in evidence["gates"]] == [3, 0, 0], "Do not stop after a failed gate"
    assert evidence["gates"][0]["summary"] == "deliberate failure"
    code, output = _evidence_cli(root, "--check-evidence")
    assert code == 1, output
    assert "failure" in output and "3" in output


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_evidence_digest_matches_lf_and_crlf_checkouts(tmp_path: Path) -> None:
    root = _evidence_tree(tmp_path)
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    lf_digest = _evidence(root)["tree_digest"]
    _git(root, "config", "core.autocrlf", "true")
    # checkout-index trusts the stat cache and may leave LF bytes; a removed file
    # is always written fresh with the autocrlf conversion.
    (root / "src/naïve file.txt").unlink()
    _git(root, "checkout", "--", "src/naïve file.txt")
    if b"\r\n" not in (root / "src/naïve file.txt").read_bytes():
        pytest.skip("This git environment did not produce a CRLF checkout with core.autocrlf=true")
    code, output = _evidence_cli(root, "--check-evidence")
    assert code == 0, output
    code, output = _evidence_cli(root, "--evidence")
    assert code == 0, output
    assert _evidence(root)["tree_digest"] == lf_digest


@pytest.mark.req("R-GRACE-INDEX.evidence")
def test_evidence_gate_lists_cover_ci_python_script_steps() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    gates = namespace["GATES"]
    skipped = namespace["GATES_SKIPPED"]
    assert not (gates.keys() & skipped.keys())
    assert all(skipped.values()), "Every omitted step needs a reason"
    workflow = (SCRIPT.parent.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    found = set()
    for job in ("lint", "test"):
        match = re.search(rf"^  {job}:\n(.*?)(?=^  \w[^\n]*:|\Z)", workflow, re.M | re.S)
        assert match, f"Missing CI job: {job}"
        steps = re.split(r"^      - ", match.group(1), flags=re.M)
        for step in steps:
            name = re.match(r"name: ([^\n]+)", step)
            if name and re.search(r"\bpython scripts/[\w]+\.py\b", step):
                step_name = name.group(1)
                found.add(step_name)
                assert step_name in gates or step_name in skipped, f"Unclassified CI step: {step_name}"
                if step_name in gates:
                    script = re.search(r"\bpython (scripts/[\w]+\.py)\b", step)
                    assert script and script.group(1) in gates[step_name], f"Wrong script for {step_name}"
    assert "Generated GRACE verification index is current" in found
    assert "CHANGELOG fragment" in found
    # The other direction: a gate whose CI step was removed must leave GATES too.
    script_gates = {name for name, cmd in gates.items() if any(str(part).startswith("scripts/") for part in cmd)}
    assert script_gates <= found, f"GATES entries with no CI step: {sorted(script_gates - found)}"


# END_BLOCK_EVIDENCE
