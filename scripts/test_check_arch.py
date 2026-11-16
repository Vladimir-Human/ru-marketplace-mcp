"""Requirement-derived planted-tree tests for the architecture CLI (brief A12).

START_MODULE_CONTRACT
PURPOSE: Prove production import boundaries, physical-line caps, purity and exemption rules.
SCOPE: Plant isolated package trees; invoke the public CLI; mock only report's local git edge.
DEPENDS: pytest; stdlib pathlib,json,re,runpy,subprocess,sys; scripts/check_arch.py at test runtime.
LINKS: M-ARCH-GATE; R-ARCH-GATE.boundary; R-ARCH-GATE.size; R-ARCH-GATE.purity; R-ARCH-GATE.exempt.
START_MODULE_MAP
  _plant / _baseline       create production sources and baseline in a temporary root
  _cli / _run_in_process   exercise CLI arguments without importing the checker at collection
  _assert_failure         require exit 1 and the documented path/rule diagnostic
  test_*                  derive acceptance and rejection cases from brief A12
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import json
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().with_name("check_arch.py")
ROOT = CHECKER.parent.parent

# START_BLOCK_PLANTED_CLI


def _plant(root: Path, module: str, text: str, filename: str = "server.py") -> str:
    package = module.replace("_", "-")
    relative = f"packages/{package}/src/{module}/{filename}"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


def _baseline(root: Path, *, files=None, functions=None, pure=None) -> None:
    path = root / "scripts/arch_baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"files": files or {}, "functions": functions or {}, "pure": pure or []}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _function(lines: int, *, name: str = "giant", prefix: str = "def") -> str:
    return f"{prefix} {name}():\n" + "    pass\n" * (lines - 1)


def _require_checker() -> None:
    assert CHECKER.is_file(), f"Expected missing checker until implementation: {CHECKER}"


def _cli(root: Path, *flags: str, default_root: bool = False) -> subprocess.CompletedProcess:
    _require_checker()
    args = [sys.executable, "-B", str(CHECKER)]
    if not default_root:
        args.extend(["--root", str(root)])
    return subprocess.run(args + list(flags), cwd=root, capture_output=True, text=True, timeout=30, check=False)


def _output(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


def _assert_failure(result: subprocess.CompletedProcess, path: str, rule: str, line: int | None = None) -> str:
    output = _output(result).replace("\\", "/")
    assert result.returncode == 1, output
    location = re.escape(path) + (f":{line}" if line is not None else r"(?::\d+)?")
    assert re.search(rf"(?m)^{location}: {rule}: .+", output), output
    return output


def _run_in_process(root: Path, monkeypatch: pytest.MonkeyPatch, *flags: str) -> int:
    _require_checker()
    monkeypatch.setattr(sys, "argv", [str(CHECKER), "--root", str(root), *flags])
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    try:
        runpy.run_path(str(CHECKER), run_name="__main__")
    except SystemExit as exc:
        return 0 if exc.code is None else exc.code
    return 0


# END_BLOCK_PLANTED_CLI
# START_BLOCK_BOUNDARY


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary")
@pytest.mark.parametrize(
    ("owner", "source", "line"),
    [
        ("wb_connector", "import ozon_connector\n", 1),
        ("wb_connector", "from ozon_connector.server import mcp\n", 1),
        ("wb_connector", "import json, ozon_connector.server\n", 1),
        ("mcp_core", "import wb_connector.server\n", 1),
        ("compare_connector", "from wb_connector import server\n", 1),
        ("marketplace_connector", "import wb_connector\n", 1),
        ("wb_connector", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import ozon_connector\n", 3),
        ("wb_connector", "def load():\n    from ozon_connector import server\n", 2),
        ("wb_connector", "class Client:\n    def load(self):\n        import ozon_connector\n", 3),
    ],
)
def test_cross_connector_imports_fail_even_when_nested_or_type_only(tmp_path, owner, source, line):
    path = _plant(tmp_path, owner, source)
    _baseline(tmp_path)
    _assert_failure(_cli(tmp_path), path, "boundary", line)


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary")
def test_relative_import_crosses_connector_in_a_valid_shared_namespace(tmp_path):
    path = "packages/wb-connector/src/vendor/wb_connector/server.py"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text("from ..ozon_connector import x\n", encoding="utf-8")
    for relative in ("vendor/__init__.py", "vendor/wb_connector/__init__.py", "vendor/ozon_connector/__init__.py"):
        initializer = tmp_path / "packages/wb-connector/src" / relative
        initializer.parent.mkdir(parents=True, exist_ok=True)
        initializer.write_text("x = 1\n", encoding="utf-8")
    _baseline(tmp_path)
    _assert_failure(_cli(tmp_path), path, "boundary", 1)


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary")
@pytest.mark.parametrize("owner", ["wb_connector", "mcp_core", "compare_connector", "marketplace_connector"])
@pytest.mark.parametrize(
    "expression",
    ["__import__('ozon_connector.server')", "importlib.import_module('ozon_connector.server')"],
)
def test_literal_dynamic_connector_imports_are_not_registry_loading(tmp_path, owner, expression):
    path = _plant(tmp_path, owner, f"import importlib\n{expression}\n")
    _baseline(tmp_path)
    _assert_failure(_cli(tmp_path), path, "boundary", 2)


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary")
@pytest.mark.parametrize("owner", ["compare_connector", "marketplace_connector", "wb_connector", "mcp_core"])
def test_registry_backed_importlib_loading_is_allowed_only_for_aggregators(tmp_path, owner):
    path = _plant(
        tmp_path,
        owner,
        "import importlib\nfrom mcp_core.sources import BY_NAME\n"
        "def load(name):\n    return importlib.import_module(BY_NAME[name].module)\n",
    )
    _baseline(tmp_path)
    result = _cli(tmp_path)
    if owner in {"compare_connector", "marketplace_connector"}:
        assert result.returncode == 0, _output(result)
    else:
        _assert_failure(result, path, "boundary", 4)


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary")
def test_registry_import_does_not_license_unrelated_literal_loader(tmp_path):
    path = _plant(
        tmp_path,
        "compare_connector",
        "import importlib\nfrom mcp_core.sources import BY_NAME\n"
        "def load(name):\n    return importlib.import_module('ozon_connector.server')\n",
    )
    _baseline(tmp_path)
    _assert_failure(_cli(tmp_path), path, "boundary", 4)


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary", "R-ARCH-GATE.size", "R-ARCH-GATE.purity")
def test_clean_tree_passes_and_only_production_sources_are_checked(tmp_path):
    _plant(tmp_path, "wb_connector", "import wb_connector.server\nfrom mcp_core import sources\nfrom . import local\n")
    _plant(tmp_path, "mcp_core", "import json\nfrom . import sources\n")
    _plant(tmp_path, "ozon_connector", "import mcp_core\n")
    ignored = "import ozon_connector\n" + "# not production\n" * 1201
    for relative in ("scripts/ignored.py", "packages/wb-connector/tests/test_ignored.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ignored, encoding="utf-8")
    _baseline(tmp_path)
    result = _cli(tmp_path)
    assert result.returncode == 0, _output(result)


# END_BLOCK_BOUNDARY
# START_BLOCK_SIZE


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.size")
@pytest.mark.parametrize("lines", [1200, 1201])
def test_new_file_physical_line_limit(tmp_path, lines):
    path = _plant(tmp_path, "wb_connector", "# physical line\n" * lines)
    _baseline(tmp_path)
    result = _cli(tmp_path)
    if lines == 1200:
        assert result.returncode == 0, _output(result)
    else:
        _assert_failure(result, path, "size")


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.size")
@pytest.mark.parametrize("prefix", ["def", "async def"])
@pytest.mark.parametrize("lines", [150, 151])
def test_new_function_limit_includes_async_and_excludes_decorators(tmp_path, prefix, lines):
    path = _plant(tmp_path, "wb_connector", "@decorate\n" * 30 + _function(lines, prefix=prefix))
    _baseline(tmp_path)
    result = _cli(tmp_path)
    if lines == 150:
        assert result.returncode == 0, _output(result)
    else:
        output = _assert_failure(result, path, "size", 31)
        assert "giant" in output and "151" in output, output


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.size")
@pytest.mark.parametrize("inner_cap", [150, 161])
def test_nested_method_function_uses_recursive_qualified_baseline_key(tmp_path, inner_cap):
    source = "class Outer:\n    async def method(self):\n        def inner():\n" + "            pass\n" * 150
    path = _plant(tmp_path, "wb_connector", source)
    _baseline(tmp_path, functions={f"{path}::Outer.method": 162, f"{path}::Outer.method.inner": inner_cap})
    result = _cli(tmp_path)
    if inner_cap == 161:
        assert result.returncode == 0, _output(result)
    else:
        output = _assert_failure(result, path, "size", 3)
        assert "Outer.method.inner" in output, output


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.size")
@pytest.mark.parametrize("lines", [1250, 1251])
def test_baseline_file_uses_its_cap_instead_of_default(tmp_path, lines):
    path = _plant(tmp_path, "wb_connector", "# line\n" * lines)
    _baseline(tmp_path, files={path: 1250})
    result = _cli(tmp_path)
    if lines == 1250:
        assert result.returncode == 0, _output(result)
    else:
        _assert_failure(result, path, "size")


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.size")
@pytest.mark.parametrize(("kind", "slack"), [("file", 100), ("file", 101), ("function", 20), ("function", 21)])
def test_hand_raised_caps_fail_only_above_slack_limit(tmp_path, kind, slack):
    path = _plant(tmp_path, "wb_connector", _function(150))
    if kind == "file":
        _baseline(tmp_path, files={path: 150 + slack})
        limit = 100
    else:
        _baseline(tmp_path, functions={f"{path}::giant": 150 + slack})
        limit = 20
    result = _cli(tmp_path)
    if slack == limit:
        assert result.returncode == 0, _output(result)
    else:
        _assert_failure(result, path, "slack")


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.size")
@pytest.mark.parametrize("kind", ["file", "function"])
def test_removed_baseline_entries_fail_instead_of_rotting(tmp_path, kind):
    path = _plant(tmp_path, "wb_connector", "present = True\n")
    if kind == "file":
        path = "packages/wb-connector/src/wb_connector/deleted.py"
        _baseline(tmp_path, files={path: 1200})
    else:
        _baseline(tmp_path, functions={f"{path}::removed": 160})
    output = _assert_failure(_cli(tmp_path), path, "missing")
    if kind == "function":
        assert "removed" in output, output


# END_BLOCK_SIZE
# START_BLOCK_PURITY


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.purity")
@pytest.mark.parametrize(
    "source",
    [
        "import asyncio\n",
        "import httpx\n",
        "import fastmcp\n",
        "import subprocess\n",
        "import socket\n",
        "import mcp_core.transport.chrome_cdp\n",
        "import mcp_core.http\n",
        "from mcp_core import http\n",
        "from mcp_core import transport\n",
        "from mcp_core.transport import chrome_cdp\n",
        "def decide():\n    import httpx\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import socket\n",
        "async def decide():\n    await value()\n",
    ],
)
def test_pure_module_rejects_banned_imports_and_await(tmp_path, source):
    path = _plant(tmp_path, "compare_connector", source, "decide.py")
    _baseline(tmp_path, pure=[path])
    _assert_failure(_cli(tmp_path), path, "purity")


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.purity")
def test_pure_module_can_use_safe_core_and_async_def_without_await(tmp_path):
    path = _plant(
        tmp_path,
        "compare_connector",
        "from mcp_core.sources import BY_NAME\nimport json\nasync def decide():\n    return BY_NAME\n",
        "decide.py",
    )
    _baseline(tmp_path, pure=[path])
    result = _cli(tmp_path)
    assert result.returncode == 0, _output(result)


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.purity")
def test_empty_pure_list_does_not_ban_impure_production_modules(tmp_path):
    _plant(tmp_path, "wb_connector", "import asyncio\nasync def work():\n    await asyncio.sleep(0)\n")
    _baseline(tmp_path)
    result = _cli(tmp_path)
    assert result.returncode == 0, _output(result)


# END_BLOCK_PURITY
# START_BLOCK_EXEMPT


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.exempt", "R-ARCH-GATE.size")
@pytest.mark.parametrize("kind", ["new_file", "new_function", "baseline_file", "baseline_function"])
def test_exempt_converts_only_size_exceedance_to_warning(tmp_path, kind):
    text = "# line\n" * 1201 if kind in {"new_file", "baseline_file"} else _function(151)
    path = _plant(tmp_path, "wb_connector", text)
    _baseline(
        tmp_path,
        files={path: 1200} if kind == "baseline_file" else None,
        functions={f"{path}::giant": 150} if kind == "baseline_function" else None,
    )
    _assert_failure(_cli(tmp_path), path, "size")
    result = _cli(tmp_path, "--exempt")
    output = _output(result).replace("\\", "/")
    assert result.returncode == 0, output
    assert re.search(rf"(?im)^warning: {re.escape(path)}(?::\d+)?: size: .+", output), output


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.exempt", "R-ARCH-GATE.boundary", "R-ARCH-GATE.size", "R-ARCH-GATE.purity")
@pytest.mark.parametrize("rule", ["boundary", "purity", "slack", "missing"])
def test_exempt_never_waives_other_rules_even_with_size_failure(tmp_path, rule):
    first = "import ozon_connector\n" if rule == "boundary" else "import asyncio\n"
    path = _plant(tmp_path, "wb_connector", first + "# line\n" * 1200)
    files = {path: 1400} if rule == "slack" else {}
    functions = {f"{path}::removed": 160} if rule == "missing" else {}
    _baseline(tmp_path, files=files, functions=functions, pure=[path] if rule == "purity" else [])
    _assert_failure(_cli(tmp_path, "--exempt"), path, rule)


# END_BLOCK_EXEMPT
# START_BLOCK_REPORT_AND_REAL_TREE


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary", "R-ARCH-GATE.size", "R-ARCH-GATE.purity", "R-ARCH-GATE.exempt")
def test_real_repository_passes_committed_baseline_and_default_root(tmp_path):
    # An unrelated cwd must not silently turn the default check into an empty-tree pass.
    _require_checker()
    assert (ROOT / "scripts/arch_baseline.json").is_file(), "Expected repository baseline alongside checker"
    result = _cli(tmp_path, default_root=True)
    assert result.returncode == 0, _output(result)


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary")
def test_git_is_never_run_without_report(tmp_path, monkeypatch):
    _plant(tmp_path, "wb_connector", "value = 1\n")
    _baseline(tmp_path)

    def forbidden_git(*args, **kwargs):
        pytest.fail(f"Default architecture check must not run git: {args}")

    monkeypatch.setattr(subprocess, "run", forbidden_git)
    assert _run_in_process(tmp_path, monkeypatch) == 0


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.size")
def test_report_counts_sizes_conformance_and_non_release_multi_package_changes(tmp_path, monkeypatch, capsys):
    large_file = _plant(tmp_path, "wb_connector", "# physical line\n" * 1201)
    large_function = _plant(tmp_path, "ozon_connector", _function(151))
    _baseline(tmp_path, files={large_file: 1201}, functions={f"{large_function}::giant": 161})
    conformance = tmp_path / "packages/marketplace-connector/tests/conformance/test_rules.py"
    conformance.parent.mkdir(parents=True, exist_ok=True)
    conformance.write_text("def test_first():\n    pass\nasync def test_second():\n    pass\ndef helper():\n    pass\n")
    unrelated = conformance.parent.parent / "test_other.py"
    unrelated.write_text("def test_not_conformance():\n    pass\n")
    calls = []
    history = (
        "\x1efeature: broad change\n\npackages/a/one.py\npackages/b/two.py\npackages/c/three.py\n"
        "\x1efeature: narrow change\n\npackages/a/one.py\npackages/a/two.py\npackages/b/three.py\nREADME.md\n"
        "\x1erelease: version\n\npackages/a/one.py\npackages/b/two.py\npackages/c/three.py\n"
    )

    def fake_git(command, *args, **kwargs):
        calls.append(command)
        assert command[0] == "git", command
        if "log" in command:
            assert any("v1.2.3" in part for part in command), command
            return subprocess.CompletedProcess(command, 0, history, "")
        assert "describe" in command or "tag" in command, command
        return subprocess.CompletedProcess(command, 0, "v1.2.3\n", "")

    monkeypatch.setattr(subprocess, "run", fake_git)
    assert _run_in_process(tmp_path, monkeypatch, "--report") == 0
    captured = capsys.readouterr()
    output = (captured.out + captured.err).replace("\\", "/")
    assert large_file in output and f"{large_function}::giant" in output, output
    assert "1201" in output and "151" in output, output
    assert re.search(r"(?im)files[^\n]*\b1\b", output), output
    assert re.search(r"(?im)functions[^\n]*\b1\b", output), output
    assert re.search(r"(?im)largest file[^\n]*" + re.escape(large_file), output), output
    assert re.search(r"(?im)largest function[^\n]*" + re.escape(large_function), output), output
    assert re.search(r"(?im)conformance[^\n]*\b2\b", output), output
    assert re.search(r"\b1\s*(?:/|of|out of)\s*2\b", output), output
    assert len(calls) >= 2, "Report must get the newest v* tag and its subsequent local history"


# END_BLOCK_REPORT_AND_REAL_TREE
# START_BLOCK_LOADER_REGRESSIONS


@pytest.mark.req("R-ARCH-GATE", "R-ARCH-GATE.boundary", "R-ARCH-GATE.purity")
@pytest.mark.parametrize(
    ("owner", "source", "rule", "line"),
    [
        (
            "compare_connector",
            "import importlib\nfrom mcp_core.sources import BY_NAME\n"
            "module_path = BY_NAME['ozon'].module\nmodule_path = 'ozon_connector.server'\n"
            "importlib.import_module(module_path)\n",
            "boundary",
            5,
        ),
        (
            "compare_connector",
            "import importlib\nfrom mcp_core.sources_fake import BY_NAME\n"
            "importlib.import_module(BY_NAME['ozon'].module)\n",
            "boundary",
            3,
        ),
        (
            "compare_connector",
            "import importlib\nfrom unrelated import SOURCE\nimportlib.import_module(SOURCE.module)\n",
            "boundary",
            3,
        ),
        ("wb_connector", "import importlib.util\nimportlib.import_module('ozon_connector.server')\n", "boundary", 2),
        ("wb_connector", "import importlib\nimportlib.import_module(name='ozon_connector.server')\n", "boundary", 2),
        ("compare_connector", "import importlib\nimportlib.import_module(name='httpx')\n", "purity", 2),
        (
            "wb_connector",
            "import importlib\ndef unrelated():\n    import json as importlib\n"
            "importlib.import_module('ozon_connector.server')\n",
            "boundary",
            4,
        ),
    ],
)
def test_loader_aliases_keywords_and_registry_provenance_do_not_bypass_rules(tmp_path, owner, source, rule, line):
    path = _plant(tmp_path, owner, source)
    _baseline(tmp_path, pure=[path] if rule == "purity" else [])
    _assert_failure(_cli(tmp_path), path, rule, line)


# END_BLOCK_LOADER_REGRESSIONS
