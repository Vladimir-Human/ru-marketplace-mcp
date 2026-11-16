"""R-NO-PERSONAL-DATA: Cian publisher names are organisations only."""
# START_MODULE_CONTRACT
# PURPOSE: Prove agent and agency_name obey the publisher's own organisation signals.
# SCOPE: Captured offer fixtures with placeholder values and transport-only fakes; no browser.
# DEPENDS: pytest; FastMCP Client; cian_connector.server/privacy.
# LINKS: M-CIAN-PRIVACY; R-NO-PERSONAL-DATA.cian.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _publisher: Derive a publisher from a fixture, replacing identity and classification values.
# test_publisher_name_rule: Exercise every organisation signal and private/unknown refusal.
# test_public_cian_names: Check search, card and card user-fallback over MCP.
# END_MODULE_MAP

import json
from pathlib import Path

import pytest
from cian_connector import server
from cian_connector.privacy import public_publisher_name
from fastmcp import Client

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    ({"accountType": "specialist"}, False),
    ({"userType": "homeowner"}, False),
    ({}, False),
    ({"accountType": "agency"}, True),
    ({"isDeveloper": True}, True),
    ({"isBuilder": True}, True),
    ({"userType": "developer"}, True),
    ({"profileUri": "company/placeholder/"}, True),
    ({"profileUri": "agents/placeholder/", "agency": {"accountType": "agency"}}, False),
]

# START_BLOCK_PURE_RULE


def _publisher(signals):
    data = json.loads((FIXTURES / "card_sale_live.json").read_text(encoding="utf-8"))
    publisher = data["offerData"]["agent"]
    for key in ("accountType", "userType", "isDeveloper", "isBuilder", "profileUri", "agency"):
        publisher.pop(key, None)
    publisher.update(signals)
    publisher.update(name="Placeholder Person", agencyName="Placeholder Person", companyName="Placeholder Person")
    return publisher


@pytest.mark.req("R-NO-PERSONAL-DATA.cian")
@pytest.mark.parametrize("signals,kept", CASES)
def test_publisher_name_rule(signals, kept):
    publisher = _publisher(signals)
    assert public_publisher_name(publisher, publisher["name"]) == ("Placeholder Person" if kept else None)


# END_BLOCK_PURE_RULE
# START_BLOCK_PUBLIC_TOOLS
@pytest.mark.req("R-NO-PERSONAL-DATA.cian")
@pytest.mark.parametrize("signals,kept", CASES)
@pytest.mark.parametrize("surface", ["search", "card", "card-user-fallback"])
async def test_public_cian_names(monkeypatch, signals, kept, surface):
    publisher = _publisher(signals)
    if surface == "search":
        payload = json.loads((FIXTURES / "search_sale_live.json").read_text(encoding="utf-8"))
        offer = payload["data"]["offersSerialized"][0]
        offer["user"] = publisher
        payload["data"]["offersSerialized"] = [offer]
    else:
        fixture = json.loads((FIXTURES / "card_sale_live.json").read_text(encoding="utf-8"))
        offer = fixture["offerData"]["offer"]
        # Viewer identity must never qualify the publisher.
        fixture["offerData"]["user"] = {"accountType": "agency", "name": "Placeholder Viewer"}
        if surface == "card":
            fixture["offerData"]["agent"] = publisher
        else:
            fixture["offerData"].pop("agent")
            offer["user"] = publisher
        payload = {
            "kind": "ok",
            "title": "Sale",
            "url": "https://www.cian.ru/sale/flat/331002424/",
            "offerData": fixture["offerData"],
        }

    async def fake_fetch(handle, ctx):
        return 200, json.dumps(payload), "cdp"

    server._cache.clear()
    monkeypatch.setattr(server, "_fetch_search", fake_fetch)
    monkeypatch.setattr(server, "_fetch_card", fake_fetch)
    tool = "cian_search" if surface == "search" else "cian_card"
    args = {"deal": "sale"} if surface == "search" else {"offer_id_or_url": "331002424"}
    async with Client(server.mcp) as client:
        result = (await client.call_tool(tool, args)).structured_content
    assert result is not None
    row = result["items"][0] if surface == "search" else result
    expected = "Placeholder Person" if kept else None
    assert row["agency_name"] == expected
    if surface != "search":
        assert row["agent"]["name"] == expected
    assert row["price_rub"] == 11985439
    assert "Placeholder Viewer" not in json.dumps(result)
    if not kept:
        assert "Placeholder Person" not in json.dumps(result)


# END_BLOCK_PUBLIC_TOOLS
