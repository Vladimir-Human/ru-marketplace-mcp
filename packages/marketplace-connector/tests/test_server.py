"""Offline tests for the unified marketplace server.

The unified server is a mount point, so its tests assert two things: every
installed connector's tools appear under their own names, and a connector that
fails to import is skipped rather than sinking the server.
"""

from __future__ import annotations

import asyncio

from marketplace_connector import server


def test_all_installed_sources_are_mounted():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}

    # One definitive tool per installed source. Selfchecks are no longer
    # mounted (they are operator-only diagnostics for `marketplace-mcp doctor`),
    # so a source's mount marker is the first model-facing tool it guarantees.
    expected_markers = {
        "wb_search",
        "ozon_card",
        "yandex_search",
        "detmir_card",
        "avito_search",
        "taobao_search",
        "megamarket_search",
        "lamoda_card",
        "dns_search",
        "citilink_search",
        "compare_prices",
        "mpstats_item",
    }
    missing = expected_markers - names
    assert not missing, f"sources not mounted: {missing}"


def test_tool_names_keep_their_source_prefixes():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}

    assert "wb_search" in names
    assert "ozon_card" in names
    assert "avito_seller" in names
    assert "taobao_search" in names
    assert "compare_prices" in names


def test_the_mounted_count_matches_the_imported_sources():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    # 8 + 3 + 2 + 3 + 3 + 2 + 2 + 2 + 2 + 2 + 2 + 2 = 33 mounted tools across
    # 12 servers, plus marketplace_sources, which this server owns rather than
    # mounts. Operator-only *_selfcheck diagnostics are not MCP tools.
    own = {"marketplace_sources"}
    assert own <= names
    assert len(tools) == 34, f"expected 33 mounted tools + 1 own, got {len(tools)}"
    assert len(names - own) == 33


def test_marketplace_sources_reports_what_mounted():
    """A skipped source must be visible to the client, not just to stderr."""
    result = asyncio.run(server.marketplace_sources())

    assert result.mounted_count == 12
    assert result.skipped_count == 0
    assert result.skipped == {}
    assert "wildberries" in result.mounted
    assert "citilink" in result.mounted
    assert "mpstats" in result.mounted
    assert result.server_version == server.SERVER_VERSION


def test_marketplace_sources_surfaces_a_skipped_source(monkeypatch):
    """Simulate the broken-install case the defensive import exists for."""
    monkeypatch.setattr(server, "_MOUNTED", ["wildberries"])
    monkeypatch.setattr(server, "_SKIPPED", {"taobao": "ModuleNotFoundError: No module named 'playwright'"})

    result = asyncio.run(server.marketplace_sources())

    assert result.mounted == ["wildberries"]
    assert result.skipped_count == 1
    assert "playwright" in result.skipped["taobao"]
