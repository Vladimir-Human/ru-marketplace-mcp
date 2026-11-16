"""WB public review/question outputs omit identity, without reading author data."""
# START_MODULE_CONTRACT
# PURPOSE: Prove the two removed WB user fields are absent in public serialized answers.
# SCOPE: Existing feedback/question payload shapes; transport-only fakes; no marketplace.
# DEPENDS: pytest; FastMCP Client; wb_connector.server.
# LINKS: M-WB; R-NO-PERSONAL-DATA.removed.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_public_wb_omits_user: Serve a placeholder-bearing upstream pool and assert the public shape.
# END_MODULE_MAP

import json

import pytest
from fastmcp import Client
from wb_connector import server


# START_BLOCK_PUBLIC_TOOLS
@pytest.mark.req("R-NO-PERSONAL-DATA.removed")
@pytest.mark.parametrize("tool,collection", [("wb_reviews", "feedbacks"), ("wb_questions", "questions")])
async def test_public_wb_omits_user(monkeypatch, tool, collection):
    entry = {
        "id": "placeholder-question",
        "text": "Does it fit?",
        "productValuation": 5,
        "createdDate": "2026-07-01T09:00:00Z",
        "wbUserDetails": {"name": "Placeholder Person"},
    }
    body = {collection: [entry], "count": 1, "feedbackCount": 1, "err": None}

    async def fake_read(client, url):
        return 200, json.dumps(body), None

    async def no_wait():
        pass

    server._cache.clear()
    monkeypatch.setattr(server, "_safe_get_text", fake_read)
    monkeypatch.setattr(server, "_polite_wait", no_wait)
    async with Client(server.mcp) as client:
        result = (await client.call_tool(tool, {"imt_id": 123, "limit": 1})).structured_content
    assert result is not None
    assert len(result[collection]) == 1
    assert "user" not in result[collection][0]
    assert result[collection][0]["text"] == "Does it fit?"
    assert "Placeholder Person" not in json.dumps(result)


# END_BLOCK_PUBLIC_TOOLS
