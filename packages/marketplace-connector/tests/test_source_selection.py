"""MARKETPLACE_SOURCES mounts the operator's subset and nothing else."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys

import pytest
from mcp_core import source_selection
from mcp_core.logging import make_stderr_logger
from mcp_core.source_selection import ENV_VAR, SourceSelectionError, canonical, selected, wanted


def _reload_unified():
    """Re-import the unified server so _mount_all runs under the current env."""
    import marketplace_connector.server as unified

    return importlib.reload(unified)


@pytest.fixture
def unified_env(monkeypatch):
    def _set(value: str | None):
        if value is None:
            monkeypatch.delenv(ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(ENV_VAR, value)
        return _reload_unified()

    yield _set
    monkeypatch.delenv(ENV_VAR, raising=False)
    _reload_unified()


def test_unset_env_mounts_everything(unified_env):
    # No pinned tool count here: test_server.py already pins it, and a new
    # connector would break this test for reasons unrelated to selection.
    server = unified_env(None)
    assert not any(reason.startswith("deselected") for reason in server._SKIPPED.values())


def test_blank_env_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "   ")
    assert selected() is None


def test_subset_drops_unlisted_sources(unified_env):
    server = unified_env("wildberries,avito")
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}

    assert any(name.startswith("wb_") for name in names)
    assert any(name.startswith("avito_") for name in names)
    assert not any(name.startswith("taobao_") for name in names)
    # marketplace_sources belongs to the unified server itself, never to a source.
    assert "marketplace_sources" in names


@pytest.mark.req("R-MPSTATS-REMOVED")
def test_removed_mpstats_keeps_other_sources_and_reports_official_server(unified_env, capfd, monkeypatch):
    notice = getattr(source_selection, "_warn_mpstats_removed", None)
    if notice is not None:
        notice.cache_clear()
    # A prior test may have bound this cached logger to its own capture stream.
    logger = make_stderr_logger("mcp_core.source_selection")
    handler = next(handler for handler in logger.handlers if isinstance(handler, logging.StreamHandler))
    monkeypatch.setattr(handler, "stream", sys.stderr)
    server = unified_env("wildberries,ozon,compare, MPStats ,mpstats")
    assert selected() == {"wildberries", "ozon", "compare"}
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert {"wb_search", "ozon_search", "compare_prices", "marketplace_sources"} <= names
    assert not any(name.startswith("mpstats_") for name in names)
    resources = asyncio.run(server.mcp.list_resources())
    assert not any(str(resource.uri).startswith("guide://mpstats/") for resource in resources)
    response = asyncio.run(server.marketplace_sources())
    assert set(response.mounted) == {"wildberries", "ozon", "compare"}
    assert "mpstats" not in response.capabilities
    # Compare and unified startup resolve the same selection more than once.
    assert selected() == {"wildberries", "ozon", "compare"}
    notices = [line for line in capfd.readouterr().err.splitlines() if "removed in 2.11.0" in line]
    assert len(notices) == 1
    assert "mpstats" in notices[0].lower()
    assert "https://mcp.mpstats.io/mcp?token=<your MPStats token>" in notices[0]


def test_deselected_sources_are_reported_not_hidden(unified_env):
    server = unified_env("wildberries")
    response = asyncio.run(server.marketplace_sources())

    assert "wildberries" in response.mounted
    assert ENV_VAR in response.skipped["taobao"]
    assert response.capabilities["wildberries"]["mounted"] is True
    assert response.capabilities["taobao"]["mounted"] is False


def test_capabilities_flag_survives_the_naming_mismatch(unified_env):
    """_MOUNTED says "yandex"/"detmir"; CAPABILITIES is keyed canonically."""
    server = unified_env("yandex,detmir")
    response = asyncio.run(server.marketplace_sources())

    assert response.capabilities["yandex_market"]["mounted"] is True
    assert response.capabilities["detsky_mir"]["mounted"] is True


def test_aliases_and_spacing_are_accepted(monkeypatch):
    monkeypatch.setenv(ENV_VAR, " WB , Yandex-Market ,ali")
    assert selected() == {"wildberries", "yandex_market", "aliexpress"}


def test_unknown_source_is_rejected_instead_of_silently_dropped(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "wildberries,typo_source")

    with pytest.raises(SourceSelectionError, match=r"unknown source.*typo_source"):
        selected()


def test_unified_server_rejects_invalid_selection(unified_env):
    with pytest.raises(SourceSelectionError, match=r"unknown source.*typo_source"):
        unified_env("wildberries,typo_source")


def test_wanted_defaults_to_keeping_everything():
    assert wanted("taobao", None) is True
    assert wanted("taobao", {"wildberries"}) is False
    assert canonical("Detmir") == "detsky_mir"


def test_compare_queries_only_the_selected_sources(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "wildberries,ozon")
    import compare_connector.server as compare

    compare = importlib.reload(compare)
    try:
        assert set(compare.SOURCES) == {"wildberries", "ozon"}
    finally:
        monkeypatch.delenv(ENV_VAR, raising=False)
        importlib.reload(compare)
