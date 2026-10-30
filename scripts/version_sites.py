#!/usr/bin/env python3
"""The one table of places a release version is written down.

``check_versions.py`` fails when those declarations disagree and
``bump_version.py`` rewrites them. Keeping two lists meant a new declaration
could be checked and not bumped (or the reverse) and nothing would notice: the
gate would go green over a half-finished bump. Both scripts read this module
instead.

A declaration is a regex match whose captured group is the version text. The
scan works on the raw file text, preserving line endings, and records the
character span, so the bump rewrites only those bytes: the rest of the line,
the indentation and the JSON key order stay exactly as they were.

START_MODULE_CONTRACT
PURPOSE: Single source of truth for version declaration sites, scanning and rewriting.
SCOPE: Site table, declaration scanning, root-version read, bump planning/applying.
DEPENDS: stdlib json, re, dataclasses, pathlib.
LINKS: M-VERSION-SITES, R-RELEASE-BUMP, R-VERSION.
START_MODULE_MAP
  Site                 one family of declaration files and its pattern
  Declaration          one matched version and its span in a file
  version_pattern      validate NEW as X.Y.Z with an optional leading v
  root_version         read the reference version from the root pyproject.toml
  build_sites          the site table for a repository root
  iter_declarations    every declaration under a root, in table order
  scan                 mismatches, per-family counts and silent files
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# X.Y.Z, optionally prefixed with v. Anything else is not a release version.
VERSION_PATTERN = re.compile(r"^v?(\d+\.\d+\.\d+)$")

PYPROJECT_VERSION = re.compile(r'^version[ \t]*=[ \t]*"([^"]+)"', re.MULTILINE)
DUNDER_VERSION = re.compile(r'^__version__[ \t]*=[ \t]*"([^"]+)"', re.MULTILINE)
SERVER_VERSION = re.compile(r'^SERVER_VERSION[ \t]*=[ \t]*"([^"]+)"', re.MULTILINE)
IMAGE_TAG = re.compile(r"ru-marketplace-mcp:(\d[^\s\"']*)")
# The OCI identifier is a JSON string; the version runs to its closing quote.
OCI_IDENTIFIER = re.compile(r"ru-marketplace-mcp:(\d[^\s\"']*)(?=\")")
# A top-level JSON key. The indentation is captured so a nested "version"
# (for example in a dependency object) is not mistaken for the manifest's own.
JSON_TOP_LEVEL_VERSION = re.compile(r'^(?P<indent>[ \t]*)"version"[ \t]*:[ \t]*"([^"]+)"', re.MULTILINE)
# The `mcp-core==X.Y.Z` pin each connector carries. Inside the workspace it is
# invisible - `uv.sources = { workspace = true }` overrides the constraint - so
# only a release install ever notices it drifting.
CORE_PIN = re.compile(r'^[ \t]*"mcp-core==([^"]+)",', re.MULTILINE)


@dataclass(frozen=True)
class Mismatch:
    """(display path, line number, what was found, what it should be)."""

    path: str
    lineno: int
    found: str
    want: str


@dataclass(frozen=True)
class Site:
    """One family of files that declares the release version.

    ``required`` means every existing file in the family must contain a
    declaration: a deleted ``__version__`` line used to shrink a summary nobody
    read, and the package shipped without one.
    """

    label: str
    pattern: re.Pattern[str]
    paths: tuple[Path, ...]
    required: bool = True
    value_group: int = 1
    top_level_indent: bool = False
    description: str = ""


@dataclass(frozen=True)
class Declaration:
    """One matched version substring, with offsets into the file's raw text."""

    site: Site
    path: Path
    lineno: int
    start: int
    end: int
    old: str


def read_raw(path: Path) -> str:
    """Read UTF-8 without newline translation, so spans and line endings survive."""
    return path.read_bytes().decode("utf-8")


def version_pattern(value: str) -> re.Match[str] | None:
    """Return the X.Y.Z match for a candidate version, or None."""
    return VERSION_PATTERN.fullmatch(value)


def root_version(root: Path) -> str:
    """The reference version from the root ``pyproject.toml``."""
    text = read_raw(root / "pyproject.toml")
    match = PYPROJECT_VERSION.search(text)
    if match is None:
        raise SystemExit("pyproject.toml at the repository root declares no version")
    return match.group(1)


