"""The registry host lists keep every input link a source accepted before P1-13.

START_MODULE_CONTRACT
PURPOSE: Prove R-NAV-HOSTS.kept-inputs: one host list per source, no previously accepted input link refused.
SCOPE: mcp_core.sources host lists through the shared matcher kit.url_on_hosts; pure, offline.
DEPENDS: pytest; mcp_core.kit,sources.
LINKS: M-SOURCES; M-KIT; R-NAV-HOSTS.
END_MODULE_CONTRACT
START_MODULE_MAP
test_a_subdomain_link_the_source_accepted_before_still_passes: Mobile/regional links on the source's own domain.
test_a_lookalike_domain_still_fails: The leading-dot entry admits the source's own subdomains only.
END_MODULE_MAP
"""

import pytest
from mcp_core.kit import url_on_hosts
from mcp_core.sources import BY_NAME

# Before P1-13 these five sources accepted an input link on any subdomain of their own domain.
KEPT = [
    ("ozon", "https://m.ozon.ru/product/123456789/"),
    ("ozon", "https://www.ozon.ru/product/123456789/"),
    ("taobao", "https://h5.m.taobao.com/awp/core/detail.htm?id=123456789012"),
    ("taobao", "https://item.taobao.com/item.htm?id=123456789012"),
    ("aliexpress", "https://m.aliexpress.ru/item/1005010003103368.html"),
    ("aliexpress", "https://www.aliexpress.ru/item/1005010003103368.html"),
    ("dns", "https://www.dns-shop.ru/product/abc/"),
    ("dns", "https://spb.dns-shop.ru/product/abc/"),
    ("citilink", "https://www.citilink.ru/product/abc-123456/"),
    ("citilink", "https://m.citilink.ru/product/abc-123456/"),
]


@pytest.mark.req("R-NAV-HOSTS", "R-NAV-HOSTS.kept-inputs")
@pytest.mark.parametrize(("source", "url"), KEPT)
def test_a_subdomain_link_the_source_accepted_before_still_passes(source, url):
    """R-NAV-HOSTS.kept-inputs: a mobile or regional link on the source's own domain is not refused."""
    assert url_on_hosts(url, BY_NAME[source].hosts)


@pytest.mark.req("R-NAV-HOSTS", "R-NAV-HOSTS.kept-inputs")
@pytest.mark.parametrize("source", sorted({source for source, _url in KEPT}))
def test_a_lookalike_domain_still_fails(source):
    """R-NAV-HOSTS.kept-inputs: only the source's own subdomains pass, never a lookalike or a foreign host."""
    domain = next(host for host in BY_NAME[source].hosts if not host.startswith("."))
    for url in (
        f"https://m.{domain}.example.com/x",
        f"https://evil{domain}/x",
        f"https://{domain}@example.com/x",
        f"ftp://m.{domain}/x",
    ):
        assert not url_on_hosts(url, BY_NAME[source].hosts), url
