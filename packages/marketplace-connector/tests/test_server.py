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
    # mpstats is absent on purpose: without MPSTATS_MP_AUTH its calls can only
    # return auth_missing, so the unified server does not mount it
    # (R-MPSTATS-MOUNT-GATE; the gate itself is tested in
    # test_mpstats_mount_gate.py).
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
    # 8 + 3 + 2 + 3 + 3 + 2 + 2 + 2 + 2 + 2 + 2 + 2 + 4 = 37 mounted tools across
    # 13 servers, plus marketplace_sources, which this server owns rather than
    # mounts. Operator-only *_selfcheck diagnostics are not MCP tools. mpstats
    # is not mounted without MPSTATS_MP_AUTH (R-MPSTATS-MOUNT-GATE), so its two
    # tools are absent here.
    own = {"marketplace_sources"}
    assert own <= names
    assert len(tools) == 38, f"expected 37 mounted tools + 1 own, got {len(tools)}"
    assert len(names - own) == 37


def test_marketplace_sources_reports_what_mounted():
    """A skipped source must be visible to the client, not just to stderr."""
    result = asyncio.run(server.marketplace_sources())

    assert result.mounted_count == 13
    assert result.skipped_count == 1
    assert "wildberries" in result.mounted
    assert "citilink" in result.mounted
    assert "aliexpress" in result.mounted
    assert "cian" in result.mounted
    assert result.capabilities["cian"]["requires_cdp"] is True
    assert result.capabilities["cian"]["text_search"] is False
    # mpstats cannot answer without MPSTATS_MP_AUTH, so it is skipped with the
    # reason a human needs to turn it on, not mounted with dead tools.
    assert set(result.skipped) == {"mpstats"}
    assert "mpstats" not in result.mounted
    assert "MPSTATS_MP_AUTH" in result.skipped["mpstats"]
    assert result.capabilities["mpstats"]["mounted"] is False
    assert result.server_version == server.SERVER_VERSION
    assert result.capabilities["taobao"]["currency"] == "cny"
    assert result.capabilities["taobao"]["requires_login"] is True
    assert result.capabilities["wildberries"]["text_search"] is True


def test_marketplace_sources_capabilities_mark_skipped_sources(monkeypatch):
    monkeypatch.setattr(server, "_MOUNTED", ["wildberries"])
    monkeypatch.setattr(server, "_SKIPPED", {"ozon": "missing"})

    result = asyncio.run(server.marketplace_sources())

    assert result.capabilities["ozon"]["mounted"] is False
    assert result.capabilities["wildberries"]["mounted"] is True


def test_marketplace_sources_surfaces_a_skipped_source(monkeypatch):
    """Simulate the broken-install case the defensive import exists for."""
    monkeypatch.setattr(server, "_MOUNTED", ["wildberries"])
    monkeypatch.setattr(server, "_SKIPPED", {"taobao": "ModuleNotFoundError: No module named 'playwright'"})

    result = asyncio.run(server.marketplace_sources())

    assert result.mounted == ["wildberries"]
    assert result.skipped_count == 1
    assert "playwright" in result.skipped["taobao"]
