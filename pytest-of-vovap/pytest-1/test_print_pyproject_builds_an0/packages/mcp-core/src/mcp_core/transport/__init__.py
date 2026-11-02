"""Transport tiers shared by every connector.

Two tiers, tried in cost order:

**Tier 1 — anonymous HTTP** (``http_tier``). Plain HTTPS with polite rate
limiting, capped bodies, and transport-only retries. Zero setup; works wherever
the marketplace serves its catalog endpoints to ordinary clients.

**Tier 2 — authenticated browser** (``chrome_cdp``). Fetches run inside a Chrome
instance the operator started and logged into themselves. This is the answer for
sources that reject datacenter TLS fingerprints or gate reads behind a session,
and it is why the project needs no stored credentials.

``chrome_cdp`` is imported lazily: Playwright is an optional dependency, so a
connector that only needs tier 1 never pays for it.
"""
# START_MODULE_CONTRACT
# PURPOSE: Publish anonymous HTTP and shared navigation-budget primitives.
# SCOPE: Transport facade; Chrome/Playwright is not eagerly imported here.
# DEPENDS: mcp_core.transport.http_tier,cdp_budget; httpx transitively.
# LINKS: M-TRANSPORT; M-HTTP-TIER; M-CDP-BUDGET; M-CHROME-CDP.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# __all__: Export HTTP clients, capped readers, gates and navigation-budget APIs.
# END_MODULE_MAP

from __future__ import annotations

from mcp_core.transport.cdp_budget import (
    HostRefusingError,
    NavigationBudget,
    budget_snapshot,
    navigation_budget,
)
from mcp_core.transport.http_tier import (
    DEFAULT_USER_AGENT,
    RETRYABLE_STATUSES,
    BodyTooLargeError,
    PoliteGate,
    RateLimiter,
    build_client,
    get_text_budgeted,
    get_text_with_retries,
    proxy_from_env,
    read_capped_text,
)

__all__ = [
    "DEFAULT_USER_AGENT",
    "RETRYABLE_STATUSES",
    "BodyTooLargeError",
    "HostRefusingError",
    "NavigationBudget",
    "PoliteGate",
    "RateLimiter",
    "budget_snapshot",
    "build_client",
    "get_text_budgeted",
    "get_text_with_retries",
    "navigation_budget",
    "proxy_from_env",
    "read_capped_text",
]
