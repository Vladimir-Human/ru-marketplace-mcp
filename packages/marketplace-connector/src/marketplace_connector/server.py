"""Unified marketplace MCP server.

One config entry instead of twelve. This server mounts every installed
marketplace connector as a namespaced toolset — ``wb_search``, ``ozon_card``,
``avito_seller`` and the rest keep their names, so client configs and agent
habits carry over untouched, but the operator wires a single ``marketplace``
entry into claude_desktop_config.json instead of one per source.

Each connector is imported defensively: a missing optional dependency (Ozon's
curl_cffi and Playwright, Taobao's Playwright) removes that source's tools
from the set rather than sinking the whole server, and a source whose tools
cannot answer without configuration (MPStats without ``MPSTATS_MP_AUTH``)
stays out too. compare-prices rides along as its own namespace
(``compare_prices``, ``compare_sources``).

The reference half of every mounted tool description — its return-format field
list and error mapping — is not repeated here: each tool's docstring names a
``guide://<source>/<tool>`` resource, and one shared ``marketplace://errors``
resource carries the error taxonomy. Mounted connectors registered that URI for
their own standalone servers, so this one takes it over and the children's
copies are hidden from this listing.

``marketplace_health`` is this server's second own tool: it reads the CDP
endpoint, the navigation budget and the retained-challenge registry without
spending a navigation, so an agent can learn Chrome is down or a host is
cooling down before it plans a fan-out. Nothing in it talks to a marketplace.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""
# START_MODULE_CONTRACT
# PURPOSE: Mount selected marketplace connectors behind one MCP server and answer read-only transport health.
# SCOPE: Preserve native tool names; skip failed/deselected/unconfigured sources; static capabilities; health from local state.
# DEPENDS: FastMCP; Pydantic; mcp_core output_schema/resources/source_selection and, lazily, cdp_budget/browser_handoff/chrome_cdp; fourteen source servers.
# LINKS: M-MARKETPLACE; M-WB; M-OZON; M-YANDEX; M-DETMIR; M-AVITO; M-TAOBAO; M-MEGAMARKET; M-LAMODA; M-DNS; M-CITILINK; M-ALIEXPRESS; M-CIAN; M-COMPARE; M-MPSTATS; M-RESOURCES; M-CAPABILITIES; M-UNBLOCK; M-CDP-BUDGET; M-BROWSER-HANDOFF; M-CHROME-CDP.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# MarketplaceSourcesResponse: Model mounted/skipped sources, cheap routing metadata and the CDP-needing mounts.
# MarketplaceHealthResponse: Model CDP reachability, budget, retained handoffs, unblock pages and server version.
# _mount_all: Import selected connectors and mount their unchanged tool names.
# _mount_unavailable_reason: A connector's own reason it cannot answer until configured.
# _health_part: Run one read-only health producer, keeping a failure inside the answer.
# _cdp_health: Probe the CDP endpoint read-only; a missing optional Playwright is part of the answer.
# _budget_health: Snapshot the shared navigation budget, imported lazily with the browser tier.
# _handoff_health: Diagnose the retained-challenge registry, imported lazily with the browser tier.
# marketplace_sources: Report mounts, skip reasons, capabilities, CDP-needing sources and server version; the reasons are redacted.
# marketplace_health: Report transport health without navigating, autostarting or opening a tab.
# apply_compact_output_schemas: Compact advertised schemas after mounting.
# mount_child: Mount a connector while keeping one owner of the shared error URI.
# register_reference_resources: Publish the guides and the shared error reference.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import ntpath
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp_core.logging import log_event
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.redact import redact_error_text
from mcp_core.resources import mount_child, register_reference_resources
from mcp_core.source_selection import ENV_VAR, canonical, selected, wanted
from pydantic import BaseModel, Field

from marketplace_connector.capabilities import CAPABILITIES, effective_capabilities
from marketplace_connector.unblock import UNBLOCK_PAGES


class MarketplaceSourcesResponse(BaseModel):
    """Which connectors mounted, and why the others did not."""

    mounted: list[str] = Field(default_factory=list, description="Sources whose tools are available in this server.")
    skipped: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Source name mapped to the reason it is unavailable: an import error, a missing "
            "credential its tools need, or an explicit MARKETPLACE_SOURCES deselection."
        ),
    )
    mounted_count: int = Field(default=0, description="How many sources mounted.")
    skipped_count: int = Field(default=0, description="How many sources were skipped.")
    capabilities: dict[str, dict[str, object]] = Field(
        default_factory=dict,
        description=(
            "Static routing metadata: access tier, login/CDP requirement, currency, and whether text search is supported."
        ),
    )
    needs_cdp: list[str] = Field(
        default_factory=list,
        description=(
            "Mounted sources whose reads can go through the operator's Chrome, so they need a "
            "running scraping browser; the rest answer over plain HTTP."
        ),
    )
    server_version: str = Field(default="", description="Unified server version.")


class MarketplaceHealthResponse(BaseModel):
    """Transport health read from local state: no navigation, no tab, no autostart."""

    cdp: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "The scraping Chrome's CDP endpoint as it answers right now: reachable, host, port, "
            "version, page_targets and a short reason or error when something failed."
        ),
    )
    budget: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "The shared CDP navigation budget: limits, in-flight count and per-host breakers with "
            "refusals, open flag and retry_after_s — the remaining pause in seconds."
        ),
    )
    handoffs: dict[str, object] = Field(
        default_factory=dict,
        description="The retained challenge-page registry: retention bounds, lease count and one entry per lease.",
    )
    unblock: dict[str, str] = Field(
        default_factory=dict,
        description="Mounted source mapped to the page a human opens in the scraping Chrome to pass its check or log in.",
    )
    server_version: str = Field(default="", description="Unified server version.")


SERVER_VERSION = "2.9.1"

mcp = FastMCP(
    "marketplace",
    instructions=(
        "All marketplace connectors in one server. Tools keep their per-source "
        "names: wb_*, ozon_*, yandex_*, detmir_*, avito_*, taobao_*, "
        "megamarket_*, lamoda_*, dns_*, citilink_*, aliexpress_*, mpstats_* plus compare_prices and "
        "compare_sources. cian_* is real estate (flats, houses, commercial "
        "property for sale or rent), not goods. Sources whose optional "
        "dependencies are missing, or whose tools cannot answer until a "
        "credential is configured, are simply absent from the set. "
        "Every tool's full field list and error codes are MCP resources (`guide://<source>/<tool>`); all error codes together are `marketplace://errors`."
    ),
    version=SERVER_VERSION,
)


_MOUNTED: list[str] = []
_SKIPPED: dict[str, str] = {}
# The mounted connector modules, by mount name. A mounted source may know its
# own effective route better than the environment does (WB reads its settings
# from a .env file too), and the inventory must follow the mode the tool will
# really use (R11).
_MOUNTED_MODULES: dict[str, object] = {}

# Routing metadata is deliberately static and cheap: it tells a model which
# tool path to attempt before spending a request on a blocked source. It is
# separate from selfcheck/doctor, which performs live network probes.
# The table itself lives in marketplace_connector.capabilities so the operator
# CLI can read it without importing this module (which mounts every connector).


def _mount_unavailable_reason(module: object) -> str | None:
    """A connector's own "I cannot answer yet" reason, or ``None``.

    Optional and generic: a source whose tools need configuration before they
    can do anything exposes ``mount_unavailable_reason() -> str | None``. The
    unified server then records that reason in ``skipped`` instead of mounting
    schemas that would only fail. MPStats is the only source today: without
    ``MPSTATS_MP_AUTH`` every call raises ``auth_missing`` before the wire, so
    unmounted, its two schemas cost the agent nothing (R-MPSTATS-MOUNT-GATE).
    """
    probe = getattr(module, "mount_unavailable_reason", None)
    if probe is None:
        return None
    reason = probe()
    return reason if isinstance(reason, str) else None


def _mount_all() -> None:
    """Import each connector defensively and mount it.

    A connector that fails to import (missing curl_cffi, missing Playwright, a
    broken install) reduces coverage; it must never prevent the unified server
    from starting with the sources that do work. A connector that imports but
    reports no usable configuration (``_mount_unavailable_reason``) is skipped
    the same way.

    What is skipped gets recorded, not just logged. An operator reading tool
    output in a client never sees our stderr, so a silently absent marketplace
    would otherwise look identical to one that returned nothing — see
    ``marketplace_sources``.

    A mounted connector keeps its own per-tool guides, but not its copy of the
    shared error reference: this server owns that URI and registers its own.
    """
    mounts = (
        ("wildberries", "wb_connector.server"),
        ("ozon", "ozon_connector.server"),
        ("yandex", "yandex_connector.server"),
        ("detmir", "detmir_connector.server"),
        ("avito", "avito_connector.server"),
        ("taobao", "taobao_connector.server"),
        ("megamarket", "megamarket_connector.server"),
        ("lamoda", "lamoda_connector.server"),
        ("dns", "dns_connector.server"),
        ("citilink", "citilink_connector.server"),
        ("aliexpress", "aliexpress_connector.server"),
        ("cian", "cian_connector.server"),
        ("compare", "compare_connector.server"),
        ("mpstats", "mpstats_connector.server"),
    )
    chosen = selected()
    for name, module_path in mounts:
        if not wanted(name, chosen):
            # Deselected, not broken. Recorded rather than dropped silently so
            # marketplace_sources can tell "you turned this off" apart from
            # "this failed to import".
            _SKIPPED[name] = f"deselected: not listed in {ENV_VAR}"
            continue
        try:
            module = __import__(module_path, fromlist=["mcp"])
            unavailable = _mount_unavailable_reason(module)
            if unavailable is not None:
                _SKIPPED[name] = redact_error_text(unavailable, 200)
                log_event("marketplace.mount_skipped", source=name, error=redact_error_text(unavailable, 120))
                continue
            # mount_child, not mount: every connector registers the shared
            # `marketplace://errors` for its own standalone server, and this
            # profile must serve that URI once, not once per mounted child
            # (R-TOOL-RESOURCES).
            mount_child(mcp, module.mcp)
            _MOUNTED.append(name)
            _MOUNTED_MODULES[name] = module
        except Exception as exc:
            # The import error is a *success* field of marketplace_sources, not
            # a log line: it is stored in `skipped` and handed to the client.
            # An exception message can carry a token or a proxy credential, so
            # it is scrubbed with the same helper the error and log paths use,
            # before it is stored.
            detail = redact_error_text(f"{type(exc).__name__}: {exc}", 200)
            _SKIPPED[name] = detail
            log_event("marketplace.mount_skipped", source=name, error=redact_error_text(detail, 120))
    log_event("marketplace.mounted", sources=_MOUNTED)


_mount_all()


# START_BLOCK_HEALTH
def _health_part(name: str, producer: Callable[[], dict[str, Any]], fallback: dict[str, Any]) -> dict[str, Any]:
    """Run one read-only health producer; a failure becomes part of the answer.

    A health tool that raises is worse than no health tool: the agent loses the
    whole pre-flight answer because one part broke. ``fallback`` holds the
    fields that part must keep even then (``cdp.reachable=false`` is the one an
    agent branches on). Only the exception class reaches the answer, because a
    message can carry a URL or a profile path; the detail goes to stderr.
    """
    try:
        return producer()
    except Exception as exc:
        log_event("marketplace.health_part_failed", part=name, error=f"{type(exc).__name__}: {str(exc)[:120]}")
        return {**fallback, "error": f"{name} diagnostics unavailable ({type(exc).__name__})"}


def _cdp_health() -> dict[str, Any]:
    """Probe the CDP endpoint read-only; a missing optional Playwright is the answer.

    The import is part of the probe: ``chrome_cdp`` pulls Playwright in at module
    level and the cdp extra is optional, so with no browser tier installed this
    must still answer instead of taking the server down.
    """
    try:
        from mcp_core.transport import chrome_cdp

        return chrome_cdp.cdp_endpoint_status()
    except Exception as exc:
        log_event("marketplace.health_part_failed", part="cdp", error=f"{type(exc).__name__}: {str(exc)[:120]}")
        return {"reachable": False, "error": f"the CDP endpoint could not be read ({type(exc).__name__})"}


def _budget_health() -> dict[str, Any]:
    """The shared navigation budget's diagnostics: breakers, pauses, in-flight count."""
    from mcp_core.transport.cdp_budget import budget_snapshot

    return budget_snapshot()


