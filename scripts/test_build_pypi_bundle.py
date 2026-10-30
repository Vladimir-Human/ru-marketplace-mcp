"""R-PYPI-BUNDLE: independently derived metadata and real offline artifact checks."""
# START_MODULE_CONTRACT
# PURPOSE: Prove bundle derivation, conflict failures, relocatable sources and real wheel contents.
# SCOPE: Offline manifest fixtures and one module-scoped uv build; writes stay under pytest temporary paths.
# DEPENDS: build_pypi_bundle; pytest; packaging; stdlib; synced hatchling backend and uv.
# LINKS: M-PYPI-BUNDLE; R-PYPI-BUNDLE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# checkout: Copy the actual build inputs, without the repository's environment or caches.
# built_bundle: Build exactly once in the module and expose both artifact paths.
# test_generated_metadata_is_the_independent_member_union: Recompute expected PEP 621 data without builder helpers.
# test_conflicting_dependency_specifiers_fail / test_console_script_clashes_fail: Reject broken member inputs.
# test_wheel_contains_every_import_package_and_py_typed / test_wheel_metadata_and_all_console_scripts: Inspect the zip.
# END_MODULE_MAP

from __future__ import annotations

import ast
import configparser
import shutil
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

sys.path.insert(0, str(Path(__file__).parent))
import build_pypi_bundle as bundle

ROOT = Path(__file__).resolve().parents[1]

# START_BLOCK_FIXTURES


@pytest.fixture
def checkout(tmp_path):
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, tmp_path / name)
    for manifest in ROOT.glob("packages/*/pyproject.toml"):
        destination = tmp_path / manifest.relative_to(ROOT)
        destination.parent.mkdir(parents=True)
        shutil.copy2(manifest, destination)
        shutil.copytree(
            manifest.parent / "src", destination.parent / "src", ignore=shutil.ignore_patterns("__pycache__")
        )
    return tmp_path


@pytest.fixture(scope="module")
def built_bundle(tmp_path_factory):
    folder = tmp_path_factory.mktemp("pypi-bundle")
    # uv uses the installed hatchling backend; no cache or index is needed.
    # HOME/TEMP and uv discovery are isolated even on a developer's laptop.
    with pytest.MonkeyPatch.context() as patch:
        for name in ("HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR"):
            patch.setenv(name, str(folder))
        patch.setenv("UV_CACHE_DIR", str(folder / "uv-cache"))
        patch.setenv("XDG_CACHE_HOME", str(folder / "cache"))
        patch.setenv("UV_OFFLINE", "1")
        patch.setenv("UV_NO_BUILD_ISOLATION", "1")
        patch.setenv("UV_NO_CONFIG", "1")
        patch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        patch.delenv("UV_PROJECT", raising=False)
        patch.delenv("UV_WORKING_DIR", raising=False)
        artifacts = bundle.build_bundle(ROOT, folder / "out", temp_dir=folder)
    return artifacts


# END_BLOCK_FIXTURES
# START_BLOCK_DERIVATION


def test_generated_metadata_is_the_independent_member_union():
    root_project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    members = [tomllib.loads(path.read_text(encoding="utf-8")) for path in ROOT.glob("packages/*/pyproject.toml")]
    workspace_names = {canonicalize_name(member["project"]["name"]) for member in members}
    expected_dependencies = set()
    expected_scripts = {}
    for member in members:
        project = member["project"]
        dependencies = list(project.get("dependencies", []))
        dependencies.extend(text for extra in project.get("optional-dependencies", {}).values() for text in extra)
        for text in dependencies:
            requirement = Requirement(text)
            if canonicalize_name(requirement.name) not in workspace_names:
                requirement.name = canonicalize_name(requirement.name)
                expected_dependencies.add(str(requirement))
        assert not expected_scripts.keys() & project.get("scripts", {}).keys()
        expected_scripts.update(project.get("scripts", {}))
    expected_scripts[root_project["name"]] = expected_scripts["marketplace-mcp"]
    generated = tomllib.loads(bundle.render_pyproject())["project"]
    for key in (
        "name",
        "version",
        "description",
        "readme",
        "license",
        "urls",
        "classifiers",
        "requires-python",
        "authors",
        "keywords",
    ):
        assert generated[key] == root_project[key]
    assert set(generated["dependencies"]) == expected_dependencies
    assert len(generated["dependencies"]) == len(expected_dependencies)
    assert generated["scripts"] == expected_scripts
    assert all(canonicalize_name(Requirement(text).name) not in workspace_names for text in generated["dependencies"])


def test_conflicting_dependency_specifiers_fail(checkout):
    manifest = checkout / "packages" / "ozon-connector" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("httpx>=0.27", "httpx<0.27"), encoding="utf-8", newline="\n"
    )
    with pytest.raises(ValueError, match="Conflicting dependency httpx") as error:
        bundle.render_pyproject(checkout)
    assert "httpx>=0.27" in str(error.value)
    assert "httpx<0.27" in str(error.value)
    assert "ozon-connector" in str(error.value)


def test_normalized_package_name_conflicts_fail(checkout):
    manifest = checkout / "packages" / "ozon-connector" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("curl-cffi>=0.8", "curl_cffi<0.8"), encoding="utf-8", newline="\n"
    )
    with pytest.raises(ValueError, match="Conflicting dependency curl-cffi") as error:
        bundle.render_pyproject(checkout)
    assert "curl-cffi>=0.8" in str(error.value)
    assert "curl_cffi<0.8" in str(error.value)


