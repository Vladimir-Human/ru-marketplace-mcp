"""Pages a human opens to pass a browser-backed source's check or log in.

``marketplace-mcp unblock`` only opens pages; the operator passes the check or
enters the login. Nothing here solves, bypasses or automates a site's check,
and no page is fetched by this module.

The map is the source registry's ``unblock_page`` facts, re-exported so the
CLI and its offline tests keep reading one name. Every URL is the page the
source's own connector loads for its browser tier (a search page, or its
home/login page when the wall is a login), and only browser-backed sources
carry one — ``test_unblock.py`` pins the map to exactly the doctor sources the
capability table calls browser-backed.
"""
# START_MODULE_CONTRACT
# PURPOSE: Static map from each browser-backed doctor source to the page a human opens to unblock it.
# SCOPE: Re-exported registry table; no I/O, no network, no side effects.
# DEPENDS: mcp_core.sources.
# LINKS: M-UNBLOCK; M-SOURCES; M-MARKETPLACE; M-CAPABILITIES.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# UNBLOCK_PAGES: Source name -> page URL a human opens in the scraping Chrome.
# END_MODULE_MAP

from __future__ import annotations

from mcp_core.sources import UNBLOCK_PAGES as UNBLOCK_PAGES
