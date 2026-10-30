#!/usr/bin/env python3
"""Build the additional PyPI bundle without changing the uv workspace.

Run from the synced dev environment: python scripts/build_pypi_bundle.py
Use --print-pyproject to inspect derived metadata without writing or building.
"""
# START_MODULE_CONTRACT
# PURPOSE: Derive one publishable distribution from workspace manifests and stage/build its sources locally.
# SCOPE: Root metadata, third-party dependency and console-script unions, import packages, wheel/sdist output.
# DEPENDS: packaging.requirements/utils; stdlib; uv on PATH (or UV); hatchling as the generated build backend.
# LINKS: M-PYPI-BUNDLE; R-PYPI-BUNDLE; R-PYPI-PUBLISH.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# workspace_members: Discover manifests from the root uv workspace patterns, including exclusions.
# bundle_project: Derive metadata, reject ambiguous dependencies/scripts/import destinations and select sources.
# render_pyproject: Serialize the generated project as portable TOML with LF line endings (UTF-8 on stdout).
# build_bundle: Stage selected files, run uv build and copy exactly one wheel and sdist to the output.
# main: Handle --out and the no-build --print-pyproject mode; report failures on stderr.
# END_MODULE_MAP

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[1]

# START_BLOCK_METADATA


def workspace_members(root: Path) -> list[Path]:
    """Resolve the workspace's own member/exclude patterns, not a second list."""
    workspace = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]["workspace"]
    excluded = {path for pattern in workspace.get("exclude", []) for path in root.glob(pattern)}
    members = sorted(
        {
            path / "pyproject.toml"
            for pattern in workspace["members"]
            for path in root.glob(pattern)
            if path not in excluded and (path / "pyproject.toml").is_file()
        }
    )
    if not members:
        raise ValueError("The root uv workspace has no member manifests")
    return members


# START_CONTRACT: bundle_project
# PURPOSE: Derive the full bundle contract without merging incompatible constraints or guessing script owners.
# INPUTS: Checkout root whose member manifests declare dependency/script/wheel-package metadata.
# OUTPUTS: Hatchling project dictionary, with only third-party dependencies and repository-relative source paths.
# SIDE_EFFECTS: Read manifests and check package directories; no writes, builds or imports of connector code.
# END_CONTRACT: bundle_project