def _handoff_health() -> dict[str, Any]:
    """The retained-challenge registry's diagnostics; imported lazily with the browser tier.

    A lease carries the scraping profile's absolute path, which holds the OS
    user name, and the challenge URL, whose query can hold a one-time token.
    Planning needs neither, so the answer keeps only the profile folder name
    and the URL without query or fragment.
    """
    from mcp_core.transport.browser_handoff import handoff_diagnostics

    report = handoff_diagnostics()
    for lease in report.get("leases") or ():
        if isinstance(lease, dict):
            if lease.get("profile"):
                lease["profile"] = ntpath.basename(str(lease["profile"]).rstrip("\\/"))
            if lease.get("url"):
                lease["url"] = str(lease["url"]).split("#", 1)[0].split("?", 1)[0]
    return report


# END_BLOCK_HEALTH


def _effective_capabilities(name: str) -> dict[str, Any]:
    """The capability fields of one source, following the mode it runs in.

    A mounted connector knows its own route better than the environment does
    — WB reads ``WB_TRANSPORT``/``WB_DOM_FALLBACK`` through its settings,
    which also come from a .env file — so its answer wins. Without a mounted
    connector the environment decides, which is what the operator CLI does
    too. Nothing here opens a browser or touches the network.
    """
    canonical_name = canonical(name)
    module = _MOUNTED_MODULES.get(name) or _MOUNTED_MODULES.get(canonical_name)
    probe = getattr(module, "effective_route", None)
    route = ""
    if callable(probe):
        try:
            value = probe()
        except Exception:
            value = ""
        route = value if isinstance(value, str) else ""
    return effective_capabilities(canonical_name, route=route or None)


