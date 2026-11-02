"""R08: real WB MCP replies match the schema sent by tools/list.

Only upstream reads are synthetic: the public wb_search body, FastMCP result
conversion and in-process MCP protocol all run for success/no_results/error.
"""
# START_MODULE_CONTRACT
# PURPOSE: Verify wb_search's advertised schema against its actual MCP replies.
# SCOPE: Offline v9 success, positively empty v9/legacy search, and typed HTTP 429.
# DEPENDS: FastMCP Client; jsonschema; wb_connector.server; stdlib contextlib/json.
# LINKS: M-WB; M-OUTPUT-SCHEMA; R-OUTPUT-SCHEMA-WRAPPING; V-M-WB.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_wb_search_mcp_answers_match_advertised_schema: Exercise tools/list and tools/call for all outcomes.
# END_MODULE_MAP

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastmcp import Client
from jsonschema import validate
from mcp.types import TextContent
from mcp_core.cache import TTLCache
from wb_connector import server

# START_BLOCK_WB_MCP_CONTRACT_TESTS


async def test_wb_search_mcp_answers_match_advertised_schema(monkeypatch):
    """R-OUTPUT-SCHEMA-WRAPPING: no phantom result field; errors stay MCP errors."""
    reads = []
    outcome = "success"
    query = "electric kettle"

    @asynccontextmanager
    async def fake_client():
        yield object()

    async def no_wait():
        return None

    async def fake_read(client, url):
        reads.append(url)
        if "v9/search" not in url:
            assert outcome == "no_results" and "search-goods.wildberries.ru" in url
            return 200, "[]", None
        if outcome == "error":
            return 429, "", None
        products = []
        if outcome == "success":
            products = [
                {
                    "id": 123,
                    "name": "Electric kettle",
                    "brand": "ACME",
                    "totalQuantity": 5,
                    "sizes": [{"price": {"basic": 200000, "product": 150000}}],
                }
            ]
        return 200, json.dumps({"products": products, "total": len(products)}), None

    monkeypatch.setattr(server, "_wb_client", fake_client)
    monkeypatch.setattr(server, "_safe_get_text", fake_read)
    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "_page1_fingerprints", TTLCache(ttl_s=300, max_entries=16))
    monkeypatch.setattr(server._settings, "transport", "api")

    async with Client(server.mcp) as client:
        schema = next(tool.output_schema for tool in await client.list_tools() if tool.name == "wb_search")
        for outcome in ("success", "no_results", "error"):
            reads.clear()
            answer = await client.call_tool_mcp("wb_search", {"query": query})
            assert answer.content and isinstance(answer.content[0], TextContent)
            text_payload = json.loads(answer.content[0].text)
            if outcome == "error":
                # outputSchema constrains successful structuredContent, not MCP errors.
                assert answer.is_error is True
                assert answer.structured_content is None
                assert text_payload["error"] == "rate_limited"
                assert text_payload["retryable"] is True
                assert text_payload["retry_after_s"] == 60.0
                assert len(reads) == 1
                continue

            assert not answer.is_error
            structured = answer.structured_content
            assert structured is not None
            # JSON Schema properties are optional on the old compacter, so also
            # reject phantom advertised fields rather than accepting that loophole.
            assert set(structured) == set(schema["properties"]), (outcome, structured.keys(), schema)
            validate(structured, schema)
            payload = structured["result"]
            assert text_payload == payload
            assert answer.meta["fastmcp"]["wrap_result"] is True
            assert payload["query"] == query
            assert payload["page"] == 1
            if outcome == "success":
                assert payload["count"] == 1
                assert payload["items"][0]["nm_id"] == 123
                assert payload["items"][0]["price_rub"] == 1500.0
                assert payload["items"][0]["in_stock"] is True
                assert payload["meta"]["healthy"] is True
                assert len(reads) == 1
            else:
                assert payload == {"status": "no_results", "query": query, "page": 1, "total_ids": 0, "meta": None}
                assert len(reads) == 2


# END_BLOCK_WB_MCP_CONTRACT_TESTS
