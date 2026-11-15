"""Requirement-derived CLI checks on planted trees, never the repository registry.

START_MODULE_CONTRACT
PURPOSE: Prove R-GRACE-INDEX marker discovery, grouped states and byte-staleness gates.
SCOPE: Small requirements/manual XML and Python sources below pytest's tmp_path.
DEPENDS: pytest; standard-library pathlib, subprocess, sys, textwrap, xml.etree.
LINKS: R-GRACE-INDEX; M-GRACE-INDEX.
END_MODULE_CONTRACT

START_MODULE_MAP
_write: Plant UTF-8/LF inputs in an isolated tree.
_tree: Plant a requirement registry and separate manual declarations.
_cli: Exercise the generator's public CLI with file-backed output.
_index: Read direct requirement groups from the generated XML.
test_*: Assert the brief's observable behavior independently of implementation.
END_MODULE_MAP
"""

from __future__ import annotations

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
