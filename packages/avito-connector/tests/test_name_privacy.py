"""R-NO-PERSONAL-DATA: fixture-backed name selection through all Avito tools."""
# START_MODULE_CONTRACT
# PURPOSE: Prove private/unknown names are null and companies/shops are retained.
# SCOPE: Captured seller blocks and transport-only fakes; no browser or marketplace.
# DEPENDS: pytest; FastMCP Client; avito_connector.server/privacy.
# LINKS: M-AVITO-PRIVACY; R-NO-PERSONAL-DATA.avito.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_seller_name_rule: Cover explicit company, shop exception and fail-closed cases.
# test_public_avito_names: Exercise search, card and profile serialization.
# END_MODULE_MAP

from pathlib import Path

import pytest
from avito_connector import server
from avito_connector.privacy import public_seller_name
from fastmcp import Client

FIXTURES = Path(__file__).parent / "fixtures"


# START_BLOCK_PURE_RULE
@pytest.mark.req("R-NO-PERSONAL-DATA.avito")
@pytest.mark.parametrize(
    "company,profile,kept",
    [
        (True, None, True),
        (False, "/brands/shop", True),
        (None, "https://www.avito.ru/brands/shop?from=item", True),
        (False, "/user/private/profile", False),
        (None, None, False),
        (None, "https://evil.test/brands/shop", False),
        (None, "https://www.avito.ru/user/private/profile?next=/brands/shop", False),
    ],
)
def test_seller_name_rule(company, profile, kept):
    block = (FIXTURES / "item_seller_block_7924452716.html").read_text(encoding="utf-8")
    name = server.parse_item_seller(block)["name"]
    assert name == "iStore | Техника Apple"
    assert public_seller_name(name, company, profile) == (name if kept else None)


@pytest.mark.req("R-NO-PERSONAL-DATA.avito")
@pytest.mark.parametrize("company", [True, False, None])
def test_seller_block_uses_native_company_hint_when_label_is_absent(company):
    block = (FIXTURES / "item_seller_block_7924452716.html").read_text(encoding="utf-8")
    block = block.replace("Частное лицо", "").replace(
        "/brands/27696b66792f17b347ec073a8d1c2d93", "/user/placeholder/profile"
    )
    result = server.parse_item_seller(block, company_hint=company)
    assert result["is_company"] is company
    assert result["name"] == ("iStore | Техника Apple" if company is True else None)


# END_BLOCK_PURE_RULE
# START_BLOCK_PUBLIC_TOOLS
@pytest.mark.req("R-NO-PERSONAL-DATA.avito")
@pytest.mark.parametrize("tool", ["avito_search", "avito_card", "avito_seller"])
@pytest.mark.parametrize("kind", ["private", "unknown", "company", "shop"])
async def test_public_avito_names(monkeypatch, tool, kind):
    import json

    # The captured block's structure stays intact; only label, profile and name
    # values change. The private-name sentinel is not a real person's identity.
    block = (FIXTURES / "item_seller_block_7924452716.html").read_text(encoding="utf-8")
    shop = "/brands/27696b66792f17b347ec073a8d1c2d93"
    profile = shop if kind == "shop" else "/user/placeholder/profile"
    label = {"private": "Частное лицо", "unknown": "", "company": "Компания", "shop": "Частное лицо"}[kind]
    name = "Placeholder Person" if kind in {"private", "unknown"} else "Placeholder Shop"
    block = block.replace("iStore | Техника Apple", name).replace("Частное лицо", label).replace(shop, profile)
    product = (FIXTURES / "item_page_7924452716.html").read_text(encoding="utf-8")
    card = product.replace("<body></body>", "<body>" + block + "</body>")
    page = (FIXTURES / "seller_profile_brands_27696b66.html").read_text(encoding="utf-8")
    page = page.replace("iStore | Техника Apple", name) + block
    payload = {
        "totalCount": 1,
        "items": [
            {
                "id": 7924452716,
                "title": "Phone",
                "price": 39990,
                "seller": {
                    "name": name,
                    "isCompany": None if kind == "unknown" else kind == "company",
                    "profileUrl": profile,
                },
            }
        ],
    }

    async def fake_search(url, ctx):
        return 200, json.dumps(payload), "fixture"

    async def fake_page(url, ctx):
        return 200, card if tool == "avito_card" else page, "cdp"

    server._cache.clear()
    monkeypatch.setattr(server, "_fetch", fake_search)
    monkeypatch.setattr(server, "_fetch_item_page", fake_page)
    args = {
        "avito_search": {"query": "phone"},
        "avito_card": {"item_id_or_url": "7924452716"},
        "avito_seller": {"seller_id_or_url": profile},
    }[tool]
    async with Client(server.mcp) as client:
        result = (await client.call_tool(tool, args)).structured_content
    assert result is not None
    expected = None if kind in {"private", "unknown"} else name
    if tool == "avito_search":
        assert result["items"][0]["seller_name"] == expected
    else:
        assert result["seller"]["name"] == expected
        assert result["seller"]["rating_score"] == 5.0
        assert result["seller"]["profile_url"].endswith(profile)
    assert name not in json.dumps(result) if expected is None else name in json.dumps(result)


# END_BLOCK_PUBLIC_TOOLS
