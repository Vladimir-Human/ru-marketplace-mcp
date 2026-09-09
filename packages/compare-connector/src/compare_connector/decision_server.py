"""Middle-sized DSH profile: comparison plus one deliberate card inspector.

The decision profile keeps the cheap comparison surface and adds a single
source-native card lookup.  This gives an agent enough evidence to inspect a
shortlist without paying for every marketplace tool schema.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from compare_connector import server as compare
from mcp_core.errors import BadRequestError, raise_tool_error
from mcp_core.output_schema import apply_compact_output_schemas

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
    source: Annotated[str, Field(description="Marketplace source from compare_prices, e.g. wildberries or yandex_market.")],
    product_id_or_url: Annotated[str, Field(min_length=1, max_length=400, description="Product identifier or URL from the shortlist.")],
    include_reviews: Annotated[bool, Field(default=False, description="Include source-native reviews when the card supports them.")] = False,
) -> dict[str, Any]:
    """Inspect one shortlisted offer, optionally including its reviews.

    This is intentionally one generic tool: a DSH model can verify the winner
    and seller before spending the context cost of the full marketplace mount.
    """
    name = source.strip().lower()
    if name not in compare._CARD_TOOL_NAMES:
        raise_tool_error(BadRequestError(f"source {source!r} has no supported card inspector"))
    module = compare.SOURCES.get(name)
    if module is None:
        raise_tool_error(BadRequestError(f"source {name!r} is not installed in this decision profile"))
    tool_name = compare._CARD_TOOL_NAMES[name]
    tool = getattr(module, tool_name, None)
    if tool is None:
        raise_tool_error(BadRequestError(f"source {name!r} has no card tool available"))

    if name == "wildberries":
        import re
        digits = re.search(r"\d+", product_id_or_url)
        if digits is None:
            raise_tool_error(BadRequestError("wildberries inspection needs a numeric nm_id"))
        result = await tool(nm_ids=[int(digits.group(0))])
    elif name == "yandex_market":
        result = await tool(product_id=product_id_or_url, include_reviews=include_reviews)
    elif name == "detsky_mir":
        result = await tool(product_id=int(product_id_or_url))
    else:
        argument = {
            "ozon": "sku_or_path", "avito": "item_id_or_url", "taobao": "item_id_or_url",
            "megamarket": "product_id_or_url", "lamoda": "sku_or_url", "dns": "product_url",
            "citilink": "product_url", "aliexpress": "item_id_or_url",
        }[name]
        result = await tool(**{argument: product_id_or_url})
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    return {"source": name, "product_id_or_url": product_id_or_url, "card": payload}


apply_compact_output_schemas(mcp)

