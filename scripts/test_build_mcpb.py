"""The Claude Desktop bundle (MCPB) of the compare profile.

START_MODULE_CONTRACT
PURPOSE: Prove R-MCPB-BUNDLE: the manifest carries the MCPB 0.4 required fields, the bundle pins the published
         package at the repository version, its entry point starts the compare server, and the zip is small,
         deterministic and holds no environment.
SCOPE: scripts/build_mcpb.py offline; a temp output directory; no network, no uv run.
DEPENDS: pytest; stdlib json, tomllib, zipfile; build_mcpb; compare_connector.__main__.
LINKS: M-MCPB-BUNDLE; R-MCPB-BUNDLE; MCPB MANIFEST.md (manifest_version 0.4, server.type "uv").
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import build_mcpb

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


@pytest.mark.req("R-MCPB-BUNDLE")
def test_the_manifest_has_the_mcpb_0_4_required_fields_for_a_uv_server() -> None:
    manifest = json.loads(build_mcpb.bundle_files()["manifest.json"])

    assert manifest["manifest_version"] == "0.4"
    assert manifest["name"] == "ru-marketplace-mcp-compare"
    assert manifest["version"] == VERSION
    assert manifest["description"] and manifest["author"]["name"]
    assert manifest["server"]["type"] == "uv"
    assert manifest["server"]["entry_point"] == "src/server.py"
    assert manifest["server"]["mcp_config"] == {
        "command": "uv",
        "args": ["run", "--directory", "${__dirname}", "src/server.py"],
    }
    assert set(manifest["compatibility"]["platforms"]) == {"win32", "darwin", "linux"}


@pytest.mark.req("R-MCPB-BUNDLE")
def test_the_bundle_pins_the_published_package_at_this_version() -> None:
    project = tomllib.loads(build_mcpb.bundle_files()["pyproject.toml"])["project"]

    assert project["dependencies"] == [f"ru-marketplace-mcp=={VERSION}"]
    assert project["version"] == VERSION


@pytest.mark.req("R-MCPB-BUNDLE")
def test_the_entry_point_starts_the_compare_profile_server() -> None:
    from compare_connector.__main__ import main

    source = build_mcpb.bundle_files()["src/server.py"]

    assert "from compare_connector.__main__ import main" in source
    assert callable(main)
    compile(source, "src/server.py", "exec")


@pytest.mark.req("R-MCPB-BUNDLE")
def test_the_zip_is_deterministic_and_holds_no_environment(tmp_path: Path) -> None:
    first = build_mcpb.build(tmp_path / "a")
    second = build_mcpb.build(tmp_path / "b")

    assert first.name == f"ru-marketplace-mcp-compare-{VERSION}.mcpb"
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as bundle:
        names = bundle.namelist()
    assert names == ["manifest.json", "pyproject.toml", "src/server.py"]
    assert first.stat().st_size < 4096