def _sorted(paths: list[Path]) -> tuple[Path, ...]:
    return tuple(sorted(paths))


def build_sites(root: Path) -> tuple[Site, ...]:
    """The site table for one repository root, in reporting order."""
    packages = root / "packages"
    manifests = _sorted([root / "pyproject.toml", *packages.glob("*/pyproject.toml")])
    image_files = [root / "docker-compose.yml", root / "docs" / "DEPLOYMENT.md", root / "dsh" / "README.md"]
    server_manifest = root / "server.json"
    dsh_manifest = root / "dsh" / "package.json"

    return (
        Site("pyproject.toml", PYPROJECT_VERSION, manifests, description="a project manifest version"),
        Site(
            "__init__.py",
            DUNDER_VERSION,
            _sorted(packages.glob("*/src/*/__init__.py")),
            description="a package __version__",
        ),
        Site(
            "SERVER_VERSION",
            SERVER_VERSION,
            _sorted(packages.glob("*/src/*/server.py")),
            description="a server's SERVER_VERSION",
        ),
        Site(
            "image tag",
            IMAGE_TAG,
            tuple(p for p in image_files if p.exists()),
            required=False,
            description="a compose or deployment image tag",
        ),
        Site(
            "mcp-core pin",
            CORE_PIN,
            _sorted(p for p in packages.glob("*/pyproject.toml") if p.parent.name != "mcp-core"),
            description="a connector's mcp-core== pin",
        ),
        Site(
            "server.json",
            JSON_TOP_LEVEL_VERSION,
            (server_manifest,) if server_manifest.exists() else (),
            value_group=2,
            top_level_indent=True,
            description="the MCP registry manifest version",
        ),
        Site(
            "OCI image tag",
            OCI_IDENTIFIER,
            (server_manifest,) if server_manifest.exists() else (),
            value_group=1,
            description="the MCP registry OCI identifier",
        ),
        Site(
            "dsh/package.json",
            JSON_TOP_LEVEL_VERSION,
            (dsh_manifest,) if dsh_manifest.exists() else (),
            value_group=2,
            top_level_indent=True,
            description="the dsh bundle version",
        ),
    )


def _matches(site: Site, text: str) -> list[re.Match[str]]:
    """Matches of one site in one file, honoring the top-level JSON rule."""
    found = list(site.pattern.finditer(text))
    if site.top_level_indent and found:
        # A pretty-printed JSON object's own keys share the smallest indent;
        # anything deeper is a nested object's field. Minified JSON has no
        # indentation, so its single hit is the top-level one.
        smallest = min(len(match.group("indent")) for match in found)
        found = [match for match in found if len(match.group("indent")) == smallest]
    return found


def _declarations_for(site: Site, root: Path) -> list[Declaration]:
    declarations: list[Declaration] = []
    for path in site.paths:
        text = read_raw(path)
        for match in _matches(site, text):
            start, end = match.span(site.value_group)
            declarations.append(
                Declaration(
                    site=site,
                    path=path,
                    lineno=text.count("\n", 0, start) + 1,
                    start=start,
                    end=end,
                    old=match.group(site.value_group),
                )
            )
    return declarations


def iter_declarations(root: Path) -> list[Declaration]:
    """Every declaration under ``root``, in table order."""
    return [declaration for site in build_sites(root) for declaration in _declarations_for(site, root)]


def display_path(path: Path, root: Path) -> str:
    """Repository-relative path for diagnostics."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def scan(root: Path) -> tuple[list[Mismatch], dict[str, int], list[str]]:
    """Compare every declaration with ``root_version(root)``.

    Returns the mismatches, how many declarations each family contributed, and
    the files that declare no version at all. A silent zero stays visible in the
    count instead of passing as clean.
    """
    expected = root_version(root)
    mismatches: list[Mismatch] = []
    counts: dict[str, int] = {}
    silent: list[str] = []

    for site in build_sites(root):
        declarations = _declarations_for(site, root)
        counts[site.label] = len(declarations)
        if site.required:
            declaring = {declaration.path for declaration in declarations}
            silent.extend(display_path(path, root) for path in site.paths if path not in declaring)
        for declaration in declarations:
            if declaration.old != expected:
                mismatches.append(
                    Mismatch(display_path(declaration.path, root), declaration.lineno, declaration.old, expected)
                )

    return mismatches, counts, silent
