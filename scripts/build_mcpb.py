#!/usr/bin/env python3
"""Build the Claude Desktop bundle (MCPB) of the compare profile.

Run from the repository: python scripts/build_mcpb.py [--out dist]
The bundle holds no code of ours: a manifest, a pyproject that pins the
published ``ru-marketplace-mcp`` package at this version, and a three-line
entry point. The host installs the dependency with uv (MCPB manifest 0.4,
``server.type: "uv"``), so the bundle stays small and cross-platform.
"""

# START_MODULE_CONTRACT
# PURPOSE: Write ru-marketplace-mcp-compare-<version>.mcpb (a zip) for the GitHub Release.
# SCOPE: Root pyproject metadata -> manifest.json, pyproject.toml, src/server.py; zip with fixed timestamps.
# DEPENDS: stdlib argparse, json, tomllib, zipfile, pathlib.
# LINKS: M-MCPB-BUNDLE; R-MCPB-BUNDLE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# bundle_files: The three files of the bundle, derived from the root pyproject.
# build: Write the .mcpb zip (deterministic entry order and timestamps) and return its path.
# main: --out directory; prints the written path.
# END_MODULE_MAP

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_NAME = "ru-marketplace-mcp-compare"
PACKAGE = "ru-marketplace-mcp"
# The compare profile: the 4 comparison tools, the default of `marketplace-mcp install`.
ENTRY_POINT = "src/server.py"
SERVER_PY = """\
from compare_connector.__main__ import main

raise SystemExit(main())
"""
# A fixed zip timestamp keeps the bundle byte-identical for the same version.
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def bundle_files(root: Path = REPO_ROOT) -> dict[str, str]:
    """manifest.json, pyproject.toml and the entry point, as text."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    urls = project.get("urls", {})
    repository = urls.get("Repository", "https://github.com/Vladimir-Human/ru-marketplace-mcp")
    license_name = project.get("license", {})
    manifest = {
        "manifest_version": "0.4",
        "name": BUNDLE_NAME,
        "display_name": "Russian marketplaces: price comparison",
        "version": version,
        "description": (
            "Compare prices for one product across Russian marketplaces (Wildberries, Ozon, Yandex Market, "
            "DNS, Citilink and others) through your own Chrome. Read-only, no keys."
        ),
        "author": {"name": "Vladimir-Human", "url": "https://github.com/Vladimir-Human"},
        "repository": {"type": "git", "url": repository},
        "homepage": urls.get("Homepage", repository),
        "support": urls.get("Issues", repository + "/issues"),
        "license": license_name.get("text", "MIT") if isinstance(license_name, dict) else str(license_name),
        "keywords": ["marketplace", "prices", "wildberries", "ozon", "yandex-market", "russia"],
        # mcp_config is optional for uv in the loose 0.4 schema but required by `mcpb validate`;
        # `uv run` resolves the pinned dependency from pyproject.toml (MCPB hello-world-uv).
        "server": {
            "type": "uv",
            "entry_point": ENTRY_POINT,
            "mcp_config": {"command": "uv", "args": ["run", "--directory", "${__dirname}", ENTRY_POINT]},
        },
        "compatibility": {
            "platforms": ["win32", "darwin", "linux"],
            "runtimes": {"python": project.get("requires-python", ">=3.12")},
        },
    }
    pyproject = (
        "[project]\n"
        f'name = "{BUNDLE_NAME}"\n'
        f'version = "{version}"\n'
        f'requires-python = "{project.get("requires-python", ">=3.12")}"\n'
        f'dependencies = ["{PACKAGE}=={version}"]\n'
    )
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "pyproject.toml": pyproject,
        ENTRY_POINT: SERVER_PY,
    }


def build(out: Path, root: Path = REPO_ROOT) -> Path:
    files = bundle_files(root)
    version = json.loads(files["manifest.json"])["version"]
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{BUNDLE_NAME}-{version}.mcpb"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, files[name].encode("utf-8"))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Claude Desktop bundle (MCPB) of the compare profile.")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "dist", help="output directory (default: dist)")
    args = parser.parse_args(argv)
    sys.stdout.write(f"{build(args.out)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
