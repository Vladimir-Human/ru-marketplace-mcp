"""Render-json source policy must reach owned navigation and reject foreign redirects.

START_MODULE_CONTRACT
PURPOSE: Prove R-NAV-HOSTS.invariant for kit's render-json navigation shell.
SCOPE: Real kit and open_page; only page lifecycle and navigation permits are fake.
DEPENDS: pytest; mcp_core.kit,sources,transport.chrome_cdp; dns_connector.server.
LINKS: M-KIT; M-CHROME-CDP; R-NAV-HOSTS.invariant.
END_MODULE_CONTRACT
START_MODULE_MAP
navigation: Supply a final URL without browser I/O and record the explicit source policy.
test_render_json_source_passes_policy_and_refuses_foreign_final_url: Exercise DNS's real render shell.
END_MODULE_MAP
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from dns_connector import server as dns
from mcp_core.sources import BY_NAME
from mcp_core.transport import chrome_cdp

# START_BLOCK_NAVIGATION_FAKE


@pytest.fixture
def navigation(monkeypatch):
    state = SimpleNamespace(final="https://example.com/x", policy=None, closed=False, extracted=False, permits=[])
    real_open_page = chrome_cdp.open_page

    @asynccontextmanager
    async def observed_open_page(url, *args, **kwargs):
        state.policy = kwargs.get("allowed_hosts")
        async with real_open_page(url, *args, **kwargs) as page:
            yield page

    @asynccontextmanager
    async def page_lifecycle(url, wait_ms, **kwargs):
        async def evaluate(expression):
            state.extracted = True
            return "{}"

        try:
            yield SimpleNamespace(url=state.final, evaluate=evaluate)
        finally:
            state.closed = True

    async def acquire(host):
        state.permits.append(host)
        return SimpleNamespace(ok=lambda: None, neutral=lambda: None, release=lambda: None)

    async def no_wait():
        pass

    monkeypatch.setattr(chrome_cdp, "open_page", observed_open_page)
    monkeypatch.setattr(chrome_cdp, "_playwright_page", page_lifecycle)
    monkeypatch.setattr(chrome_cdp, "_acquire_navigation_permit", acquire)
    monkeypatch.setattr(dns, "_polite_wait", no_wait)
    return state


# END_BLOCK_NAVIGATION_FAKE
# START_BLOCK_REDIRECT_POLICY


@pytest.mark.req("R-NAV-HOSTS.invariant")
async def test_render_json_source_passes_policy_and_refuses_foreign_final_url(navigation):
    with pytest.raises(chrome_cdp.NavigationPolicyError) as exc:
        await dns._cdp_render("https://www.dns-shop.ru/search/?q=laptop", "() => '{}'", 0, None)
    assert exc.value.final_url == navigation.final
    assert navigation.closed
    assert not navigation.extracted
    # Old code already rejects this redirect using the initial host, but does
    # not propagate DNS's policy: this assertion is the old-code red proof.
    assert navigation.policy, "render_json omitted the source host policy"
    assert set(navigation.policy) == set(BY_NAME["dns"].hosts)


@pytest.mark.req("R-NAV-HOSTS.invariant")
async def test_render_json_source_accepts_its_registered_redirect(navigation):
    navigation.final = "https://dns-shop.ru/search/?q=laptop"
    assert await dns._cdp_render("https://www.dns-shop.ru/search/?q=laptop", "() => '{}'", 0, None) == {}
    assert navigation.closed and navigation.extracted


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize("hosts", [(), None, ("www.dns-shop.ru",)])
async def test_open_page_refuses_foreign_input_before_browser_or_permit(navigation, hosts):
    with pytest.raises(chrome_cdp.NavigationPolicyError):
        async with chrome_cdp.open_page("https://example.com/x", allowed_hosts=hosts):
            pytest.fail("foreign input was yielded")
    assert not navigation.permits
    assert not navigation.closed and not navigation.extracted


# END_BLOCK_REDIRECT_POLICY
