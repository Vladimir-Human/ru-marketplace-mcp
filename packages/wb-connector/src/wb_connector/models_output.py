"""Pydantic output models for the WB connector (Stage 2).

Every tool returns a typed Pydantic model. Success responses carry a ``meta``
block (source / healthy / warnings) replacing the old ``_meta`` dict from
``resilience.attach_meta``. No-results is a distinct response type, NOT a
ToolError. Validation/transport/parse failures raise ``ToolError`` via
``raise_tool_error`` with a typed ``ConnectorError`` subclass.
"""

from __future__ import annotations

from typing import Any

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, Field


class MetaOut(MetaOutBase):
    """WB carries the shared envelope unchanged."""


class WbCardItem(BaseModel):
    nm_id: int | None = Field(default=None, description="WB product nmId.")
    name: str = Field(default="", description="Product name (mojibake-decoded).")
    brand: str = Field(default="", description="Brand name (mojibake-decoded).")
    supplier: str = Field(default="", description="Supplier name (mojibake-decoded).")
    supplier_id: int | None = Field(default=None, description="Supplier id.")
    supplier_rating: float | None = Field(default=None, description="Supplier rating.")
    review_rating: float | None = Field(default=None, description="Product review rating.")
    feedbacks: int | None = Field(default=None, description="Total feedback count.")
    total_quantity: int | None = Field(default=None, description="Total stock quantity.")
    in_stock: bool = Field(default=False, description="Whether the product has stock and a valid price.")
    price_rub: float | None = Field(default=None, description="Current price in rubles.")
    price_original_rub: float | None = Field(default=None, description="Strikethrough original price in rubles.")


