"""Unified marketplace MCP server.

One config entry instead of twelve. This server mounts every installed
marketplace connector as a namespaced toolset — ``wb_search``, ``ozon_card``,
``avito_seller`` and the rest keep their names, so client configs and agent
habits carry over untouched, but the operator wires a single ``marketplace``
entry into claude_desktop_config.json instead of one per source.

Each connector is imported defensively: a missing optional dependency (Ozon's
curl_cffi and Playwright, Taobao's Playwright) removes that source's tools
from the set rather than sinking the whole server. compare-prices rides along
as its own namespace (``compare_prices``, ``compare_sources``).

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""

from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp_core.logging import log_event
from pydantic import BaseModel, Field


class MarketplaceSourcesResponse(BaseModel):
    """Which connectors mounted, and why the others did not."""

    mounted: list[str] = Field(default_factory=list, description="Sources whose tools are available in this server.")
    skipped: dict[str, str] = Field(
        default_factory=dict,
        description="Source name mapped to the import error that removed it — usually a missing dependency.",
    )
    mounted_count: int = Field(default=0, description="How many sources mounted.")
    skipped_count: int = Field(default=0, description="How many sources were skipped.")
    server_version: str = Field(default="", description="Unified server version.")


SERVER_VERSION = "1.3.1"

mcp = FastMCP(
    "marketplace",
    instructions=(
        "All marketplace connectors in one server. Tools keep their per-source "
        "names: wb_*, ozon_*, yandex_*, detmir_*, avito_*, taobao_*, "
        "megamarket_*, lamoda_*, dns_*, citilink_*, mpstats_* plus compare_prices and "
        "compare_sources. Sources whose optional dependencies are missing are "
        "simply absent from the set."
    ),
    version=SERVER_VERSION,
)


_MOUNTED: list[str] = []
_SKIPPED: dict[str, str] = {}


def _mount_all() -> None:
    """Import each connector defensively and mount it.

    A connector that fails to import (missing curl_cffi, missing Playwright, a
    broken install) reduces coverage; it must never prevent the unified server
    from starting with the sources that do work.

    What is skipped gets recorded, not just logged. An operator reading tool
    output in a client never sees our stderr, so a silently absent marketplace
    would otherwise look identical to one that returned nothing — see
    ``marketplace_sources``.
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
        ("compare", "compare_connector.server"),
        ("mpstats", "mpstats_connector.server"),
    )
    for name, module_path in mounts:
        try:
            module = __import__(module_path, fromlist=["mcp"])
            mcp.mount(module.mcp)
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
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def marketplace_sources() -> MarketplaceSourcesResponse:
    """List which connectors this unified server actually mounted.

    ## Why this exists

    Connectors are imported defensively, so a missing dependency removes a
    marketplace instead of killing the server. That is the right failure mode,
    but it is invisible from the client: absent tools look the same as a source
    that simply found nothing. Call this before concluding a marketplace has no
    results — if it is in ``skipped``, it was never queried at all.

    ## Return Format

    MarketplaceSourcesResponse: {mounted, skipped, mounted_count, skipped_count,
    server_version}. ``skipped`` maps source name to the import error that
    removed it, which is usually a missing optional dependency.

    ## Error Format

    Never raises ToolError: pure introspection of the mounted connectors — a
    failed import is recorded in ``skipped`` instead of being raised.
    """
    return MarketplaceSourcesResponse(
        mounted=sorted(_MOUNTED),
        skipped=dict(sorted(_SKIPPED.items())),
        mounted_count=len(_MOUNTED),
        skipped_count=len(_SKIPPED),
        server_version=SERVER_VERSION,
    )
