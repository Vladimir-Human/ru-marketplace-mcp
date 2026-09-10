"""Every source _mount_all mounts must be a dependency this package declares.

The contract in pyproject.toml is "install one thing, get every source", and a
wheel is only honest about that contract if it lists every connector the server
mounts. Twice now it did not. AliExpress shipped mounted in server.py and
absent from dependencies: outside the workspace, a standalone marketplace-mcp
wheel silently lost aliexpress_*, and the only evidence was one line in
marketplace_sources.skipped that nobody reads until they miss a marketplace.
PR #47 repeated the same mistake with cian-connector and survived only because
a maintainer happened to notice in review.

Nothing at runtime can catch this. The defensive import in _mount_all is
designed to turn a missing connector into one skipped source instead of a dead
server, so an undeclared dependency degrades coverage quietly, forever, on
every clean install. This file is where it gets loud instead: it reads the
mounts table straight out of server.py with ast — no import side effects, no
environment to drift with — and demands every distribution named there appear
in [project].dependencies and in [tool.uv.sources] as a workspace member.
Mount a fifteenth source and forget the pyproject rows, and the offline suite
fails here rather than in an operator's bug report.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SERVER_PY = PACKAGE_ROOT / "src" / "marketplace_connector" / "server.py"
PYPROJECT_TOML = PACKAGE_ROOT / "pyproject.toml"

# This package's own distribution. The reverse check skips it so the gate keeps
# working if the manifest ever grows a self-reference.
SELF = "marketplace-connector"

# The known profile packages: connector distributions that legitimately carry
# servers beyond their _mount_all row. compare-connector ships the standalone
# compare-mcp and decision-mcp profile servers (the middle DSH profile)
# alongside the mounted `compare` source, so it is the one dependency that may
# exist for reasons the mounts table does not show. Listed by name so an
# unmounted dependency has to be *declared* here, with a reason — never pass
# silently, which is the exact failure mode this file exists against.
KNOWN_PROFILE_DISTRIBUTIONS = frozenset({"compare-connector"})

# PEP 508 dependency names, so version specifiers (==/>=/<), extras and
# environment markers can be stripped before comparing.
_DEPENDENCY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _pyproject() -> dict:
    with PYPROJECT_TOML.open("rb") as handle:
        return tomllib.load(handle)


def _declared_dependencies() -> set[str]:
    """Bare distribution names from [project].dependencies, specifiers stripped."""
    names: set[str] = set()
    for entry in _pyproject()["project"]["dependencies"]:
        match = _DEPENDENCY_NAME.match(entry)
        assert match is not None, f"unreadable dependency entry in pyproject.toml: {entry!r}"
        names.add(match.group(0))
    return names


def _workspace_sources() -> dict:
    """The [tool.uv.sources] table: distribution name -> source declaration."""
    return _pyproject().get("tool", {}).get("uv", {}).get("sources", {})


def _mounted_distributions() -> list[tuple[str, str]]:
    """(source name, distribution) pairs from the mounts table inside _mount_all.

    Parsed with ast rather than imported: importing server.py mounts fourteen
    servers as a side effect, and an import-based check would measure whatever
    the current environment happens to resolve instead of what the package
    declares. The distribution name comes from the module path's top-level
    package (``wb_connector.server`` -> ``wb-connector``), which is exactly the
    import the defensive guard needs satisfied at install time.
    """
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"), filename=str(SERVER_PY))
    function = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_mount_all"),
        None,
    )
    assert function is not None, "server.py no longer defines _mount_all — update this test with the source"

    pairs: list[tuple[str, str]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "mounts" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Tuple):
            continue
        for row in node.value.elts:
            if not isinstance(row, ast.Tuple) or len(row.elts) != 2:
                continue
            first, second = row.elts
            if not (
                isinstance(first, ast.Constant)
                and isinstance(second, ast.Constant)
                and isinstance(first.value, str)
                and isinstance(second.value, str)
            ):
                continue
            distribution = second.value.split(".")[0].replace("_", "-")
            pairs.append((first.value, distribution))

    assert pairs, "could not read the mounts table out of _mount_all — this test needs updating with the source"
    return pairs


def test_the_mounts_table_was_read() -> None:
    """Guard the guard: a broken extraction would make every check below vacuous.

    If the ast walk silently returned nothing — _mount_all renamed, the table
    reshaped — the parity tests would pass by checking nothing, the failure
    mode this repo keeps finding elsewhere. So the extraction must recognise
    rows it already knows, including the two this file exists because of.
    """
    pairs = _mounted_distributions()
    assert len(pairs) >= 14, f"expected the full mount table, got {pairs}"
    for known in [
        ("wildberries", "wb-connector"),
        ("aliexpress", "aliexpress-connector"),
        ("cian", "cian-connector"),
        ("compare", "compare-connector"),
    ]:
        assert known in pairs, f"extraction lost a known mount row: {known} not in {pairs}"

    # The dependency parser must strip version specifiers: pyproject pins
    # "mcp-core==2.1.0" on purpose (the unpinned PyPI name belongs to an
    # unrelated project), and a parser that kept the pin would compare
    # "mcp-core==2.1.0" against "mcp-core" and fail on a row that is present
    # and correct.
    assert "mcp-core" in _declared_dependencies()


def test_every_mounted_source_is_a_declared_dependency() -> None:
    """The bug this file exists for: registered in _mount_all, forgotten in pyproject.

    A standalone wheel installs exactly what [project].dependencies names.
    Anything mounted but absent there is silently skipped on the operator's
    machine — aliexpress_* was missing from every pip-installed marketplace-mcp
    until the row existed, and only marketplace_sources.skipped knew.
    """
    declared = _declared_dependencies()
    missing = sorted(
        f"{distribution} ({source})"
        for source, distribution in _mounted_distributions()
        if distribution not in declared
    )
    assert not missing, (
        f"mounted by _mount_all but absent from [project].dependencies: {missing} — "
        "a standalone wheel silently drops these sources"
    )


def test_every_mounted_source_is_a_workspace_source() -> None:
    """Declaring the dependency is half the row; [tool.uv.sources] is the other.

    Without ``workspace = true`` uv resolves the sibling connector from PyPI,
    where these names are not ours to take — the lockfile drifts away from
    packages/ the moment anyone runs ``uv lock``.
    """
    sources = _workspace_sources()
    wrong = []
    for source, distribution in _mounted_distributions():
        entry = sources.get(distribution)
        if not isinstance(entry, dict) or entry.get("workspace") is not True:
            wrong.append(f"{distribution} ({source})")
    assert not wrong, (
        f"mounted but not a workspace source in [tool.uv.sources]: {sorted(wrong)} — "
        "uv would resolve them from PyPI instead of packages/"
    )


def test_every_connector_dependency_is_mounted_or_a_known_profile() -> None:
    """The reverse drift: a connector dependency that nothing mounts.

    Not the disaster the forward direction is — the wheel merely carries dead
    weight — but it means the manifest and the mounts table disagree, and a
    disagreement is either deliberate or a mistake; either way it gets written
    down. Every "<x>-connector" dependency except this package itself is either
    a row in _mount_all or a named profile package
    (KNOWN_PROFILE_DISTRIBUTIONS: compare-connector, which ships the compare-mcp
    and decision-mcp profile servers alongside the mounted compare source).
    """
    dependencies = _declared_dependencies()
    mounted = {distribution for _source, distribution in _mounted_distributions()}
    unaccounted = sorted(
        dependency
        for dependency in dependencies
        if dependency.endswith("-connector")
        and dependency != SELF
        and dependency not in mounted
        and dependency not in KNOWN_PROFILE_DISTRIBUTIONS
    )
    assert not unaccounted, (
        f"connector dependencies that nothing mounts: {unaccounted} — "
        "mount them in _mount_all, or name them in KNOWN_PROFILE_DISTRIBUTIONS with a reason"
    )

    # The allow-list must not rot: a profile package nobody depends on anymore
    # is a standing excuse for future unmounted dependencies.
    for profile in KNOWN_PROFILE_DISTRIBUTIONS:
        assert profile in dependencies, (
            f"{profile} is listed as a known profile but is not a dependency — remove it from the allow-list"
        )