class WbCardResponse(BaseModel):
    dest: str = Field(default="", description="WB region ID used.")
    count: int = Field(default=0, description="Number of items returned.")
    items: list[WbCardItem] = Field(default_factory=list, description="Product card items.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class WbRootInfoResponse(BaseModel):
    imt_id: int = Field(default=0, description="Root product id (imt_id) shared by all variants.")
    subj_name: str = Field(default="", description="Subject name (mojibake-decoded).")
    subj_root_name: str = Field(default="", description="Root subject name (mojibake-decoded).")
    colors: list[Any] | None = Field(default=None, description="Color names list.")
    compositions: Any | None = Field(default=None, description="Compositions data.")
    options: list[Any] = Field(default_factory=list, description="Product options (max 30).")
    host_used: str = Field(default="", description="Basket CDN host used.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class WbReviewItem(BaseModel):
    rating: int | None = Field(default=None, description="Review rating 1..5.")
    text: str = Field(default="", description="Review text (mojibake-decoded, max 1500 chars).")
    pros: str = Field(default="", description="Pros text (mojibake-decoded, max 500 chars).")
    cons: str = Field(default="", description="Cons text (mojibake-decoded, max 500 chars).")
    user: str = Field(default="", description="Reviewer name (mojibake-decoded, max 60 chars).")
    date: str | None = Field(default=None, description="Review created date.")


class WbReviewsResponse(BaseModel):
    imt_id: int = Field(default=0, description="Root product id (imt_id) the reviews belong to.")
    sort: str = Field(default="recent", description="Client-side sort applied: recent, best, or worst.")
    pool_size: int = Field(default=0, description="Total reviews in the returned pool.")
    feedback_count: int | None = Field(default=None, description="Total feedback count from the API.")
    valuation: Any | None = Field(default=None, description="Overall valuation data.")
    valuation_distribution: Any | None = Field(default=None, description="Star valuation distribution.")
    feedbacks: list[WbReviewItem] = Field(default_factory=list, description="Review items.")
    host_used: str = Field(default="", description="Feedbacks CDN host used.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class WbQuestionItem(BaseModel):
    """One buyer question, with the seller's answer when there is one."""

    question_id: str = Field(default="", description="WB question id.")
    text: str = Field(default="", description="The buyer's question (mojibake-decoded, max 1500 chars).")
    date: str | None = Field(default=None, description="When the question was asked (UTC ISO-8601).")
    user: str = Field(default="", description="Asker's display name (mojibake-decoded, max 60 chars).")
    answered: bool = Field(default=False, description="Whether a non-empty seller answer is present.")
    answer_text: str = Field(default="", description="Seller's answer (mojibake-decoded, max 1500 chars).")
    answer_date: str | None = Field(default=None, description="When the seller answered (UTC ISO-8601).")
    nm_id: int | None = Field(
        default=None,
        description="The specific variant this question was asked on — the pool spans every variant of the imt_id.",
    )


class WbQuestionsResponse(BaseModel):
    imt_id: int = Field(default=0, description="Root product id (imt_id) the questions belong to.")
    total_available: int = Field(default=0, description="Total questions upstream reports for this product.")
    returned: int = Field(default=0, description="Number of questions in this response.")
    skip: int = Field(default=0, description="Offset this page starts at.")
    answered_count: int = Field(default=0, description="How many of the returned questions have a seller answer.")
    has_more: bool = Field(default=False, description="Whether more questions exist past this page.")
    questions: list[WbQuestionItem] = Field(default_factory=list, description="Question items.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class WbSearchResponse(BaseModel):
    query: str = Field(default="", description="Search query text.")
    page: int = Field(default=1, description="Search page number.")
    page_size: int = Field(default=30, description="Items per page.")
    total_ids: int = Field(default=0, description="Total ids returned by search-goods.")
    count: int = Field(default=0, description="Number of enriched items on this page.")
    items: list[WbCardItem] = Field(default_factory=list, description="Enriched product card items.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class WbCategoryProductsResponse(BaseModel):
    """A page of products from one catalog category.

    ``items`` uses the same shape as ``wb_search`` and ``wb_card`` so a category
    walk and a text search can be compared without translating between formats.
    """

    shard: str = Field(default="", description="WB catalog shard used.")
    query: str = Field(default="", description="WB catalog selector used (cat=/subject=).")
    page: int = Field(default=1, description="Page number this response covers.")
    sort: str = Field(default="popular", description="Upstream ordering applied.")
    dest: str = Field(default="", description="WB region id the prices and stock apply to.")
    count: int = Field(default=0, description="Number of products on this page.")
    has_more: bool = Field(
        default=False,
        description="Whether another page likely follows. Inferred from a full page — WB reports no total here.",
    )
    items: list[WbCardItem] = Field(default_factory=list, description="Products in this category page.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class WbNoResultsResponse(BaseModel):
    status: str = Field(default="no_results", description="Response status: no_results (not an error).")
    query: str = Field(default="", description="Search query text.")
    page: int | None = Field(default=None, description="Search page number if applicable.")
    total_ids: int | None = Field(default=None, description="Total ids from search-goods if applicable.")


class WbSelfCheckEntry(SelfCheckEntryBase):
    """WB sub-check entry: adds the baseline-comparison fields WB reports."""

    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class WbSelfCheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    connector: str = Field(default="wb", description="Connector name: wb.")
    checks: dict[str, WbSelfCheckEntry] = Field(default_factory=dict, description="Per-subcheck results.")


class WbSellerResponse(BaseModel):
    """Legal identity behind a WB supplier id.

    WB publishes the registered entity for every seller — this is what lets a
    buyer tell an official brand store from a reseller trading under a similar
    name, and it is the only place the tax ids appear.
    """

    supplier_id: int = Field(default=0, description="WB supplier id (as passed in).")
    name: str = Field(default="", description="Short trading name of the seller.")
    full_name: str = Field(default="", description="Full registered legal-entity name.")
    trademark: str = Field(default="", description="Registered trademark, when the seller declares one.")
    inn: str = Field(default="", description="Russian taxpayer id (INN).")
    kpp: str = Field(default="", description="Tax registration reason code (KPP), RU legal entities only.")
    ogrn: str = Field(default="", description="State registration number (OGRN/OGRNIP).")
    legal_address: str = Field(default="", description="Registered legal address.")
    taxpayer_code: str = Field(default="", description="Taxpayer code — INN for RU, national code for EAEU sellers.")
    foreign_codes: dict[str, str] = Field(
        default_factory=dict,
        description="Non-RU registration codes when present (unp=BY, bin=KZ, unn=other EAEU).",
    )
    host_used: str = Field(default="", description="Static CDN host that served the record.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class WbCategoryNode(BaseModel):
    """One node of the WB catalog tree.

    ``shard`` and ``query`` together form the address of a category feed in WB's
    catalog API, so they are surfaced verbatim: they are what a follow-up
    category listing call needs.
    """

    id: int | None = Field(default=None, description="WB category id.")
    name: str = Field(default="", description="Category display name.")
    url: str = Field(default="", description="Site-relative category URL.")
    shard: str = Field(default="", description="WB catalog shard (needed to query this category's feed).")
    query: str = Field(default="", description="WB catalog query string (cat=/subject= selector).")
    depth: int = Field(default=0, description="Depth in the tree: 0 for a top-level section.")
    children_count: int = Field(default=0, description="Number of direct children.")
    children: list[WbCategoryNode] = Field(default_factory=list, description="Direct children, when expanded.")


class WbCategoriesResponse(BaseModel):
    """A slice of the WB catalog tree.

    The full menu is ~800 KB, which is far too large to hand an agent verbatim,
    so responses are always a bounded slice: either the top level, or the
    subtree under one requested category.
    """

    root: str = Field(default="", description="Requested root: 'top' or the resolved category name.")
    max_depth: int = Field(default=1, description="Depth limit applied to this response.")
    total_returned: int = Field(default=0, description="Total nodes in the returned slice.")
    truncated: bool = Field(default=False, description="Whether the slice was cut short by node limits.")
    items: list[WbCategoryNode] = Field(default_factory=list, description="Category nodes at the requested root.")
    host_used: str = Field(default="", description="Static CDN host that served the menu.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")
