"""WB's DOM redirect policy is narrower than its HTTP API/CDN policy.

START_MODULE_CONTRACT
PURPOSE: Prove R-NAV-HOSTS.invariant through WB's real renderer and owned navigation guard.
SCOPE: Offline page lifecycle and permit fakes only; no browser or marketplace calls.
DEPENDS: pytest; mcp_core.transport.chrome_cdp; wb_connector.dom_transport.
LINKS: M-WB; M-CHROME-CDP; M-SOURCES; R-NAV-HOSTS.invariant.
END_MODULE_CONTRACT
START_MODULE_MAP
redirect: Supply a final URL through the real open_page guard, without browser I/O.
test_wb_dom_refuses_fetch_only_redirects: API and basket hosts never reach extraction.
END_MODULE_MAP
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp_core.transport import chrome_cdp
from wb_connector import dom_transport as dom

# START_BLOCK_REDIRECT_FAKE


@pytest.fixture
def redirect(monkeypatch):
    state = SimpleNamespace(final="", closed=False, extracted=False)

    @asynccontextmanager
    async def lifecycle(url, wait_ms, **kwargs):
        async def evaluate(expression):
            state.extracted = True
            return '{"state":"ok","missing":true}' if expression == dom.PROBE_JS else '{"tiles":[]}'

        try:
            yield SimpleNamespace(url=state.final, evaluate=evaluate)
        finally:
            state.closed = True

    async def acquire(host):
        return SimpleNamespace(ok=lambda: None, neutral=lambda: None, release=lambda: None)

    async def no_gap():
        pass

    monkeypatch.setattr(chrome_cdp, "_playwright_page", lifecycle)
    monkeypatch.setattr(chrome_cdp, "_acquire_navigation_permit", acquire)
    monkeypatch.setattr(dom, "_gap", no_gap)
    dom._dom_cache._data.clear()
    return state


# END_BLOCK_REDIRECT_FAKE
# START_BLOCK_REDIRECT_POLICY


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize("host", ["card.wb.ru", "basket-01.wbbasket.ru"])
async def test_wb_dom_refuses_fetch_only_redirects(redirect, host):
    redirect.final = f"https://{host}/catalog/123456789/detail.aspx"
    with pytest.raises(chrome_cdp.NavigationPolicyError) as caught:
        await dom.render(dom.card_url(123456789), "() => '{}'", "card")
    assert caught.value.final_url == redirect.final
    assert redirect.closed
    assert not redirect.extracted


@pytest.mark.req("R-NAV-HOSTS.kept-inputs")
async def test_wb_dom_keeps_its_page_alias(redirect):
    redirect.final = "https://wildberries.ru/catalog/123456789/detail.aspx"
    assert await dom.render(dom.card_url(123456789), "() => '{}'", "card") == {"tiles": [], "page_says_empty": False}
    assert redirect.closed and redirect.extracted


# END_BLOCK_REDIRECT_POLICY
