"""Fixture-backed public searches must not use the deprecated MCP log channel.

START_MODULE_CONTRACT
PURPOSE: Prove R-NO-MCP-LOGGING with real Context/SDK warnings and offline transports.
SCOPE: Every registry marketplace listing tool; static guard covers non-search tools too.
DEPENDS: pytest; FastMCP Client; mcp warning class; existing captured-listing adapters.
LINKS: M-SOURCES; M-WB; M-OZON; M-YANDEX; M-DETMIR; M-AVITO; R-NO-MCP-LOGGING.
END_MODULE_CONTRACT
START_MODULE_MAP
_install: Reuse fixture transports, retaining logging-bearing HTTP/CDP shells.
test_fixture_search_has_no_mcp_logging_warning: Real MCP calls with SDK warnings as errors.
test_no_production_context_logging_calls: Cover all logging siblings and non-search tools.
END_MODULE_MAP
"""

from __future__ import annotations

import ast
import importlib
import json
import warnings
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPDeprecationWarning
from mcp_core.cache import TTLCache
from mcp_core.sources import BY_NAME, SOURCES

from .test_selfcheck_gates_price import ADAPTERS, Adapter

# START_BLOCK_FIXTURE_TRANSPORTS
ROOT = Path(__file__).resolve().parents[4]


async def _no_wait() -> None:
    return None


def _install(adapter: Adapter, module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    """Replace I/O, never Context logging or result parsing/validation.

    The shared adapters already feed captured pages through the real extractors.
    HTTP sources use lower seams here so their ctx.debug paths are exercised;
    Avito/Ozon intentionally fall back to the offline CDP reply.
    """
    monkeypatch.setattr(module, "_cache", TTLCache(ttl_s=300, max_entries=64))
    if adapter.source in {"yandex_market", "detsky_mir"}:

        async def http_reply(*args: Any, **kwargs: Any) -> tuple[int, str]:
            return 200, payload if isinstance(payload, str) else json.dumps(payload)

        monkeypatch.setattr(module, "get_text_with_retries", http_reply)
    elif adapter.source == "avito":

        def refused_http(*args: Any, **kwargs: Any) -> tuple[int, str]:
            return 403, "offline tier-1 refusal"

        async def cdp_reply(*args: Any, **kwargs: Any) -> tuple[int, str]:
            return 200, json.dumps(payload)

        monkeypatch.setattr(module, "_polite_wait", _no_wait)
        monkeypatch.setattr(module, "_sync_curl_get", refused_http)
        monkeypatch.setattr(module, "_cdp_fetch", cdp_reply)
    elif adapter.source == "ozon":

        async def refused_bounded(*args: Any, **kwargs: Any) -> tuple[int, str]:
            return 403, "offline tier-1 refusal"

        async def composer_cdp_reply(*args: Any, **kwargs: Any) -> tuple[int, str]:
            return 200, json.dumps(payload)

        monkeypatch.setattr(module, "_polite_wait", _no_wait)
        monkeypatch.setattr(module, "_run_sync_bounded", refused_bounded)
        monkeypatch.setattr(module, "_cdp_fetch_json", composer_cdp_reply)
    else:
        adapter.install(module, monkeypatch, payload)


# END_BLOCK_FIXTURE_TRANSPORTS
# START_BLOCK_CONFORMANCE


@pytest.mark.req("R-NO-MCP-LOGGING.fixtures")
@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda adapter: adapter.source)
async def test_fixture_search_has_no_mcp_logging_warning(adapter: Adapter, monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module(BY_NAME[adapter.source].module)
    payload = adapter.load(module)
    _install(adapter, module, monkeypatch, payload)
    monkeypatch.setattr(module.mcp, "client_log_level", "debug")
    if adapter.source == "detsky_mir":
        tool, args, rows = "detmir_category", {"alias": "pups"}, "items"
    elif adapter.source == "cian":
        tool, args, rows = "cian_search", {"deal": "sale"}, "items"
    else:
        tool, args, rows = BY_NAME[adapter.source].tool_prefix + "search", {"query": "fixture query"}, "items"
    with warnings.catch_warnings():
        warnings.simplefilter("error", MCPDeprecationWarning)
        async with Client(module.mcp) as client:
            # Repeat to exercise a cached read (including Detmir's cache-hit log).
            for _ in range(2):
                reply = await client.call_tool(tool, args)
                assert reply.structured_content is not None
                # WB's union response keeps the SDK's advertised result envelope.
                result = reply.structured_content.get("result", reply.structured_content)
                assert result[rows], "the fixture must reach a successful nonempty result"


@pytest.mark.req("R-NO-MCP-LOGGING.context-calls")
def test_no_production_context_logging_calls() -> None:
    violations = []
    for path in sorted((ROOT / "packages").glob("*/src/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ctx"
                and node.func.attr in {"info", "debug", "warning", "warn", "error", "log", "critical", "exception"}
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: ctx.{node.func.attr}")
    assert not violations, "deprecated MCP logging calls remain:\n" + "\n".join(violations)
    # Detmir uses category.
    assert {adapter.source for adapter in ADAPTERS} == {
        source.name for source in SOURCES if source.kind == "marketplace"
    }


# END_BLOCK_CONFORMANCE
