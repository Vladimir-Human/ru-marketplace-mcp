"""MPStats mounts only when a token can make its tools answer.

R-MPSTATS-MOUNT-GATE: without ``MPSTATS_MP_AUTH`` every mpstats call raises
``auth_missing`` before the wire, so the two schemas would be paid for on every
request with nothing behind them. The unified server therefore leaves the
source out and records the reason; the standalone ``mpstats-mcp`` server keeps
its tools. The decision is made when the unified module is imported, so these
tests reload it under a controlled environment and restore it afterwards.

Offline by construction: the mounted FastMCP objects are inspected in-process;
no connector call is made.
"""

from __future__ import annotations

import asyncio
import importlib
import os

import pytest
from marketplace_connector import server as marketplace_server
from mpstats_connector import server as mpstats_server

_MPSTATS_TOOLS = {"mpstats_item", "mpstats_warehouses"}


def _tool_names(server: object) -> set[str]:
    tools = asyncio.run(server.mcp.list_tools())  # type: ignore[attr-defined]
    return {tool.name for tool in tools}


@pytest.fixture
def unified(monkeypatch):
    """Reload the unified server under the test's environment, then restore it.

    ``tests/conftest.py`` pins ``MPSTATS_MP_AUTH`` to an empty value for this
    directory, so the baseline state is token-less whatever the developer's
    shell or ``.env`` holds; a test that wants a token sets one explicitly.
    """
    original = os.environ.get("MPSTATS_MP_AUTH")

    def _reload():
        return importlib.reload(marketplace_server)

    yield _reload

    if original is None:
        os.environ.pop("MPSTATS_MP_AUTH", None)
    else:
        os.environ["MPSTATS_MP_AUTH"] = original
    importlib.reload(marketplace_server)


def test_mpstats_tools_are_not_mounted_without_the_token(unified, monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", "")
    server = unified()

    assert _MPSTATS_TOOLS.isdisjoint(_tool_names(server))


def test_mpstats_tools_are_mounted_with_the_token(unified, monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", "test.jwt.token")
    server = unified()

    assert _tool_names(server) >= _MPSTATS_TOOLS


def test_the_skip_reason_names_the_missing_token(unified, monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", "")
    server = unified()

    response = asyncio.run(server.marketplace_sources())

    assert "mpstats" in response.skipped
    assert "MPSTATS_MP_AUTH" in response.skipped["mpstats"]
    assert response.capabilities["mpstats"]["mounted"] is False


def test_the_standalone_mpstats_server_keeps_its_tools(monkeypatch):
    """The gate lives in the unified mount, never in the standalone server."""
    monkeypatch.setenv("MPSTATS_MP_AUTH", "")

    assert _tool_names(mpstats_server) >= _MPSTATS_TOOLS
