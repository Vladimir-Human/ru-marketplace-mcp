"""The Claude Desktop bundle (MCPB) of the compare profile.

START_MODULE_CONTRACT
PURPOSE: Prove R-MCPB-BUNDLE: the manifest carries the MCPB 0.4 required fields, the bundle pins the published
         package at the repository version with explicit non-package uv metadata, its entry point starts the
         compare server under uv offline, and the zip is small, deterministic and holds no environment.
SCOPE: scripts/build_mcpb.py offline; unpacked bundle + local wheel build + uv stdio probe in temp dirs; no network.
DEPENDS: pytest; stdlib json, os, shutil, subprocess, sys, sysconfig, time, tomllib, zipfile; build_mcpb;
         build_pypi_bundle; stdio_probe; compare_connector.__main__.
LINKS: M-MCPB-BUNDLE; R-MCPB-BUNDLE; MCPB MANIFEST.md (manifest_version 0.4, server.type "uv").
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import time
import tomllib
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import build_mcpb
import build_pypi_bundle
from stdio_probe import StdioProbe

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
    parsed = tomllib.loads(build_mcpb.bundle_files()["pyproject.toml"])
    project = parsed["project"]

    assert project["dependencies"] == [f"ru-marketplace-mcp=={VERSION}"]
    assert project["version"] == VERSION
    assert parsed["tool"]["uv"] == {"package": False}


@pytest.mark.req("R-MCPB-BUNDLE")
def test_the_entry_point_starts_the_compare_profile_server() -> None:
    from compare_connector.__main__ import main

    source = build_mcpb.bundle_files()["src/server.py"]

    assert "from compare_connector.__main__ import main" in source
    assert callable(main)
    compile(source, "src/server.py", "exec")


@pytest.mark.req("R-MCPB-BUNDLE")
def test_the_unpacked_bundle_declares_non_package_uv_and_starts_cleanly_offline(tmp_path: Path) -> None:
    archive = build_mcpb.build(tmp_path / "dist")
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(unpacked)

    pyproject = tomllib.loads((unpacked / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject.get("tool", {}).get("uv") == {"package": False}
    manifest = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))

    uv = os.environ.get("UV") or shutil.which("uv")
    assert uv is not None, "uv binary is required to verify the MCPB uv server contract"
    host_purelib = sysconfig.get_path("purelib")
    assert host_purelib is not None

    sandbox = tmp_path / "sandbox"
    wheelhouse = tmp_path / "wheelhouse"
    venv_dir = unpacked / ".venv"
    with pytest.MonkeyPatch.context() as patch:
        for name in ("HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR"):
            patch.setenv(name, str(sandbox))
        patch.setenv("UV_CACHE_DIR", str(sandbox / "uv-cache"))
        patch.setenv("XDG_CACHE_HOME", str(sandbox / "cache"))
        patch.setenv("UV_OFFLINE", "1")
        patch.setenv("UV_NO_BUILD_ISOLATION", "1")
        patch.setenv("UV_NO_CONFIG", "1")
        patch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        patch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        patch.setenv("NO_PROXY", "127.0.0.1,localhost")
        for var in ("UV_FROZEN", "UV_LOCKED", "UV_PROJECT", "UV_WORKING_DIR", "VIRTUAL_ENV", "PYTHONPATH"):
            patch.delenv(var, raising=False)

        sandbox.mkdir(parents=True, exist_ok=True)
        build_pypi_bundle.build_bundle(ROOT, wheelhouse, temp_dir=sandbox)
        subprocess.run([uv, "venv", "--offline", "--python", sys.executable, str(venv_dir)], check=True)
        venv_python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--python",
                str(venv_python),
                "--find-links",
                str(wheelhouse),
                f"ru-marketplace-mcp=={VERSION}",
            ],
            check=True,
        )
        venv_purelib = Path(
            subprocess.check_output(
                [str(venv_python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                text=True,
                encoding="utf-8",
            ).strip()
        )
        (venv_purelib / "_third_party_deps.pth").write_text(
            f"import site; site.addsitedir({host_purelib!r})\n",
            encoding="utf-8",
        )
        resolved = Path(
            subprocess.check_output(
                [str(venv_python), "-c", "import compare_connector; print(compare_connector.__file__)"],
                text=True,
                encoding="utf-8",
            ).strip()
        ).resolve()
        assert resolved.is_relative_to(venv_purelib.resolve())

        deadline = time.monotonic() + 30.0
        command = [uv, "run", "--offline", "--no-sync", "--directory", str(unpacked), manifest["server"]["entry_point"]]
        with StdioProbe(command) as session:
            session.initialize("mcpb-offline-test", deadline)
            tools = session.list_tools(deadline)

    assert {tool["name"] for tool in tools} == {
        "compare_prices",
        "compare_verify_offer",
        "compare_sources",
        "compare_browser_snapshot",
    }


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
