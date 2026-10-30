"""The bump tool proves itself on a copied mini-tree, never on the real repository.

R-RELEASE-BUMP: one declaration table feeds both the check and the bump, the
bump changes only version text, a dry run changes nothing, and a malformed
version is refused with exit code 2.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import bump_version
import check_versions
import version_sites

OLD = "1.2.3"
NEW = "1.3.0"


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture
def mini_tree(tmp_path: Path) -> Path:
    """One file per declaration family, all at OLD."""
    _write(tmp_path, "pyproject.toml", '[project]\nname = "root"\nversion = "1.2.3"\n')
    _write(
        tmp_path,
        "packages/a/pyproject.toml",
        '[project]\nname = "a"\nversion = "1.2.3"\ndependencies = [\n    "mcp-core==1.2.3",\n]\n',
    )
    _write(tmp_path, "packages/mcp-core/pyproject.toml", '[project]\nname = "mcp-core"\nversion = "1.2.3"\n')
    _write(tmp_path, "packages/a/src/a/__init__.py", '__version__ = "1.2.3"\n')
    _write(tmp_path, "packages/a/src/a/server.py", 'SERVER_VERSION = "1.2.3"\n')
    _write(tmp_path, "docker-compose.yml", "services:\n  a:\n    image: ru-marketplace-mcp:1.2.3\n")
    _write(tmp_path, "docs/DEPLOYMENT.md", "docker build -t ru-marketplace-mcp:1.2.3 .\n")
    _write(
        tmp_path,
        "server.json",
        '{\n  "version": "1.2.3",\n  "packages": [\n    {\n'
        '      "identifier": "ghcr.io/x/ru-marketplace-mcp:1.2.3"\n    }\n  ]\n}\n',
    )
    _write(tmp_path, "dsh/package.json", '{\n  "name": "bundle",\n  "version": "1.2.3"\n}\n')
    return tmp_path


def _declared_files(root: Path) -> list[Path]:
    return sorted({declaration.path for declaration in version_sites.iter_declarations(root)})


def test_every_site_family_is_bumped(mini_tree: Path) -> None:
    assert bump_version.bump(mini_tree, NEW) == 0

    for path in _declared_files(mini_tree):
        text = path.read_bytes().decode("utf-8")
        assert OLD not in text, f"{path.name} still carries {OLD}"
        assert NEW in text, f"{path.name} was not bumped"
    assert 'version = "1.3.0"' in (mini_tree / "packages/mcp-core/pyproject.toml").read_text(encoding="utf-8")


def test_dry_run_prints_the_plan_and_writes_nothing(mini_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = {path: path.read_bytes() for path in _declared_files(mini_tree)}

    assert bump_version.bump(mini_tree, NEW, dry_run=True) == 0

    output = capsys.readouterr().out
    assert "pyproject.toml:3 1.2.3 -> 1.3.0" in output
    assert "nothing was written" in output
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("candidate", ["1.2", "1.2.3.4", "release", "v1.2.3-rc1", ""])
def test_a_bad_version_exits_2(mini_tree: Path, candidate: str) -> None:
    before = (mini_tree / "pyproject.toml").read_bytes()
    assert bump_version.main([candidate], root=mini_tree) == 2
    assert (mini_tree / "pyproject.toml").read_bytes() == before


def test_an_optional_v_prefix_is_accepted(mini_tree: Path) -> None:
    assert bump_version.bump(mini_tree, f"v{NEW}") == 0
    assert 'version = "1.3.0"' in (mini_tree / "pyproject.toml").read_text(encoding="utf-8")


def test_formatting_and_json_key_order_are_preserved(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\n# keep this comment\nname = "root"  # and this one\nversion    =  "1.2.3"   # bump only me\n',
    )
    _write(tmp_path, "docker-compose.yml", "services:\n  x:\n    image: ru-marketplace-mcp:1.2.3 # tag\n")
    original_json = (
        "{\n"
        '    "$schema": "x",\n'
        '    "version": "1.2.3",\n'
        '    "packages": [\n'
        "        {\n"
        '            "identifier": "ghcr.io/x/ru-marketplace-mcp:1.2.3",\n'
        '            "version": "0.0.1"\n'
        "        }\n"
        "    ]\n"
        "}\n"
    )
    _write(tmp_path, "server.json", original_json)

    assert bump_version.bump(tmp_path, NEW) == 0

    assert (tmp_path / "pyproject.toml").read_bytes().decode("utf-8") == (
        '[project]\n# keep this comment\nname = "root"  # and this one\nversion    =  "1.3.0"   # bump only me\n'
    )
    assert (tmp_path / "docker-compose.yml").read_bytes().decode("utf-8") == (
        "services:\n  x:\n    image: ru-marketplace-mcp:1.3.0 # tag\n"
    )
    expected_json = original_json.replace('"version": "1.2.3"', '"version": "1.3.0"').replace(
        "ru-marketplace-mcp:1.2.3", "ru-marketplace-mcp:1.3.0"
    )
    assert (tmp_path / "server.json").read_bytes().decode("utf-8") == expected_json
    assert '"version": "0.0.1"' in expected_json, "the nested fixture version proves it was left alone"


def test_the_shared_site_table_is_what_both_scripts_use(
    mini_tree: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bump_version.version_sites is version_sites
    assert check_versions.version_sites is version_sites

    monkeypatch.setattr(version_sites, "build_sites", lambda root: ())

    assert check_versions.main([], root=mini_tree) == 0
    assert "agreed in 0 places" in capsys.readouterr().err
    assert bump_version.bump(mini_tree, NEW) == 1
    assert "no version declarations found" in capsys.readouterr().err
    assert OLD in (mini_tree / "pyproject.toml").read_text(encoding="utf-8")


def test_a_failed_post_write_check_makes_the_bump_fail(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nversion = "1.2.3"\n')
    # A required manifest with no version makes check_versions fail after the
    # write; the bump must surface that failure instead of claiming success.
    _write(tmp_path, "packages/a/pyproject.toml", '[project]\nname = "a"\n')

    assert bump_version.bump(tmp_path, NEW) == 1
    assert 'version = "1.3.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
