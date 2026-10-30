"""Pages a human opens to pass a browser-backed source's check or log in.

``marketplace-mcp unblock`` only opens pages; the operator passes the check or
enters the login. Nothing here solves, bypasses or automates a site's check,
and no page is fetched by this module.

The map is static data so the CLI and its offline tests can agree on exactly
which browser-backed sources have a page. Every URL is the page the source's
own connector loads for its browser tier (a search page, or its home/login page
when the wall is a login).
"""
# START_MODULE_CONTRACT
# PURPOSE: Static map from each browser-backed doctor source to the page a human opens to unblock it.
# SCOPE: The UNBLOCK_PAGES table; no I/O, no network, no side effects.
# DEPENDS: stdlib typing.
# LINKS: M-UNBLOCK; M-MARKETPLACE; M-CAPABILITIES.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# UNBLOCK_PAGES: Source name -> page URL a human opens in the scraping Chrome.
# END_MODULE_MAP

from __future__ import annotations

UNBLOCK_PAGES: dict[str, str] = {
    # The browser tier opens the site root and fetches the search JSON from
    # inside that page (ozon_connector/server.py:388).
    "ozon": "https://www.ozon.ru/",
    # Same pattern: the items API is fetched from a page opened at the root
    # (avito_connector/server.py:222).
    "avito": "https://www.avito.ru/",
    # The wall is a login, so the human goes straight to the login page
    # (taobao_connector/server.py:18).
    "taobao": "https://login.taobao.com/member/login.jhtml",
    # The wall is an unauthenticated session; the home page carries the login
    # entry point (megamarket_connector/server.py:84,173).
    "megamarket": "https://megamarket.ru/",
    # Search is rendered in Chrome at this URL (lamoda_connector/server.py:444).
    "lamoda": "https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8",
    # The selfcheck renders this search URL (dns_connector/server.py:601).
    "dns": "https://www.dns-shop.ru/search/?q=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA",
    # The selfcheck renders this search URL (citilink_connector/server.py:557).
    "citilink": "https://www.citilink.ru/search/?text=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA",
    # The selfcheck renders this landing page (aliexpress_connector/server.py:100).
    "aliexpress": "https://aliexpress.ru/wholesale?SearchText=%D1%87%D0%B0%D1%81%D1%8B",
    # Search POSTs to api.cian.ru from a page opened at the root
    # (cian_connector/server.py:236).
    "cian": "https://www.cian.ru/",
}
