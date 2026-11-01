"""The source registry and every site that cannot read it must agree.

``mcp_core.sources`` is the one place "this project ships source X" is written
down, and the tables that could import it now do (the unified mount table's
names, the CLI's per-source config and selfcheck rows, the unblock map, the
compare source list, the stdio end-to-end counts, the CI inventory). A few
sites cannot import Python at all — the workspace manifests, ``server.json``,
and three literals that existing tests parse straight out of their source
(``_mount_all``'s ``mounts``, compare's ``SEARCHABLE``, the diagnose script's
``SOURCES``/``API_SOURCES``).

Those are the sites this file pins. It does not re-check behaviour: it checks
that no list of sources exists anywhere else without a test tying it back to
the registry. Adding a source to a manifest and forgetting the registry (or
the reverse) fails here rather than in an operator's ``marketplace_sources``.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

from marketplace_connector import cli, unblock
from marketplace_connector.capabilities import CAPABILITIES as CLI_CAPABILITIES
from mcp_core.sources import (
    BY_NAME,
    CAPABILITIES,
    CARD_TOOL_NAMES,
    COMPARE_SOURCES,
    CONFIG_SERVERS,
    DRIFT_PROBE_SOURCES,
    FOREIGN_CURRENCY_SOURCES,
    LOGIN_UNBLOCK_SOURCES,
    MOUNTS,
    SELFCHECKS,
    SOURCES,
    UNBLOCK_PAGES,
)

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# PEP 508 dependency names, so a version specifier is stripped before comparing.
_DEPENDENCY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _module_literals(path: Path) -> dict[str, ast.expr]:
    """Module-level assignments by name, for tables existing tests parse with ast."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tables: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            tables[node.target.id] = node.value
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            tables[node.targets[0].id] = node.value
    return tables


def _mounted_literal() -> list[tuple[str, str]]:
    """The mounts table inside marketplace_connector.server._mount_all, by ast."""
    path = PACKAGES / "marketplace-connector" / "src" / "marketplace_connector" / "server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_mount_all"),
        None,
    )
    assert function is not None, "server.py no longer defines _mount_all"
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "mounts" for target in node.targets):
            continue
        if isinstance(node.value, ast.Tuple):
            return [
                (row.elts[0].value, row.elts[1].value)
                for row in node.value.elts
                if isinstance(row, ast.Tuple) and len(row.elts) == 2
            ]
    raise AssertionError("could not read the mounts table out of _mount_all")


def _package_metadata(package: str) -> dict:
    path = PACKAGES / package / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _connector_packages() -> set[str]:
    """Packages that expose a console script — i.e. runnable MCP servers."""
    out = set()
    for pyproject in sorted(PACKAGES.glob("*/pyproject.toml")):
        if _package_metadata(pyproject.parent.name).get("project", {}).get("scripts"):
            out.add(pyproject.parent.name)
    return out


def test_every_runnable_package_has_exactly_one_registry_entry():
    """The registry is not a list of the sources someone remembered."""
    declared = {spec.package for spec in SOURCES}

    assert len(declared) == len(SOURCES), "two sources share one package"
    assert declared == _connector_packages(), (
        "packages and registry disagree: "
        f"only in packages {sorted(_connector_packages() - declared)}, "
        f"only in registry {sorted(declared - _connector_packages())}"
    )


def test_every_entrys_console_script_is_declared_by_its_package():
    for spec in SOURCES:
        scripts = _package_metadata(spec.package)["project"]["scripts"]
        assert spec.console_script in scripts, f"{spec.package} does not declare {spec.console_script}"
        assert (PACKAGES / spec.package / "src" / spec.module.replace(".", "/")).with_suffix(".py").is_file(), spec


