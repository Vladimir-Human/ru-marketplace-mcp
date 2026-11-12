"""Issue #225: comparison selection must not depend on cached connector imports."""
# START_MODULE_CONTRACT
# PURPOSE: Prove allowed-source discovery, introspection and dispatch through public MCP tools.
# SCOPE: Offline native-call doubles; cold/preloaded modules; selected/unset/blank environment.
# DEPENDS: compare/decision servers; FastMCP Client; mcp_core source registry; pytest.
# LINKS: M-COMPARE; M-SOURCE-SELECTION; R-COMPARE-SOURCE-SELECTION.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# connector_calls: Replace every native comparison search/card call without changing adapters.
# load_server: Load a fresh server without reloading the suite's shared module objects.
# test_compare_cold_discovery_skips_deselected_connectors: Disabled connectors are never discovered.
# test_compare_prices_uses_selected_sources_regardless_of_discovery_state: Default fan-out is stable.
# test_compare_sources_filters_preloaded_deselected_connectors: Inventory respects current selection.
# test_compare_tools_refuse_explicit_deselected_source: Disabled aliases are clear bad_request errors.
# test_compare_verification_still_reads_an_allowed_source: Refusal does not disable allowed cards.
# END_MODULE_MAP

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from compare_connector import server
from fastmcp import Client
from mcp_core.source_selection import ENV_VAR
from mcp_core.sources import BY_NAME, COMPARE_SOURCES

SELECTION = "wildberries,ozon,yandex_market,compare"
ALLOWED = {"wildberries", "ozon", "yandex_market"}


# START_BLOCK_OFFLINE_SERVERS
@pytest.fixture
def connector_calls(monkeypatch):
    calls = []

    def search_for(name):
        async def search(**kwargs):
            calls.append((name, "search"))
            return SimpleNamespace(items=[], meta={"healthy": True, "warnings": []})

        return search

    def card_for(name):
        async def card(**kwargs):
            calls.append((name, "card"))
            if name == "wildberries":
                return {"items": [{"nm_id": kwargs["nm_ids"][0], "price_rub": 100}]}
            return {"price_rub": 100}

        return card

    for name in COMPARE_SOURCES:
        spec = BY_NAME[name]
        module = importlib.import_module(spec.module)
        if spec.text_search:
            monkeypatch.setattr(module, f"{spec.tool_prefix}search", search_for(name))
        monkeypatch.setattr(module, spec.card_tool, card_for(name))
    return calls


@pytest.fixture
def load_server(monkeypatch, connector_calls):
    def load(path):
        name = f"_selection_test_{path.parent.name}_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    return load


# END_BLOCK_OFFLINE_SERVERS
# START_BLOCK_SOURCE_SELECTION
async def test_compare_cold_discovery_skips_deselected_connectors(monkeypatch, load_server):
    """R-COMPARE-SOURCE-SELECTION.discovery: cold startup never imports disabled connectors."""
    monkeypatch.setenv(ENV_VAR, SELECTION)
    imported = []
    real_import = importlib.import_module
    comparison_modules = {BY_NAME[name].module: name for name in COMPARE_SOURCES}

    def record_import(name, package=None):
        if name in comparison_modules:
            imported.append(comparison_modules[name])
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", record_import)
    compare = load_server(Path(server.__file__))
    async with Client(compare.mcp) as client:
        report = (await client.call_tool("compare_sources", {})).data
    assert set(report["searchable"]) == ALLOWED
    assert set(imported) == ALLOWED


