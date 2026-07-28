"""Every connector ships its own skill, and that skill describes the real tools.

A connector without a skill is invisible: the agent never learns it exists, so
the server runs and is simply never called. A skill that lists tools the server
does not expose is worse — the agent calls a name that does not resolve, and the
failure looks like the marketplace being down.

Neither condition was checked by anything before 2026-07-28, and both had already
drifted: `skills/dns-connector/SKILL.md` documented the product-URL shape as
`/product/<24-hex>/`, which was the very pattern a previous fix had removed
(the real DNS id is 16 hex). Prose drifts silently; this file makes it fail.

marketplace-connector owns these tests because it is the one package that knows
about every source — it mounts them all.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGES = REPO_ROOT / "packages"
SKILLS = REPO_ROOT / "skills"

# Skill directory names that intentionally differ from their package name.
SKILL_DIR_FOR_PACKAGE = {
    "compare-connector": "compare-prices",
    "marketplace-connector": "marketplace",
}

# mcp-core is the shared runtime, not a marketplace server: no console script,
# no tools, no skill. Anything else without a skill is a bug. Listed rather than
# assumed so the reason is written down, and asserted below so the list cannot
# quietly become wrong.
RUNTIME_ONLY = {"mcp-core"}


def _connector_packages() -> list[str]:
    """Packages that expose a console script — i.e. runnable MCP servers."""
    out = []
    for pyproject in sorted(PACKAGES.glob("*/pyproject.toml")):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        if data.get("project", {}).get("scripts"):
            out.append(pyproject.parent.name)
    return out


def _skill_dir(package: str) -> Path:
    return SKILLS / SKILL_DIR_FOR_PACKAGE.get(package, package)


def _tool_names_in_source(package: str) -> set[str]:
    """Tool names as registered with @mcp.tool(name="...") across the package."""
    names: set[str] = set()
    for path in (PACKAGES / package / "src").rglob("*.py"):
        names.update(re.findall(r'@mcp\.tool\(\s*\n?\s*name="([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    return names


def _frontmatter(skill_md: Path) -> dict[str, str]:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.index("---", 3)
    block = text[3:end]
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


CONNECTORS = _connector_packages()


def test_there_are_connectors_to_check() -> None:
    """Guard the guard: a broken discovery would make every test below vacuous.

    Every check in this file is parametrised over CONNECTORS. If discovery
    silently returned an empty list, the whole suite would pass by doing nothing
    — the failure mode this repo keeps finding elsewhere.
    """
    assert len(CONNECTORS) >= 12, f"only found {CONNECTORS}"


def test_only_the_runtime_packages_are_exempt() -> None:
    """Every package is either a server with a skill, or a listed runtime.

    This is what makes the suite scale: add a fourteenth package and it must
    land in one bucket or the other. A new connector without a skill fails
    `test_every_connector_has_a_skill`; a new runtime library has to be named
    here, deliberately, with a reason.
    """
    all_packages = {path.parent.name for path in PACKAGES.glob("*/pyproject.toml")}
    unaccounted = sorted(all_packages - set(CONNECTORS) - RUNTIME_ONLY)
    assert not unaccounted, (
        f"packages that are neither a server with a console script nor a declared runtime: {unaccounted}. "
        "Give it a console script and a skill, or add it to RUNTIME_ONLY with a reason."
    )
    for runtime in RUNTIME_ONLY:
        assert runtime not in CONNECTORS, f"{runtime} is listed as runtime-only but exposes a console script"


@pytest.mark.parametrize("package", CONNECTORS)
def test_every_connector_has_a_skill(package: str) -> None:
    skill = _skill_dir(package) / "SKILL.md"
    assert skill.is_file(), (
        f"{package} exposes a console script but has no skill at {skill.relative_to(REPO_ROOT)} — "
        "the agent will never learn this connector exists"
    )


# The published Agent Skills frontmatter contract: `name` is 1-64 characters of
# lowercase alphanumerics and hyphens, with no leading, trailing or doubled
# hyphen; `description` is 1-1024 characters. A skill that breaks either may be
# rejected at load time, which reads downstream as "the connector does nothing".
_SKILL_NAME_RE = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)$")
_MAX_DESCRIPTION = 1024
# Not part of the spec, a judgement of our own: a one-line description cannot
# carry both what the source does and when to reach for it.
_MIN_USEFUL_DESCRIPTION = 60


@pytest.mark.parametrize("package", CONNECTORS)
def test_skill_frontmatter_matches_the_published_spec(package: str) -> None:
    """Conformance to the Agent Skills frontmatter contract, not to a house rule."""
    front = _frontmatter(_skill_dir(package) / "SKILL.md")

    name = front.get("name", "")
    assert name, f"{package}: skill has no name in frontmatter"
    assert _SKILL_NAME_RE.match(name), (
        f"{package}: skill name {name!r} breaks the spec — 1-64 chars, lowercase alphanumerics and "
        "hyphens, no leading/trailing/doubled hyphen"
    )

    description = front.get("description", "")
    assert description, f"{package}: skill has no description; it is what the model routes on"
    assert len(description) <= _MAX_DESCRIPTION, (
        f"{package}: description is {len(description)} chars, over the {_MAX_DESCRIPTION} limit"
    )
    assert len(description) >= _MIN_USEFUL_DESCRIPTION, (
        f"{package}: description is too thin to route on: {description!r}"
    )


@pytest.mark.parametrize("package", CONNECTORS)
def test_skill_documents_every_tool_the_server_exposes(package: str) -> None:
    """A tool the skill never mentions is a tool the agent will not reach for."""
    tools = _tool_names_in_source(package)
    if not tools:
        pytest.skip(f"{package} registers no @mcp.tool of its own")
    text = (_skill_dir(package) / "SKILL.md").read_text(encoding="utf-8")
    undocumented = sorted(name for name in tools if name not in text)
    assert not undocumented, f"{package}: tools exposed but absent from its skill: {undocumented}"


@pytest.mark.parametrize("package", CONNECTORS)
def test_skill_invents_no_tools(package: str) -> None:
    """The reverse drift: a skill naming a tool that no longer exists.

    The agent calls the name, nothing resolves, and the failure reads as the
    marketplace being unavailable rather than as stale documentation.
    """
    own_tools = _tool_names_in_source(package)
    if not own_tools:
        pytest.skip(f"{package} registers no @mcp.tool of its own")
    prefix = sorted(own_tools)[0].split("_")[0]
    text = (_skill_dir(package) / "SKILL.md").read_text(encoding="utf-8")
    # Only check names sharing this connector's prefix; skills legitimately
    # cross-reference other connectors' tools (e.g. compare mentions wb_*).
    mentioned = set(re.findall(rf"\b{prefix}_[a-z0-9_]+\b", text))
    ghosts = sorted(name for name in mentioned if name not in own_tools)
    assert not ghosts, f"{package}: skill names tools that do not exist: {ghosts}"


def test_no_skill_still_documents_the_removed_24_hex_url_shape() -> None:
    """Regression for the drift that prompted this file.

    DNS ids are 16 hex (`/product/b7a1667f9b19ed20/`) and Citilink routes are a
    slug ending in digits. The 24-hex MongoDB-ObjectId shape was a bug, and its
    removal must not leave instructions telling operators to use it.
    """
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in SKILLS.rglob("SKILL.md")
        if "24-hex" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"skills still document the removed 24-hex URL shape: {offenders}"


def test_every_mounted_source_maps_to_a_skill() -> None:
    """The unified server's mount list and the skills directory must agree."""
    from marketplace_connector import server

    source = Path(server.__file__).read_text(encoding="utf-8")
    mounted = re.findall(r'\(\s*"([a-z]+)",\s*"([a-z_]+)\.server"\s*\)', source)
    assert mounted, "could not read the mount list — this test needs updating with the source"

    missing = []
    for _name, module_path in mounted:
        package = module_path.replace("_connector", "-connector")
        if not (_skill_dir(package) / "SKILL.md").is_file():
            missing.append(package)
    assert not missing, f"mounted by marketplace-mcp but no skill: {missing}"


def test_the_docker_image_ships_the_skills() -> None:
    """Servers without skills are twelve endpoints nothing knows how to call.

    Two things have to line up: the Dockerfile must copy `skills/`, and
    `.dockerignore` must not swallow the SKILL.md files on the way. The ignore
    file excludes `*.md` at every level, so without an explicit exception the
    COPY lands empty directories in the image — a change that reviews as correct
    and ships nothing.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "skills/" in dockerfile, "Dockerfile does not copy skills/ into the image"

    ignore_path = REPO_ROOT / ".dockerignore"
    if ignore_path.is_file():
        ignore = ignore_path.read_text(encoding="utf-8")
        if "*.md" in ignore:
            assert "!skills/**/SKILL.md" in ignore, (
                ".dockerignore excludes *.md but never re-includes skills/**/SKILL.md — "
                "the image would get empty skill directories"
            )


def test_readme_points_at_the_skills() -> None:
    """A skill nobody is told about is a skill nobody loads."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "skills/" in readme, "README never mentions the skills directory"
