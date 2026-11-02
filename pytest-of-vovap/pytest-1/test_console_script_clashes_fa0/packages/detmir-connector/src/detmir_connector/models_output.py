"""Typed responses for the Detsky Mir connector.

Every field is described because these schemas are what an LLM sees when it
decides whether a tool answers the question in front of it.
"""

from __future__ import annotations

from typing import Any

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, Field


class MetaOut(MetaOutBase):
    """Detsky Mir flags cache hits so a caller can tell fresh data from a replay."""

    cached: bool = Field(default=False, description="Whether this response was served from the in-process TTL cache.")


class DetmirProduct(BaseModel):
    """One Detsky Mir product, flattened from the API's nested payload."""

    product_id: int | None = Field(default=None, description="Numeric product id used by all Detsky Mir endpoints.")
    title: str = Field(default="", description="Product title.")
    article: str = Field(default="", description="Vendor article/model code.")
    brand: str = Field(default="", description="Brand name (first brand when several are listed).")
    price_rub: float | None = Field(
        default=None, description="Current price in roubles. None when absent — never 0 as a stand-in."
    )
    old_price_rub: float | None = Field(
        default=None, description="Pre-discount price in roubles, when the item is on sale."
    )
    discount_percent: int | None = Field(default=None, description="Discount percentage as reported upstream.")
    rating: float | None = Field(default=None, description="Average rating, 1..5.")
    review_count: int | None = Field(default=None, description="Number of reviews.")
    questions_count: int | None = Field(default=None, description="Number of buyer questions.")
    availability: str = Field(default="", description="Upstream availability verdict, e.g. AVAILABLE or NOT_AVAILABLE.")
    available_online: bool | None = Field(default=None, description="Whether the item ships from an online warehouse.")
    available_offline: bool | None = Field(default=None, description="Whether any physical store stocks it.")
    store_count: int | None = Field(default=None, description="How many physical stores report stock.")
    is_marketplace: bool | None = Field(
        default=None, description="True when sold by a third-party marketplace seller, not Detsky Mir itself."
    )
    vendor: str = Field(default="", description="Marketplace seller name, when the item is not sold by Detsky Mir.")
    url: str = Field(default="", description="Canonical product page URL.")
    picture: str = Field(default="", description="Primary product image URL.")


class DetmirCardResponse(BaseModel):
    product: DetmirProduct = Field(default_factory=DetmirProduct, description="The requested product.")
    region: str = Field(default="", description="ISO region the prices and stock apply to.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class DetmirListResponse(BaseModel):
    """A page of products from a category listing or a text search."""

    query: str = Field(default="", description="The category alias or search text that produced this page.")
    mode: str = Field(default="", description="How the page was obtained: 'category' or 'search'.")
    total_available: int | None = Field(default=None, description="Total matches upstream reports, when known.")
    category_title: str = Field(default="", description="Human-readable category name, for category listings.")
    returned: int = Field(default=0, description="Number of items in this page.")
    offset: int = Field(default=0, description="Offset this page starts at.")
    items: list[DetmirProduct] = Field(default_factory=list, description="Products on this page.")
    region: str = Field(default="", description="ISO region the prices and stock apply to.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class DetmirCategory(BaseModel):
    """One node of the Detsky Mir catalog tree."""

    category_id: int | None = Field(default=None, description="Numeric category id.")
    alias: str = Field(default="", description="URL slug — pass this to detmir_category to list its products.")
    title: str = Field(default="", description="Category display title.")
    full_name: str = Field(default="", description="Fully qualified name including parent path, when provided.")
    level: int | None = Field(default=None, description="Depth in the catalog tree (1 = top level).")
    products_count: int | None = Field(default=None, description="How many products upstream reports in this category.")
    parent_id: int | None = Field(default=None, description="Parent category id, when not top level.")
    url: str = Field(default="", description="Canonical category page URL.")


class DetmirCategoriesResponse(BaseModel):
    """A level of the Detsky Mir catalog tree."""

    parent: str = Field(default="", description="Requested parent: 'top' or the parent category id/alias.")
    returned: int = Field(default=0, description="Number of categories returned.")
    total_available: int | None = Field(default=None, description="Total categories at this level, when reported.")
    items: list[DetmirCategory] = Field(default_factory=list, description="Categories at this level.")
    region: str = Field(default="", description="ISO region the listing applies to.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class DetmirSelfcheckEntry(SelfCheckEntryBase):
    detail: str = Field(default="", description="What was observed.")


class DetmirSelfcheckResponse(SelfCheckResponseBase):
    connector: str = Field(default="detmir", description="Connector name.")
    checks: dict[str, DetmirSelfcheckEntry] = Field(default_factory=dict, description="Per-endpoint-family results.")
    cache_stats: dict[str, Any] = Field(default_factory=dict, description="TTL cache counters for this process.")
