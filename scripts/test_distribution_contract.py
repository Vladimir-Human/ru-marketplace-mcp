"""Keep standalone-install documentation aligned with package metadata."""

from __future__ import annotations

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


def test_deployment_does_not_advertise_unpublished_uvx_install() -> None:
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "uvx" not in deployment
    assert "GitHub Release" in deployment
    assert "--find-links wheelhouse" in deployment
    assert "mcp-core==2.4.1" in deployment
