"""The image-check workflow keeps the contract that makes it an image gate.

R-CI-IMAGE-CHECK: the workflow is triggered by hand and by relevant pull
requests, pins the same action SHAs as the existing workflows, only reads the
repository, builds both images and drives the stdio image through a real MCP
session at the root pyproject version.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
IMAGE = WORKFLOWS / "image-check.yml"
REGISTRY = WORKFLOWS / "mcp-registry-publish.yml"


def test_triggers_and_watched_paths() -> None:
    text = IMAGE.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "pull_request:" in text
    for path in (
        "Dockerfile",
        "Dockerfile.stdio",
        ".dockerignore",
        "uv.lock",
        "pyproject.toml",
        "packages/*/pyproject.toml",
        ".github/workflows/image-check.yml",
    ):
        assert f"- {path}" in text, f"image-check.yml no longer watches {path}"


def test_actions_keep_the_exact_pinned_shas() -> None:
    text = IMAGE.read_text(encoding="utf-8")
    registry = REGISTRY.read_text(encoding="utf-8")
    for action in ("actions/checkout", "docker/setup-buildx-action", "docker/build-push-action"):
        pinned = re.findall(re.escape(action) + r"@([0-9a-f]{40})", registry)
        assert pinned, f"{action} is no longer pinned in mcp-registry-publish.yml"
        assert len(set(pinned)) == 1, f"{action} has more than one pin to compare"
        assert f"{action}@{pinned[0]}" in text, f"image-check.yml does not reuse the {action} pin"


def test_stdio_image_is_loaded_and_probed_at_the_root_version() -> None:
    text = IMAGE.read_text(encoding="utf-8")
    assert "tomllib.load" in text and "pyproject.toml" in text
    assert "file: ./Dockerfile.stdio" in text
    assert "load: true" in text
    assert "MCP_DOCKER_IMAGE:" in text
    assert "MCP_EXPECTED_VERSION:" in text
    assert "python3 scripts/e2e_stdio_check_docker.py" in text


def test_the_http_image_is_built_without_loading_or_pushing() -> None:
    text = IMAGE.read_text(encoding="utf-8")
    assert "file: ./Dockerfile" in text
    assert "load: false" in text
    assert text.count("push: false") == 2, "both builds must disable push"


def test_permissions_are_read_only() -> None:
    text = IMAGE.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read\n" in text
    assert "packages: write" not in text
    assert "id-token: write" not in text
