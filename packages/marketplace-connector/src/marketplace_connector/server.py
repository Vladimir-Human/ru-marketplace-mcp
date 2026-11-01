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

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""
# START_MODULE_CONTRACT
# PURPOSE: Mount selected marketplace connectors behind one MCP server.
# SCOPE: Preserve native tool names; skip failed/deselected/unconfigured sources; static capabilities.
# DEPENDS: FastMCP; Pydantic; mcp_core output_schema/resources/source_selection; fourteen source servers.
# LINKS: M-MARKETPLACE; M-WB; M-OZON; M-YANDEX; M-DETMIR; M-AVITO; M-TAOBAO; M-MEGAMARKET; M-LAMODA; M-DNS; M-CITILINK; M-ALIEXPRESS; M-CIAN; M-COMPARE; M-MPSTATS; M-RESOURCES.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# MarketplaceSourcesResponse: Model mounted/skipped sources and cheap routing metadata.
# _mount_all: Import selected connectors and mount their unchanged tool names.
# _mount_unavailable_reason: A connector's own reason it cannot answer until configured.
# marketplace_sources: Report mounts, skip reasons, capabilities and server version.
# apply_compact_output_schemas: Compact advertised schemas after mounting.
# mount_child: Mount a connector while keeping one owner of the shared error URI.
# register_reference_resources: Publish the guides and the shared error reference.
# END_MODULE_MAP

from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp_core.logging import log_event
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.resources import mount_child, register_reference_resources
from mcp_core.source_selection import ENV_VAR, canonical, selected, wanted
from pydantic import BaseModel, Field

from marketplace_connector.capabilities import CAPABILITIES


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
    server_version: str = Field(default="", description="Unified server version.")


SERVER_VERSION = "2.7.0"

mcp = FastMCP(
    "marketplace",
    instructions=(
        "All marketplace connectors in one server. Tools keep their per-source "
        "names: wb_*, ozon_*, yandex_*, detmir_*, avito_*, taobao_*, "
        "megamarket_*, lamoda_*, dns_*, citilink_*, aliexpress_*, mpstats_* plus compare_prices and "
        "compare_sources. cian_* is real estate (flats, houses, commercial "
        "property for sale or rent), not goods. Sources whose optional "
        "dependencies are missing, or whose tools cannot answer until a "
        "credential is configured, are simply absent from the set."
        "Every tool's full field list and error codes are MCP resources (`guide://<source>/<tool>`); all error codes together are `marketplace://errors`."
    ),
    version=SERVER_VERSION,
)


_MOUNTED: list[str] = []
_SKIPPED: dict[str, str] = {}

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
                _SKIPPED[name] = unavailable
                log_event("marketplace.mount_skipped", source=name, error=unavailable[:120])
                continue
            # mount_child, not mount: every connector registers the shared
            # `marketplace://errors` for its own standalone server, and this
            # profile must serve that URI once, not once per mounted child
            # (R-TOOL-RESOURCES).
            mount_child(mcp, module.mcp)
            _MOUNTED.append(name)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            _SKIPPED[name] = detail[:200]
            log_event("marketplace.mount_skipped", source=name, error=detail[:120])
    log_event("marketplace.mounted", sources=_MOUNTED)


_mount_all()


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
        skipped=dict(sorted(_SKIPPED.items())),
        mounted_count=len(_MOUNTED),
        skipped_count=len(_SKIPPED),
        capabilities={
            name: {**metadata, "mounted": name in {canonical(m) for m in _MOUNTED}}
            for name, metadata in CAPABILITIES.items()
        },
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