def test_the_workspace_manifests_list_every_shipped_package():
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set()
    for entry in root["project"]["dependencies"]:
        match = _DEPENDENCY_NAME.match(entry)
        assert match is not None, f"unreadable dependency entry: {entry!r}"
        dependencies.add(match.group(0))
    uv_sources = root["tool"]["uv"]["sources"]
    testpaths = set(root["tool"]["pytest"]["ini_options"]["testpaths"])
    coverage = set(root["tool"]["coverage"]["run"]["source"])

    for spec in SOURCES:
        assert spec.package in dependencies, spec.package
        assert uv_sources.get(spec.package) == {"workspace": True}, spec.package
        assert f"packages/{spec.package}/tests" in testpaths, spec.package
        module_dir = "packages/" + spec.package + "/src/" + spec.module.rsplit(".", 1)[0]
        assert module_dir in coverage, spec.package


def test_server_json_lists_every_shipped_server_once():
    manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    rows = manifest["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]["servers"]
    by_script = {row["script"]: row for row in rows}

    assert len(by_script) == len(rows), "server.json lists one script twice"
    assert set(by_script) == {spec.console_script for spec in SOURCES}

    for spec in SOURCES:
        row = by_script[spec.console_script]
        assert row["package"] == spec.package, spec.console_script
        assert row["subfolder"] == f"packages/{spec.package}", spec.console_script
        assert row["toolPrefix"] == spec.tool_prefix, spec.console_script


def test_the_ci_inventory_reads_the_registry_instead_of_a_table():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    # The smoke job's tool inventory and console-script check both read
    # mcp_core.sources now; the copies they replaced had already drifted
    # (mpstats sat in one table and not the other).
    assert "from mcp_core.sources import SOURCES" in workflow
    assert "uv run which megamarket-mcp" not in workflow
    assert '"megamarket": 2' not in workflow
    assert "scripts/e2e_stdio_check.py" in workflow


def test_the_literal_mount_table_matches_the_registry():
    """server.py's mounts stay literal: test_dependency_parity.py parses it."""
    assert tuple(_mounted_literal()) == MOUNTS


def test_the_literal_searchable_list_matches_the_registry():
    """compare's SEARCHABLE stays literal: test_distribution_contract.py parses it."""
    path = PACKAGES / "compare-connector" / "src" / "compare_connector" / "server.py"
    searchable = ast.literal_eval(_module_literals(path)["SEARCHABLE"])

    # The set is the registry's text-search comparison sources; the order is
    # compare's own presentation choice (it survives in compare_sources), so it
    # is not pinned here beyond staying duplicate-free.
    expected = {name for name in COMPARE_SOURCES if BY_NAME[name].text_search}
    assert set(searchable) == expected
    assert len(searchable) == len(expected)
    assert "detsky_mir" not in searchable, "Detsky Mir has no text search upstream"


def test_the_diagnose_script_probes_exactly_the_registry_drift_sources():
    """The script's tables stay literal: test_cli.py parses them with ast."""
    literals = _module_literals(ROOT / "scripts" / "diagnose_drift.py")
    probed = frozenset(ast.literal_eval(literals["SOURCES"])) | frozenset(ast.literal_eval(literals["API_SOURCES"]))

    assert probed == DRIFT_PROBE_SOURCES
    assert set(ast.literal_eval(literals["DEFAULT_QUERY"])) == DRIFT_PROBE_SOURCES


def test_the_cli_and_compare_tables_are_the_registry_projections():
    assert list(CONFIG_SERVERS) == cli.SERVERS
    assert list(SELFCHECKS) == cli._SELFCHECKS
    assert cli.DIAGNOSE_DRIFT_SOURCES is DRIFT_PROBE_SOURCES
    assert cli._LOGIN_UNBLOCK_SOURCES is LOGIN_UNBLOCK_SOURCES
    assert unblock.UNBLOCK_PAGES is UNBLOCK_PAGES
    assert CLI_CAPABILITIES is CAPABILITIES

    from compare_connector import server as compare

    assert compare._CARD_TOOL_NAMES == CARD_TOOL_NAMES
    assert compare.FOREIGN_CURRENCY_SOURCES == FOREIGN_CURRENCY_SOURCES
    # MARKETPLACE_SOURCES can only shrink the installed set, never add to it.
    assert set(compare.SOURCES) <= set(COMPARE_SOURCES)
