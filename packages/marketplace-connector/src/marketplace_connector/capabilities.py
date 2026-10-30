"""Static per-source capabilities: access tier, CDP, login and currency.

This table is plain data, so it lives in its own module instead of inside
``server.py``. Two callers need it and only one of them may have side
effects:

* the unified server answers ``marketplace_sources`` with it;
* the operator CLI maps a failed selfcheck to one next step with it, and the
  CLI must not import the unified server — that import mounts every connector
  and builds a FastMCP instance, which is a side effect a diagnostic command
  has no business causing.

The data is routing metadata, not live health: it says what a source *can*
do, never whether it works right now.
"""
# START_MODULE_CONTRACT
# PURPOSE: Static per-source capabilities — access tier, CDP, login, currency, text search.
# SCOPE: The routing table and the browser-backed predicate; no health, no network, no side effects.
# DEPENDS: stdlib typing.
# LINKS: M-CAPABILITIES; M-MARKETPLACE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CAPABILITIES: Per-source access tier, CDP/login needs, currency and text-search support.
# BROWSER_ACCESS_TIERS: Access tiers whose sources can be read through the operator's Chrome.
# browser_backed: True when a source can use the operator's browser at all.
# END_MODULE_MAP

from __future__ import annotations

from typing import Any

CAPABILITIES: dict[str, dict[str, Any]] = {
    "wildberries": {
        "access": "anonymous_http",
        "requires_cdp": False,
        "requires_login": False,
        "currency": "rub",
        "text_search": True,
    },
    "ozon": {
        "access": "http_or_cdp",
        "requires_cdp": False,
        "requires_login": False,
        "currency": "rub",
        "text_search": True,
    },
    "yandex_market": {
        "access": "anonymous_http",
        "requires_cdp": False,
        "requires_login": False,
        "currency": "rub",
        "text_search": True,
    },
    "detsky_mir": {
        "access": "anonymous_http",
        "requires_cdp": False,
        "requires_login": False,
        "currency": "rub",
        "text_search": False,
    },
    "avito": {"access": "cdp", "requires_cdp": True, "requires_login": False, "currency": "rub", "text_search": True},
    "taobao": {"access": "cdp", "requires_cdp": True, "requires_login": True, "currency": "cny", "text_search": True},
    "megamarket": {
        "access": "cdp",
        "requires_cdp": True,
        "requires_login": True,
        "currency": "rub",
        "text_search": True,
    },
    "lamoda": {
        "access": "graphql_or_cdp",
        "requires_cdp": False,
        "requires_login": False,
        "currency": "rub",
        "text_search": True,
    },
    "dns": {"access": "cdp", "requires_cdp": True, "requires_login": False, "currency": "rub", "text_search": True},
    "citilink": {
        "access": "cdp",
        "requires_cdp": True,
        "requires_login": False,
        "currency": "rub",
        "text_search": True,
    },
    "aliexpress": {
        "access": "cdp",
        "requires_cdp": True,
        "requires_login": False,
        "currency": "rub",
        "text_search": True,
    },
    "mpstats": {
        "access": "api_token",
        "requires_cdp": False,
        "requires_login": False,
        "currency": "rub",
        "text_search": False,
    },
    # Real estate, not goods: search is by filters (deal, type, region, rooms,
    # price, area), never by text, and it takes no part in compare_prices.
    "cian": {"access": "cdp", "requires_cdp": True, "requires_login": False, "currency": "rub", "text_search": False},
}

# Access tiers whose sources can use the operator's Chrome at all. A source
# outside this set is reached over plain HTTP: for it, "open the scraping
# Chrome" is the wrong instruction, and docs/ANTI_BOT.md is the right one.
BROWSER_ACCESS_TIERS = frozenset({"cdp", "graphql_or_cdp", "http_or_cdp"})


def browser_backed(capability: dict[str, Any] | None) -> bool:
    """True when this source can be read through the operator's Chrome.

    ``requires_cdp`` and ``requires_login`` are the two ways a source says
    "a browser is involved"; the access tier covers the sources that can go
    either way (Ozon tier 1/tier 2, Lamoda GraphQL/CDP).
    """
    if not capability:
        return False
    if capability.get("requires_cdp") or capability.get("requires_login"):
        return True
    return str(capability.get("access", "")) in BROWSER_ACCESS_TIERS
