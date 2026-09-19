"""Keep standalone-install documentation aligned with package metadata."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

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
    assert "GitHub Release" in deployment
    assert "--find-links wheelhouse" in deployment
    assert "aliexpress-connector==2.4.1" in deployment
    assert "uv run --frozen --directory" in deployment


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
