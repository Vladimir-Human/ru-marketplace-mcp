"""Static per-source capabilities: access tier, CDP, login and currency.

The table itself is the source registry in ``mcp_core.sources``; this module
re-exports it and keeps the derived predicates. Two callers need it and only
one of them may have side effects:

* the unified server answers ``marketplace_sources`` with it;
* the operator CLI maps a failed selfcheck to one next step with it, and the
  CLI must not import the unified server — that import mounts every connector
  and builds a FastMCP instance, which is a side effect a diagnostic command
  has no business causing.

The registry is plain data in mcp-core for exactly that reason: it can be read
without importing a connector. The data is routing metadata, not live health:
it says what a source *can* do, never whether it works right now. One source
(``effective_capabilities``) also answers what it WILL do: a source whose
route is configured — Wildberries — is browser-backed in DOM mode and not in
API-only mode, and the inventory must say which.
"""
# START_MODULE_CONTRACT
# PURPOSE: Per-source capabilities: access tier, CDP, login, currency, text search.
# SCOPE: Re-exported registry table, the browser-backed predicate and the effective
#        (configuration-aware) capability fields; no health, no network, no side effects.
# DEPENDS: mcp_core.sources; mcp_core.source_selection; stdlib typing.
# LINKS: M-CAPABILITIES; M-SOURCES; M-MARKETPLACE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CAPABILITIES: Per-source access tier, CDP/login needs, currency and text-search support.
# BROWSER_ACCESS_TIERS: Access tiers whose sources can be read through the operator's Chrome.
# browser_backed: True when a source can use the operator's browser at all.
# effective_capabilities: The capability fields of one source with its current configuration.
# END_MODULE_MAP

from __future__ import annotations

from typing import Any

from mcp_core.source_selection import canonical
from mcp_core.sources import (
    BY_NAME,
    ROUTE_API_THEN_DOM,
    ROUTE_DOM,
    source_route,
)
from mcp_core.sources import (
    CAPABILITIES as CAPABILITIES,
)

# Access tiers whose sources can use the operator's Chrome at all. A source
# outside this set is reached over plain HTTP: for it, "open the scraping
# Chrome" is the wrong instruction, and docs/ANTI_BOT.md is the right one.
BROWSER_ACCESS_TIERS = frozenset({"cdp", "graphql_or_cdp", "http_or_cdp"})


def browser_backed(capability: dict[str, Any] | None) -> bool:
    """True when this source can be read through the operator's Chrome.

    ``requires_cdp`` and ``requires_login`` are the two ways a source says
    "a browser is involved"; the access tier covers the sources that can go
    either way (Ozon tier 1/tier 2, Lamoda GraphQL/CDP), and ``may_use_cdp``
    carries the same fact for a source whose browser route is configured
    (Wildberries in DOM mode).
    """
    if not capability:
        return False
    if capability.get("requires_cdp") or capability.get("requires_login"):
        return True
    if capability.get("may_use_cdp"):
        return True
    return str(capability.get("access", "")) in BROWSER_ACCESS_TIERS


def effective_capabilities(name: str, *, route: str | None = None) -> dict[str, Any]:
    """The capability fields of one source with the current configuration.

    Three questions, three answers:

    * ``requires_cdp`` — the browser is unavoidable for this source now;
    * ``may_use_cdp`` — the browser is one of the routes it may take;
    * ``effective_route`` — the route it will actually take now.

    For every source but Wildberries these are static facts of the registry.
    WB's route is configured (``WB_TRANSPORT``, ``WB_DOM_FALLBACK``): in DOM
    mode the browser is the only way in, and in the default API mode WB may
    still fall back to the browser when its API refuses this address. A
    caller that has the source's own settings passes the route it read.
    """
    spec = BY_NAME.get(canonical(name))
    capability = CAPABILITIES.get(canonical(name))
    if spec is None or capability is None or spec.kind != "marketplace":
        return {}
    effective = route or source_route(spec.name)
    requires = bool(capability.get("requires_cdp"))
    # The static fact: the browser is one of this source's routes (its own
    # tier, or a fallback tier). For Wildberries the configured route decides
    # instead of the static tier.
    may_use = browser_backed(capability)
    if spec.name == "wildberries":
        requires = effective == ROUTE_DOM
        may_use = effective in {ROUTE_DOM, ROUTE_API_THEN_DOM}
    return {
        "requires_cdp": requires,
        "may_use_cdp": may_use,
        "effective_route": effective,
    }
