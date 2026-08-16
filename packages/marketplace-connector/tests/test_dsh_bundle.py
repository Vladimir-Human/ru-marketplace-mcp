"""Gate for the vendored dsh skills and the dsh bundle layout.

The bundle copies the project skills into ``dsh/skills/``. A copy drifts
silently unless a test owns the comparison, so the vendor and the source are
compared byte-for-byte after normalising CRLF/LF line endings (the same
normalisation a checkout on Windows needs — without it a clean tree with
``core.autocrlf=input`` would produce false failures).

The second gate is about the bundle being inert until dsh activates it: it may
contain only YAML, JSON and Markdown — never an executable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DSH_ROOT = REPO_ROOT / "dsh"
SOURCE_SKILLS = REPO_ROOT / "skills"
VENDOR_SKILLS = DSH_ROOT / "skills"

_ALLOWED_DSH_SUFFIXES = {".md", ".json", ".yml"}


def _normalise(data: bytes) -> bytes:
    """Byte equality with platform line endings folded to LF."""
    return data.replace(b"\r\n", b"\n")


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _file_state(root: Path, relative: str) -> bytes:
    return _normalise((root / relative).read_bytes())


def _tree_diff(source: Path, vendor: Path) -> tuple[list[str], list[str], list[str]]:
    """Return (missing in vendor, extra in vendor, byte-different) paths."""
    source_files = _files(source)
    vendor_files = _files(vendor)

    missing_in_vendor = sorted(source_files - vendor_files)
    extra_in_vendor = sorted(vendor_files - source_files)
    changed = sorted(
        path for path in source_files & vendor_files if _file_state(source, path) != _file_state(vendor, path)
    )
    return missing_in_vendor, extra_in_vendor, changed


def _assert_tree_matches(source: Path, vendor: Path) -> None:
    missing, extra, changed = _tree_diff(source, vendor)
    assert not missing, f"files present in the source skills but absent from dsh/skills: {missing}"
    assert not extra, f"files present in dsh/skills but absent from the source skills: {extra}"
    assert not changed, f"vendored files drifted byte-wise from the source skills: {changed}"


def test_vendored_dsh_skills_match_the_source_skills():
    assert VENDOR_SKILLS.is_dir(), "dsh/skills is missing"
    _assert_tree_matches(SOURCE_SKILLS, VENDOR_SKILLS)


@pytest.mark.parametrize("mutation", ["change", "delete", "extra"])
def test_the_vendor_gate_detects_every_drift_class(tmp_path: Path, mutation: str):
    """A gate that cannot fail is worse than no gate at all."""
    source = tmp_path / "source-skills"
    vendor = tmp_path / "vendor-skills"
    (source / "pkg").mkdir(parents=True)
    (vendor / "pkg").mkdir(parents=True)
    (source / "pkg" / "SKILL.md").write_bytes(b"---\r\nname: pkg\r\n---\r\n")
    (vendor / "pkg" / "SKILL.md").write_bytes(b"---\r\nname: pkg\r\n---\r\n")
    assert _tree_diff(source, vendor) == ([], [], [])

    if mutation == "change":
        (vendor / "pkg" / "SKILL.md").write_bytes(b"---\r\nname: pkg\r\n---\r\nextra")
        assert _tree_diff(source, vendor)[2] == ["pkg/SKILL.md"]
    elif mutation == "delete":
        (vendor / "pkg" / "SKILL.md").unlink()
        assert _tree_diff(source, vendor)[0] == ["pkg/SKILL.md"]
    else:
        (vendor / "pkg" / "EXTRA.md").write_text("extra", encoding="utf-8")
        assert _tree_diff(source, vendor)[1] == ["pkg/EXTRA.md"]


def test_dsh_bundle_contains_only_yaml_json_and_markdown():
    offenders: list[str] = []
    for path in DSH_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _ALLOWED_DSH_SUFFIXES:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"dsh/ may only contain YAML, JSON and Markdown: {offenders}"


def test_dsh_manifest_points_at_the_patch_and_has_no_scoped_name():
    manifest = json.loads((DSH_ROOT / "package.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "ru-marketplace-mcp-dsh"
    assert not manifest["name"].startswith("@")
    patch = DSH_ROOT / manifest["dsh"]["bundle"]["patch"]
    assert patch.is_file(), f"dsh manifest patch path does not exist: {patch}"
    assert "skills" in manifest["files"]
    assert "cordis.patch.yml" in manifest["files"]
