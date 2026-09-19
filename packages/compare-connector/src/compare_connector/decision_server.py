"""Middle-sized DSH profile: comparison plus one deliberate card inspector.

The decision profile keeps the cheap comparison surface and adds a single
source-native card lookup.  This gives an agent enough evidence to inspect a
shortlist without paying for every marketplace tool schema.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp_core.output_schema import apply_compact_output_schemas
from pydantic import Field

from compare_connector import server as compare

mcp = FastMCP(name="decision-connector", version=compare.SERVER_VERSION)
mcp.mount(compare.mcp)


@mcp.tool(
    name="decision_inspect",
    annotations=ToolAnnotations(
        title="Inspect a Shortlisted Marketplace Card",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
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
    """
    name = source.strip().lower()
    payload, _ = await compare._call_card_tool(name, product_id_or_url, include_reviews=include_reviews)
    return {"source": name, "product_id_or_url": product_id_or_url, "card": payload}


apply_compact_output_schemas(mcp)
