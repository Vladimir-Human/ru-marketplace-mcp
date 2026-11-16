"""Cian's accepted schemeless links still obey the page-host boundary.

START_MODULE_CONTRACT
PURPOSE: Prove R-NAV-HOSTS.kept-inputs and invariant for schemeless card links.
SCOPE: Real offer parser and public card tool; a recorded 404 transport spy, no I/O.
DEPENDS: pytest; cian_connector.server; fastmcp.exceptions.ToolError.
LINKS: M-CIAN; M-SOURCES; R-NAV-HOSTS.kept-inputs; R-NAV-HOSTS.invariant.
END_MODULE_CONTRACT
START_MODULE_MAP
test_schemeless_offer_links_keep_the_id: The legacy inputs yield their id and reach card dispatch.
test_schemeless_foreign_links_have_no_id: A lookalike or embedded domain cannot rescue a foreign host.
END_MODULE_MAP
"""

import json

import pytest
from cian_connector import server
from fastmcp.exceptions import ToolError

# START_BLOCK_SCHEMELESS_INPUTS


@pytest.mark.req("R-NAV-HOSTS.kept-inputs")
@pytest.mark.parametrize(
    "url",
    [
        "www.cian.ru/sale/flat/123456789/",
        "cian.ru/rent/flat/123456789",
        "spb.cian.ru/sale/flat/123456789/",
    ],
)
async def test_schemeless_offer_links_keep_the_id(monkeypatch, url):
    assert server._extract_offer_id(url) == 123456789
    calls = []

    async def missing_card(offer_id, ctx):
        calls.append(offer_id)
        return 404, "", "cdp"

    monkeypatch.setattr(server, "_fetch_card", missing_card)
    with pytest.raises(ToolError) as caught:
        await server.cian_card(offer_id_or_url=url)
    assert json.loads(str(caught.value))["error"] == "not_found"
    assert calls == [123456789]


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize(
    "url",
    [
        "cian.ru.evil.example/sale/flat/123456789/",
        "evil.example/cian.ru/sale/flat/123456789/",
    ],
)
def test_schemeless_foreign_links_have_no_id(url):
    assert server._extract_offer_id(url) is None


# END_BLOCK_SCHEMELESS_INPUTS
