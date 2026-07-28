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

    # Every connector's selfcheck must be present — it is the one tool each
    # package guarantees, so its absence means the mount silently failed.
    expected_selfchecks = {
        "wb_selfcheck",
        "ozon_selfcheck",
        "yandex_selfcheck",
        "detmir_selfcheck",
        "avito_selfcheck",
        "taobao_selfcheck",
        "megamarket_selfcheck",
        "lamoda_selfcheck",
        "dns_selfcheck",
        "citilink_selfcheck",
    }
    missing = expected_selfchecks - names
    assert not missing, f"selfchecks not mounted: {missing}"


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
    # 9 + 4 + 3 + 4 + 4 + 3 + 3 + 3 + 3 + 3 + 2 = 41 tools across 11 servers,
    # plus marketplace_sources, which this server owns rather than mounts.
    own = {"marketplace_sources"}
    assert own <= names
    assert len(tools) == 42, f"expected 41 mounted tools + 1 own, got {len(tools)}"
    assert len(names - own) == 41


def test_marketplace_sources_reports_what_mounted():
    """A skipped source must be visible to the client, not just to stderr."""
    result = asyncio.run(server.marketplace_sources())

    assert result.mounted_count == 11
    assert result.skipped_count == 0
    assert result.skipped == {}
    assert "wildberries" in result.mounted
    assert "citilink" in result.mounted
    assert result.server_version == server.SERVER_VERSION


def test_marketplace_sources_surfaces_a_skipped_source(monkeypatch):
    """Simulate the broken-install case the defensive import exists for."""
    monkeypatch.setattr(server, "_MOUNTED", ["wildberries"])
    monkeypatch.setattr(server, "_SKIPPED", {"taobao": "ModuleNotFoundError: No module named 'playwright'"})

    result = asyncio.run(server.marketplace_sources())

    assert result.mounted == ["wildberries"]
    assert result.skipped_count == 1
    assert "playwright" in result.skipped["taobao"]