def test_console_script_clashes_fail(checkout):
    manifest = checkout / "packages" / "ozon-connector" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("ozon-mcp =", "wb-mcp ="), encoding="utf-8", newline="\n"
    )
    with pytest.raises(ValueError, match="Console script clash 'wb-mcp'"):
        bundle.render_pyproject(checkout)


def test_bundle_alias_is_reserved(checkout):
    manifest = checkout / "packages" / "ozon-connector" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("ozon-mcp =", "ru-marketplace-mcp ="),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="Console script clash 'ru-marketplace-mcp'"):
        bundle.render_pyproject(checkout)


def test_print_pyproject_builds_and_writes_nothing(checkout, monkeypatch, capsys):
    monkeypatch.setattr(bundle, "REPO_ROOT", checkout)
    original_render = bundle.render_pyproject
    monkeypatch.setattr(bundle, "render_pyproject", lambda: original_render(checkout))

    def no_build(*args, **kwargs):
        pytest.fail("--print-pyproject must not build")

    monkeypatch.setattr(bundle, "build_bundle", no_build)
    out = checkout / "unused-output"
    assert bundle.main(["--print-pyproject", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert tomllib.loads(captured.out)["project"]["name"] == "ru-marketplace-mcp"
    assert not captured.err
    assert not out.exists()
    assert "\r" not in captured.out


# END_BLOCK_DERIVATION
# START_BLOCK_ARTIFACTS


def test_wheel_contains_every_import_package_and_py_typed(built_bundle):
    wheel = next(path for path in built_bundle if path.suffix == ".whl")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for source in ROOT.glob("packages/*/src/*"):
            if not source.is_dir() or not (source / "__init__.py").is_file():
                continue
            assert f"{source.name}/py.typed" in names
            for file in source.rglob("*.py"):
                assert f"{source.name}/{file.relative_to(source).as_posix()}" in names
                assert archive.read(f"{source.name}/{file.relative_to(source).as_posix()}") == file.read_bytes()
        assert "README.md" not in names, "the README is metadata, not a file in site-packages"
        assert not any("__pycache__" in name or name.startswith("packages/") for name in names)
        top_level = {name.split("/", 1)[0] for name in names}
        packages = {source.name for source in ROOT.glob("packages/*/src/*") if (source / "__init__.py").is_file()}
        assert {name for name in top_level if not name.endswith(".dist-info")} == packages


def test_wheel_metadata_and_all_console_scripts(built_bundle):
    expected = tomllib.loads(bundle.render_pyproject())["project"]
    expected_scripts = {}
    member_script_count = 0
    for manifest in ROOT.glob("packages/*/pyproject.toml"):
        scripts = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"].get("scripts", {})
        member_script_count += len(scripts)
        expected_scripts.update(scripts)
    expected_scripts[expected["name"]] = expected_scripts["marketplace-mcp"]
    wheel = next(path for path in built_bundle if path.suffix == ".whl")
    with zipfile.ZipFile(wheel) as archive:
        metadata = BytesParser().parsebytes(
            archive.read(next(name for name in archive.namelist() if name.endswith("/METADATA")))
        )
        assert metadata["Name"] == expected["name"]
        assert metadata["Version"] == expected["version"]
        assert metadata["Requires-Python"] == expected["requires-python"]
        assert metadata["Description-Content-Type"] == "text/markdown"
        first_line = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        assert first_line.encode("utf-8") in archive.read(
            next(name for name in archive.namelist() if name.endswith("/METADATA"))
        )
        assert set(metadata.get_all("Requires-Dist")) == set(expected["dependencies"])
        entry_points = configparser.ConfigParser()
        entry_points.read_string(
            archive.read(next(name for name in archive.namelist() if name.endswith("/entry_points.txt"))).decode()
        )
        assert dict(entry_points["console_scripts"]) == expected_scripts
        # The brief's numeric claim is stale: this checkout has 16 member scripts.
        # Count the actual union plus alias instead of inventing an eighteenth command.
        assert len(entry_points["console_scripts"]) == member_script_count + 1
        assert entry_points["console_scripts"]["ru-marketplace-mcp"] == expected["scripts"]["marketplace-mcp"]


def test_sdist_has_relocatable_project_sources_and_readme(built_bundle):
    sdist = next(path for path in built_bundle if path.name.endswith(".tar.gz"))
    with tarfile.open(sdist) as archive:
        prefix = archive.getnames()[0].split("/", 1)[0]
        project = archive.extractfile(f"{prefix}/pyproject.toml")
        assert project is not None
        metadata = tomllib.loads(project.read().decode("utf-8"))
        for relative in metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]:
            assert not Path(relative).is_absolute()
            if relative.startswith("packages/"):
                assert f"{prefix}/{relative}/py.typed" in archive.getnames()
                assert f"{prefix}/{relative}/__init__.py" in archive.getnames()
            else:
                assert f"{prefix}/{relative}" in archive.getnames()
        assert f"{prefix}/LICENSE" in archive.getnames()
        assert f"{prefix}/README.md" in archive.getnames()


def test_sources_do_not_look_up_workspace_distribution_metadata():
    """Bundled imports cannot depend on 16 separately installed dist-info directories."""
    for source in ROOT.glob("packages/*/src/**/*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name != "importlib.metadata" for alias in node.names), source
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "importlib.metadata", source
                if node.module == "importlib":
                    assert all(alias.name != "metadata" for alias in node.names), source


# END_BLOCK_ARTIFACTS
