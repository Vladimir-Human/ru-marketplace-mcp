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
    _write(
        tmp_path,
        "docs/DEPLOYMENT.md",
        "docker build -t ru-marketplace-mcp:1.2.3 .\n"
        "python -m pip install --no-deps wheelhouse/mcp_core-1.2.3-py3-none-any.whl\n"
        'python -m pip install --find-links wheelhouse "compare-connector[all]==1.2.3" '
        '"aliexpress-connector==1.2.3"\n',
    )
    _write(
        tmp_path,
        "dsh/README.md",
        "# dsh bundle\n\ndocker pull ghcr.io/vladimir-human/ru-marketplace-mcp:1.2.3\n",
    )
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


def test_the_expected_declaration_files_are_pinned_in_this_test(mini_tree: Path) -> None:
    """The table under test must not be allowed to shrink unnoticed (audit S4-F11).

    The expected set is written out here, so dropping a family - or one file of
    a family, like the dsh bundle README - from ``version_sites`` fails this
    test instead of quietly leaving that file on the previous release.
    """
    declared = {path.relative_to(mini_tree).as_posix() for path in _declared_files(mini_tree)}

    assert declared == {
        "docker-compose.yml",
        "docs/DEPLOYMENT.md",
        "dsh/README.md",
        "dsh/package.json",
        "packages/a/pyproject.toml",
        "packages/a/src/a/__init__.py",
        "packages/a/src/a/server.py",
        "packages/mcp-core/pyproject.toml",
        "pyproject.toml",
        "server.json",
    }


def test_the_site_table_names_every_declaration_family(mini_tree: Path) -> None:
    """One family per kind of declaration; the check and the bump share them."""
    labels = {site.label for site in version_sites.build_sites(mini_tree)}

    assert labels == {
        "pyproject.toml",
        "__init__.py",
        "SERVER_VERSION",
        "image tag",
        "deployment release pin",
        "mcp-core pin",
        "server.json",
        "OCI image tag",
        "dsh/package.json",
    }


def test_the_bump_reaches_the_dsh_bundle_readme_image_tag(mini_tree: Path) -> None:
    """The dsh README advertises the image tag; it must not keep the old one."""
    assert bump_version.bump(mini_tree, NEW) == 0

    readme = (mini_tree / "dsh" / "README.md").read_text(encoding="utf-8")
    assert "ru-marketplace-mcp:1.3.0" in readme
    assert OLD not in readme


def test_every_site_family_is_bumped(mini_tree: Path) -> None:
    assert bump_version.bump(mini_tree, NEW) == 0

    for path in _declared_files(mini_tree):
        text = path.read_bytes().decode("utf-8")
        assert OLD not in text, f"{path.name} still carries {OLD}"
        assert NEW in text, f"{path.name} was not bumped"
    assert 'version = "1.3.0"' in (mini_tree / "packages/mcp-core/pyproject.toml").read_text(encoding="utf-8")


def test_the_bump_reaches_the_deployment_guide_release_pins(mini_tree: Path) -> None:
    """The guide installs one release: its wheel name and pins carry the version.

    They were not in the site table, so the bump skipped them and the check
    stayed green over a guide that installed the previous release (audit S3-F4).
    """
    assert bump_version.bump(mini_tree, NEW) == 0

    guide = (mini_tree / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "mcp_core-1.3.0-py3-none-any.whl" in guide
    assert '"compare-connector[all]==1.3.0"' in guide
    assert '"aliexpress-connector==1.3.0"' in guide
    assert OLD not in guide


def test_a_stale_deployment_guide_pin_fails_the_version_check(
    mini_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate must read the guide's pins, not only the manifests (audit S3-F4)."""
    _write(
        mini_tree,
        "docs/DEPLOYMENT.md",
        "docker build -t ru-marketplace-mcp:1.2.3 .\n"
        "python -m pip install --no-deps wheelhouse/mcp_core-1.2.2-py3-none-any.whl\n"
        'python -m pip install --find-links wheelhouse "compare-connector[all]==1.2.3" '
        '"aliexpress-connector==1.2.3"\n',
    )

    assert check_versions.main([], root=mini_tree) == 1

    err = capsys.readouterr().err
    assert "DEPLOYMENT.md:2: 1.2.2 should be 1.2.3" in err


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


def test_the_version_check_fails_on_a_file_that_kept_the_old_version(
    mini_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate exists for exactly this case (audit S4-F10).

    One declaration site that was not rewritten must be reported with the
    version it still carries, and the script must exit non-zero.
    """
    _write(mini_tree, "packages/a/src/a/__init__.py", '__version__ = "1.2.2"\n')

    assert check_versions.main([], root=mini_tree) == 1

    err = capsys.readouterr().err
    assert "version disagreements" in err
    # The path is printed with the platform separator, so only the file name,
    # the line and the two versions are asserted here.
    assert "__init__.py:1: 1.2.2 should be 1.2.3" in err


def test_the_version_check_passes_when_every_declaration_agrees(
    mini_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_versions.main([], root=mini_tree) == 0
    assert "agreed in" in capsys.readouterr().err


def test_the_version_check_refuses_a_version_the_root_does_not_say(
    mini_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`check_versions.py 1.3.0` asserts the root version too (audit S4-F10)."""
    assert check_versions.main([NEW], root=mini_tree) == 1

    err = capsys.readouterr().err
    assert f"root pyproject.toml says {OLD}, you asked for {NEW}" in err

    # The same argument as the root version is accepted.
    assert check_versions.main([OLD], root=mini_tree) == 0
    assert "agreed in" in capsys.readouterr().err