@pytest.mark.parametrize("discovery", ["cold", "preloaded", "unset", "blank"])
async def test_compare_prices_uses_selected_sources_regardless_of_discovery_state(
    monkeypatch, load_server, connector_calls, discovery
):
    """R-COMPARE-SOURCE-SELECTION.defaults: the reporter's default fan-out, plus unset controls."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    if discovery == "cold":
        monkeypatch.setenv(ENV_VAR, SELECTION)
    compare = load_server(Path(server.__file__))
    if discovery == "preloaded":
        monkeypatch.setenv(ENV_VAR, SELECTION)
    elif discovery == "blank":
        monkeypatch.setenv(ENV_VAR, "   ")
    expected = ALLOWED if discovery in {"cold", "preloaded"} else set(server.SEARCHABLE)

    import compare_connector

    monkeypatch.setitem(sys.modules, "compare_connector.server", compare)
    monkeypatch.setattr(compare_connector, "server", compare)
    unified = load_server(
        Path(server.__file__).resolve().parents[4]
        / "packages/marketplace-connector/src/marketplace_connector/server.py"
    )
    async with Client(unified.mcp) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert {"wb_search", "ozon_search", "yandex_search", "compare_prices"} <= tools
        if discovery in {"cold", "preloaded"}:
            assert not any(
                tool.startswith(("avito_", "taobao_", "megamarket_", "lamoda_", "dns_", "citilink_", "aliexpress_"))
                for tool in tools
            )
        for _ in range(2):
            connector_calls.clear()
            result = (
                await client.call_tool("compare_prices", {"query": "realme P4 8/256", "per_source_limit": 3})
            ).structured_content
            assert set(result["sources_queried"]) == expected
            assert {outcome["source"] for outcome in result["source_outcomes"]} == expected
            assert all(outcome["status"] == "ok" for outcome in result["source_outcomes"])
            assert result["complete"] is True
            assert sorted(connector_calls) == sorted((name, "search") for name in expected)


async def test_compare_sources_filters_preloaded_deselected_connectors(monkeypatch, load_server):
    """R-COMPARE-SOURCE-SELECTION.inventory: cached availability cannot override deselection."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    compare = load_server(Path(server.__file__))
    monkeypatch.setenv(ENV_VAR, SELECTION)
    async with Client(compare.mcp) as client:
        report = (await client.call_tool("compare_sources", {})).data
    assert set(report["installed"]) == ALLOWED
    assert set(report["searchable"]) == ALLOWED
    assert not set(report["installed"]) & set(report["deselected"])
    assert report["not_installed"] == []


@pytest.mark.parametrize("tool", ["compare_prices", "compare_verify_offer", "decision_inspect"])
async def test_compare_tools_refuse_explicit_deselected_source(monkeypatch, load_server, connector_calls, tool):
    """R-COMPARE-SOURCE-SELECTION.refusal: an explicit disabled alias never reaches native I/O."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    compare = load_server(Path(server.__file__))
    mcp = compare.mcp
    if tool == "decision_inspect":
        import compare_connector

        monkeypatch.setattr(compare_connector, "server", compare)
        mcp = load_server(Path(server.__file__).with_name("decision_server.py")).mcp
    monkeypatch.setenv(ENV_VAR, SELECTION)
    arguments = (
        {"query": "realme P4 8/256", "sources": ["ali"]}
        if tool == "compare_prices"
        else {"source": "ali", "product_id_or_url": "123"}
    )
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error is True
    payload = json.loads(result.content[0].text)
    assert payload["error"] == "bad_request"
    assert "aliexpress" in payload["message"]
    assert "deselected by MARKETPLACE_SOURCES" in payload["message"]
    assert connector_calls == []


async def test_compare_verification_still_reads_an_allowed_source(monkeypatch, load_server, connector_calls):
    """R-COMPARE-SOURCE-SELECTION.refusal: an allowed alias still dispatches and verifies."""
    monkeypatch.setenv(ENV_VAR, SELECTION)
    compare = load_server(Path(server.__file__))
    async with Client(compare.mcp) as client:
        result = (
            await client.call_tool(
                "compare_verify_offer", {"source": "WB", "product_id_or_url": "123", "expected_price_rub": 100}
            )
        ).data
    assert result["source"] == "wildberries"
    assert result["price_verification"]["matches"] is True
    assert connector_calls == [("wildberries", "card")]


# END_BLOCK_SOURCE_SELECTION