@mcp.tool(
    name="marketplace_sources",
    annotations=ToolAnnotations(
        title="Which Marketplaces Are Loaded",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
async def marketplace_sources() -> MarketplaceSourcesResponse:
    """List which connectors this unified server actually mounted.

    ## Why this exists

    Connectors are imported defensively, so a missing dependency removes a
    marketplace instead of killing the server. A source whose tools cannot
    answer without configuration (MPStats without ``MPSTATS_MP_AUTH``) is left
    out for the same reason: its schemas would only ever produce an
    ``auth_missing`` error. That is the right failure mode, but it is invisible
    from the client: absent tools look the same as a source that simply found
    nothing. Call this before concluding a marketplace has no results — if it
    is in ``skipped``, it was never queried at all.

    Full field list and error codes: `guide://marketplace/marketplace_sources`.
    """
    return MarketplaceSourcesResponse(
        mounted=sorted(_MOUNTED),
        # Scrubbed again on the way out: `skipped` is a success answer, and a
        # reason recorded elsewhere must not become a leak just because it
        # reached this dict without the helper.
        skipped={name: redact_error_text(reason, 200) for name, reason in sorted(_SKIPPED.items())},
        mounted_count=len(_MOUNTED),
        skipped_count=len(_SKIPPED),
        capabilities={
            name: {**metadata, **_effective_capabilities(name), "mounted": name in {canonical(m) for m in _MOUNTED}}
            for name, metadata in CAPABILITIES.items()
        },
        # The browser is involved for every mounted source whose effective
        # route uses it — including Wildberries in DOM mode and in the default
        # API mode with the DOM fallback on. The old list was built from the
        # static tier only, so a configured WB said "no browser needed" right
        # before wb_search went through CDP (R11).
        needs_cdp=sorted(name for name in _MOUNTED if _effective_capabilities(name).get("may_use_cdp") is True),
        server_version=SERVER_VERSION,
    )


@mcp.tool(
    name="marketplace_health",
    annotations=ToolAnnotations(
        title="Transport Health Before a Fan-Out",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
async def marketplace_health() -> MarketplaceHealthResponse:
    """Report transport health from local state, spending no navigation.

    Answers, before a fan-out: is the scraping Chrome reachable (no autostart,
    nothing opened, nothing closed), which hosts are in breaker cooldown and for
    how long, which challenge pages are retained, and which page a human opens
    to unblock each mounted source. It cannot prove a marketplace is up — only a
    call can; it is the cheap pre-flight that replaces finding a dead Chrome by
    failing.

    Full field list and error codes: `guide://marketplace/marketplace_health`.
    """
    # The CDP probe is two blocking HTTP GETs (up to ~2 s each against a dead
    # port), so it runs off the event loop like the other /json reads do.
    cdp = await asyncio.to_thread(_health_part, "cdp", _cdp_health, {"reachable": False})
    return MarketplaceHealthResponse(
        cdp=cdp,
        budget=_health_part("budget", _budget_health, {}),
        handoffs=_health_part("handoffs", _handoff_health, {}),
        unblock={source: UNBLOCK_PAGES[source] for source in sorted(_MOUNTED) if source in UNBLOCK_PAGES},
        server_version=SERVER_VERSION,
    )


# Advertised output schemas are the dominant constant cost of an MCP mount:
# replace the full Pydantic tree with top-level field names (~64 % fewer
# wire tokens on the unified server).
apply_compact_output_schemas(mcp)

# The reference half of every tool description is served as an MCP resource
# (guide://<source>/<tool>) and the error taxonomy as marketplace://errors, so
# the agent pays for them when it reads them instead of on every request.
register_reference_resources(mcp)
