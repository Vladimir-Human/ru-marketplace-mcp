"""The workflows a tag push starts publish things: they must not trust what they cannot check.

Pre-release audit 2026-10-30 (MiniMax S3-F5, S3-F8 and a cache found while
checking them): the downloaded mcp-publisher runs with packages: write and
id-token: write, so it is verified against a pinned digest before it is
unpacked; no tag job leaves the checkout token on disk or restores a cache
another run could have written; one run per tag at a time. The checks read
the workflow text, because running a publishing workflow is the thing they guard.
"""

# START_MODULE_CONTRACT
# PURPOSE: Pin the supply-chain guarantees of the tag-triggered publishing workflows.
# SCOPE: release.yml, mcp-registry-publish.yml, pypi-publish.yml read as text; nothing is run.
# DEPENDS: pytest.
# LINKS: R-CI-TAG-WORKFLOWS.
# END_MODULE_CONTRACT

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TAG_WORKFLOWS = ("release.yml", "mcp-registry-publish.yml", "pypi-publish.yml")
PUBLISHER_SHA256 = "a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", TAG_WORKFLOWS)
def test_a_tag_workflow_runs_once_per_ref(name: str) -> None:
    text = _read(name)

    assert re.search(r"^concurrency:\n  group: \$\{\{ github\.workflow \}\}-\$\{\{ github\.ref \}\}\n", text, re.M)
    assert re.search(r"^  cancel-in-progress: false$", text, re.M), "a queued publish must not cancel a running one"


@pytest.mark.parametrize("name", TAG_WORKFLOWS)
def test_a_tag_workflow_never_persists_the_checkout_token(name: str) -> None:
    text = _read(name)
    checkouts = re.findall(r"- uses: actions/checkout@[0-9a-f]{40}[^\n]*\n((?:        [^\n]*\n)*)", text)

    assert checkouts, "every tag workflow checks out the tagged tree"
    assert all("persist-credentials: false" in block for block in checkouts)


@pytest.mark.parametrize("name", TAG_WORKFLOWS)
def test_a_tag_workflow_restores_no_cache(name: str) -> None:
    text = _read(name)

    assert "enable-cache: true" not in text
    assert "cache-from" not in text
    if "astral-sh/setup-uv@" in text:
        assert "enable-cache: false" in text


def test_the_registry_publisher_is_verified_before_it_is_unpacked() -> None:
    text = _read("mcp-registry-publish.yml")
    step = text.split("- name: Install mcp-publisher", 1)[1].split("- name:", 1)[0]

    assert f"PUBLISHER_SHA256: {PUBLISHER_SHA256}" in step
    assert "/download/v1.8.1/mcp-publisher_linux_amd64.tar.gz" in step
    assert "| tar" not in step, "a piped download is unpacked before anything can check it"
    check = step.index("sha256sum -c -")
    assert check < step.index("tar xzf mcp-publisher.tar.gz mcp-publisher") < step.index("./mcp-publisher --help")
