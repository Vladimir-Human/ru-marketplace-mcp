"""Middle-sized DSH profile: comparison plus one deliberate card inspector.

The decision profile keeps the cheap comparison surface and adds a single
source-native card lookup.  This gives an agent enough evidence to inspect a
shortlist without paying for every marketplace tool schema.

The compare connector mounted here brings its own ``guide://`` resources and the
shared ``marketplace://errors`` reference; this server adds the guide for
``decision_inspect`` and, since it is the entry point of the profile, keeps the
single copy of the shared reference (R-TOOL-RESOURCES).
"""
# START_MODULE_CONTRACT
# PURPOSE: Serve the decision profile: the compare tools plus one card inspector.
# SCOPE: Mount the compare server, add decision_inspect, publish the reference resources.
# DEPENDS: FastMCP; mcp_core output_schema/resources; compare_connector.server.
# LINKS: M-DECISION; M-COMPARE; M-RESOURCES.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# mcp: The decision-profile server object.
# decision_inspect: Read one shortlisted card, optionally with reviews.
# END_MODULE_MAP

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.resources import mount_child, register_reference_resources
from pydantic import Field

from compare_connector import server as compare

mcp = FastMCP(name="decision-connector", version=compare.SERVER_VERSION, instructions=compare.mcp.instructions)
# mount_child, not mount: the compare server registered `marketplace://errors`
# for its own standalone run, and this profile serves that URI once, for itself
# (R-TOOL-RESOURCES).
mount_child(mcp, compare.mcp)


@mcp.tool(
    name="decision_inspect",
    annotations=ToolAnnotations(
        title="Inspect a Shortlisted Marketplace Card",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
async def decision_inspect(
    source: Annotated[
        str, Field(description="Marketplace source from compare_prices, e.g. wildberries or yandex_market.")
    ],
    product_id_or_url: Annotated[
        str, Field(min_length=1, max_length=400, description="Product identifier or URL from the shortlist.")
    ],
    include_reviews: Annotated[
        bool, Field(default=False, description="Include source-native reviews when the card supports them.")
    ] = False,
) -> dict[str, Any]:
    """Inspect one shortlisted offer, optionally including its reviews.

    This is intentionally one generic tool: a DSH model can verify the winner
    and seller before spending the context cost of the full marketplace mount.

    Full field list and error codes: `guide://decision/decision_inspect`.
    """
    name = source.strip().lower()
    payload, _ = await compare._call_card_tool(name, product_id_or_url, include_reviews=include_reviews)
    return {"source": name, "product_id_or_url": product_id_or_url, "card": payload}


apply_compact_output_schemas(mcp)

# The reference half of every tool description is served as an MCP resource
# (guide://<source>/<tool>) and the error taxonomy as marketplace://errors, so
# the agent pays for them when it reads them instead of on every request.
register_reference_resources(mcp)