def bundle_project(root: Path = REPO_ROOT) -> dict[str, Any]:
    root = root.resolve()
    root_project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    project = copy.deepcopy(
        {
            key: value
            for key, value in root_project.items()
            if key
            in {
                "name",
                "version",
                "description",
                "readme",
                "license",
                "license-files",
                "urls",
                "classifiers",
                "requires-python",
                "authors",
                "maintainers",
                "keywords",
            }
        }
    )
    members = {manifest: tomllib.loads(manifest.read_text(encoding="utf-8")) for manifest in workspace_members(root)}
    workspace_names = {canonicalize_name(member["project"]["name"]) for member in members.values()}
    constraints: dict[str, tuple[Requirement, str, Path]] = {}
    dependencies: set[str] = set()
    scripts: dict[str, str] = {}
    script_owners: dict[str, Path] = {}
    force_include: dict[str, str] = {}
    package_owners: dict[str, Path] = {}

    for manifest, member in members.items():
        metadata = member["project"]
        requirements = list(metadata.get("dependencies", []))
        for extra in metadata.get("optional-dependencies", {}).values():
            requirements.extend(extra)
        for text in requirements:
            requirement = Requirement(text)
            name = canonicalize_name(requirement.name)
            if name in workspace_names:
                continue
            previous = constraints.get(name)
            if previous is not None:
                old, old_text, owner = previous
                if requirement.specifier != old.specifier or requirement.url != old.url:
                    raise ValueError(
                        f"Conflicting dependency {name}: {old_text!r} ({owner.relative_to(root)}) vs "
                        f"{text!r} ({manifest.relative_to(root)}); align member specifiers explicitly"
                    )
            constraints[name] = (requirement, text, manifest)
            requirement.name = name
            requirement.extras = {canonicalize_name(extra) for extra in requirement.extras}
            dependencies.add(str(requirement))
        for name, target in metadata.get("scripts", {}).items():
            if name in scripts or name == project["name"]:
                script_owner = script_owners.get(name)
                raise ValueError(f"Console script clash {name!r}: {script_owner or 'bundle alias'} vs {manifest}")
            scripts[name] = target
            script_owners[name] = manifest
        for package in member["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]:
            source = (manifest.parent / package).resolve()
            relative = source.relative_to(root).as_posix()
            if not source.is_dir() or not (source / "py.typed").is_file():
                raise ValueError(f"Import package or py.typed missing: {relative}")
            destination = source.name
            if destination in package_owners:
                raise ValueError(f"Import package clash {destination!r}: {package_owners[destination]} vs {manifest}")
            force_include[relative] = destination
            package_owners[destination] = manifest

    scripts[project["name"]] = scripts["marketplace-mcp"]
    project["dependencies"] = sorted(dependencies)
    project["scripts"] = dict(sorted(scripts.items()))
    # The README is the long description only: force-including it would drop a
    # bare README.md into the root of site-packages.
    readme_path = _readme_path(project)
    return {
        "build-system": {"requires": ["hatchling"], "build-backend": "hatchling.build"},
        "project": project,
        "tool.hatch.build.targets.wheel": {"bypass-selection": True},
        "tool.hatch.build.targets.wheel.force-include": dict(sorted(force_include.items())),
        "tool.hatch.build.targets.sdist": {"include": ["/pyproject.toml", "/packages", f"/{readme_path}", "/LICENSE"]},
    }


def _readme_path(project: dict[str, Any]) -> str:
    readme = project["readme"]
    return str(readme if isinstance(readme, str) else readme["file"])


# END_BLOCK_METADATA
# START_BLOCK_TOML


def _toml_value(value: Any) -> str:
    """Serialize the strings, lists and inline tables used by PEP 621 metadata."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_value(key)} = {_toml_value(item)}" for key, item in value.items()) + " }"
    raise TypeError(f"Unsupported TOML metadata value: {value!r}")


def render_pyproject(root: Path = REPO_ROOT) -> str:
    sections = []
    for table, values in bundle_project(root).items():
        lines = [f"[{table}]"]
        lines.extend(f"{_toml_value(key)} = {_toml_value(value)}" for key, value in values.items())
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


# END_BLOCK_TOML
# START_BLOCK_BUILD


def build_bundle(root: Path = REPO_ROOT, out: Path | None = None, *, temp_dir: Path | None = None) -> list[Path]:
    """Build local staged sources; temp_dir lets offline tests isolate all writes."""
    root = root.resolve()
    out = out.resolve() if out is not None else root / "dist" / "pypi"
    generated = render_pyproject(root)
    metadata = tomllib.loads(generated)
    sources = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None:
        raise ValueError("uv was not found: install uv or set UV to its binary")
    with tempfile.TemporaryDirectory(prefix="ru-marketplace-pypi-", dir=temp_dir) as folder:
        staging = Path(folder)
        (staging / "pyproject.toml").write_text(generated, encoding="utf-8", newline="\n")
        for relative in [*sources, _readme_path(metadata["project"]), "LICENSE"]:
            source = (root / relative).resolve()
            source.relative_to(root)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
            else:
                shutil.copy2(source, destination)
        sys.stderr.write(
            f"[M-PYPI-BUNDLE][BLOCK_BUILD] building {metadata['project']['name']}=={metadata['project']['version']} "
            f"dependencies={len(metadata['project']['dependencies'])} scripts={len(metadata['project']['scripts'])}\n"
        )
        subprocess.run([uv, "build", "--python", sys.executable, "--out-dir", "dist"], cwd=staging, check=True)
        artifacts = sorted((staging / "dist").glob("*.whl")) + sorted((staging / "dist").glob("*.tar.gz"))
        if len(artifacts) != 2 or sum(path.suffix == ".whl" for path in artifacts) != 1:
            raise ValueError(f"Expected exactly one wheel and one sdist, got {artifacts}")
        out.mkdir(parents=True, exist_ok=True)
        result = [Path(shutil.copy2(artifact, out / artifact.name)) for artifact in artifacts]
    sys.stderr.write(f"[R-PYPI-BUNDLE] ok: copied wheel and sdist to {out}\n")
    return result


# END_BLOCK_BUILD
# START_BLOCK_CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Output directory (default: dist/pypi)")
    parser.add_argument("--print-pyproject", action="store_true", help="Print derived TOML; build and write nothing")
    args = parser.parse_args(argv)
    try:
        if args.print_pyproject:
            # Bytes, not text: a Windows console code page would mangle the
            # description's em dash when the output is redirected to a file.
            sys.stdout.buffer.write(render_pyproject().encode("utf-8"))
            sys.stdout.buffer.flush()
        else:
            build_bundle(out=args.out)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.stderr.write(f"[R-PYPI-BUNDLE] fail: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# END_BLOCK_CLI
