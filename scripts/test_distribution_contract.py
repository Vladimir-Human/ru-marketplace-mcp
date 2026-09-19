"""Keep standalone-install documentation aligned with package metadata."""

from __future__ import annotations

import ast
import re
import shlex
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def test_compare_exposes_aliexpress_as_a_documented_optional_extra() -> None:
    metadata = tomllib.loads((ROOT / "packages" / "compare-connector" / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    extras = metadata["optional-dependencies"]
    assert extras["aliexpress"] == ["aliexpress-connector"]
    assert "aliexpress-connector" in extras["all"]


def test_deployment_documents_supported_install_commands() -> None:
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    commands = [
        shlex.split(line)
        for block in re.findall(r"```console\n(.*?)```", deployment, re.S)
        for line in block.splitlines()
        if line.strip()
    ]
    core_install = next(command for command in commands if "--no-deps" in command)
    assert core_install[:5] == [".venv/bin/python", "-m", "pip", "install", "--no-deps"]
    core_wheel = Path(core_install[-1])
    assert core_wheel.parent == Path("wheelhouse")
    core_version = re.fullmatch(r"mcp_core-(.+)-py3-none-any\.whl", core_wheel.name)
    assert core_version is not None
    compare_install = next(command for command in commands if "--find-links" in command)
    assert compare_install[:6] == [".venv/bin/python", "-m", "pip", "install", "--find-links", "wheelhouse"]
    requirements = {requirement.name: requirement for requirement in map(Requirement, compare_install[6:])}
    assert set(requirements) == {"compare-connector", "aliexpress-connector"}
    assert requirements["compare-connector"].extras == {"all"}
    assert all(str(requirement.specifier) == f"=={core_version[1]}" for requirement in requirements.values())


def test_entrypoint_examples_launch_their_declared_console_script() -> None:
    for package in ("wb", "yandex", "detmir", "ozon", "compare"):
        package_dir = ROOT / "packages" / f"{package}-connector"
        entrypoint = package_dir / "src" / f"{package}_connector" / "__main__.py"
        docstring = ast.get_docstring(ast.parse(entrypoint.read_text(encoding="utf-8")))
        command = next(shlex.split(line) for line in docstring.splitlines() if line.strip().startswith("uv "))
        assert command[:5] == ["uv", "run", "--frozen", "--directory", "/path/to/ru-marketplace-mcp"]
        assert len(command) == 6
        metadata = tomllib.loads((package_dir / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        assert metadata["scripts"][command[5]] == f"{package}_connector.__main__:main"


def test_all_comparison_sources_have_a_wheelhouse_dependency() -> None:
    source = (ROOT / "packages" / "compare-connector" / "src" / "compare_connector" / "server.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    searchable = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SEARCHABLE" for target in node.targets)
    )
    names = {element.value for element in searchable.elts if isinstance(element, ast.Constant)}
    package_for_source = {
        "wildberries": "wb-connector",
        "yandex_market": "yandex-connector",
        "ozon": "ozon-connector",
        "avito": "avito-connector",
        "taobao": "taobao-connector",
        "megamarket": "megamarket-connector",
        "lamoda": "lamoda-connector",
        "dns": "dns-connector",
        "citilink": "citilink-connector",
        "aliexpress": "aliexpress-connector",
    }
    metadata = tomllib.loads((ROOT / "packages" / "compare-connector" / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    available = set(metadata["dependencies"]) | set(metadata["optional-dependencies"]["all"])
    assert names == set(package_for_source)
    assert {package_for_source[name] for name in names} <= available
