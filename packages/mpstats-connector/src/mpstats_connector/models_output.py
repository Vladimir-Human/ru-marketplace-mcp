"""Pydantic output models for the MPStats connector.

Every tool returns a typed Pydantic model. Success responses carry a ``meta``
block (source / healthy / warnings) from the shared runtime. A missing value is
``None``, never ``0``: a zero orders count would rank a dead listing as the
top seller, which is the exact bug class these models exist to prevent.

The MPStats plugin API is a single RPC endpoint (``POST plugin.mpstats.io/pluginapi``)
that answers analytics for one or many SKUs at once. The two tool families here
mirror the two request shapes that carry real data:

* ``mpstats_item`` — per-SKU analytics: 30-day graphs for orders, prices, stock,
  sales totals, seller/brand identity.
* ``mpstats_warehouses`` — per-SKU warehouse stock split (FBS vs FBO) with the
  upstream ``last_update`` timestamp.

Graphs are length ``days`` (default 30, oldest-first). Each day index maps to a
calendar day ending at ``last_update``; a zero means "no data for that day", not
"the value was zero". Callers comparing totals across SKUs should sum the graphs,
not read a single cell.
"""

from __future__ import annotations

from typing import Any

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, Field


class MetaOut(MetaOutBase):
    """MPStats carries the shared envelope unchanged."""


class MpStatsTotals(BaseModel):
    """Aggregated sales totals over the ``days`` window for one SKU.

    ``sum`` is revenue in rubles, ``orders`` is unit count. ``sum_prev`` is the
    prior window's revenue, used to gauge trend — both stay ``None`` when MPStats
    has no data, never ``0``.
    """

    orders: int | None = Field(default=None, description="Total orders over the window.")
    sum: float | None = Field(default=None, description="Total revenue in rubles over the window.")
    sum_prev: float | None = Field(default=None, description="Prior window revenue in rubles (trend reference).")


class MpStatsItem(BaseModel):
    """Per-SKU analytics from the MPStats plugin API.

    The four ``*_graph`` lists are length ``days`` (oldest-first); a zero entry
    means "no data for that day", not "the value was zero". ``orders_per_day`` is
    the rolling average MPStats computes; compare it to summing ``orders_graph``
    only after confirming the window length.

    ``price_avg_rub`` is the last non-zero price-graph value — the current selling
    price, since the graph's final cell is "today". ``stock_now`` is the final
    ``count_graph`` cell. Both are ``None`` when the graph is empty or all-zero,
    so a delisted item never reports a false price/stock.
    """

    sku: int | None = Field(default=None, description="The SKU this analytics row belongs to.")
    place: str = Field(default="", description="Marketplace the SKU lives on: ozon or wildberries.")
    seller: str = Field(default="", description="Seller display name.")
    seller_id: int | None = Field(default=None, description="Seller id.")
    brand: str = Field(default="", description="Brand name (empty when MPStats has none).")
    stock_now: int | None = Field(
        default=None, description="Current stock (final count-graph cell), or None if unknown."
    )
    price_avg_rub: float | None = Field(
        default=None, description="Current price in rubles (last non-zero price-graph cell), or None."
    )
    orders_per_day: float | None = Field(default=None, description="Rolling orders-per-day average MPStats computes.")
    days_on_stocks: int | None = Field(default=None, description="Days the SKU has been on stock.")
    totals: MpStatsTotals = Field(default_factory=MpStatsTotals, description="Aggregated sales totals over the window.")
    orders_graph: list[int] = Field(default_factory=list, description="Per-day orders over the window (length=days).")
    prices_graph: list[int] = Field(
        default_factory=list, description="Per-day price in rubles over the window (length=days)."
    )
    count_graph: list[int] = Field(
        default_factory=list, description="Per-day stock count over the window (length=days)."
    )
    rubrics_graph: list[int] = Field(
        default_factory=list, description="Per-day rubric/category-position count over the window (length=days)."
    )


class MpStatsItemResponse(BaseModel):
    place: str = Field(default="", description="Marketplace queried: ozon or wildberries.")
    days: int = Field(default=30, description="Window length in days the graphs span.")
    count: int = Field(default=0, description="Number of SKU analytics rows returned.")
    items: list[MpStatsItem] = Field(
        default_factory=list, description="Per-SKU analytics rows, keyed by the requested SKU order."
    )
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class MpStatsStocks(BaseModel):
    """Warehouse stock split for one SKU.

    ``fbs`` is the Fulfilled-by-Seller stock count; ``fbo`` is the
    Fulfilled-by-Operator (marketplace warehouse) stock. MPStats returns ``fbo``
    as a list of per-warehouse entries when populated and an empty list otherwise;
    we collapse it to a total count for the agent-facing view and keep the raw
    list under ``fbo_warehouses`` for callers who need per-warehouse detail.
    """

    fbs: int | None = Field(default=None, description="FBS (seller warehouse) stock count.")
    fbo: int | None = Field(default=None, description="Total FBO (marketplace warehouse) stock count.")
    fbo_warehouses: list[Any] = Field(
        default_factory=list, description="Raw per-warehouse FBO entries when MPStats populates them."
    )
    last_update: str | None = Field(default=None, description="Upstream last-update timestamp (server-localised).")


class MpStatsWarehousesItem(BaseModel):
    sku: int | None = Field(default=None, description="The SKU this stock row belongs to.")
    stocks: MpStatsStocks = Field(default_factory=MpStatsStocks, description="Stock split for this SKU.")


class MpStatsWarehousesResponse(BaseModel):
    place: str = Field(default="", description="Marketplace queried: ozon or wildberries.")
    days: int = Field(default=30, description="Window length in days the stock snapshot spans.")
    count: int = Field(default=0, description="Number of SKU stock rows returned.")
    items: list[MpStatsWarehousesItem] = Field(
        default_factory=list, description="Per-SKU stock rows, keyed by the requested SKU order."
    )
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class MpStatsNoResultsResponse(BaseModel):
    status: str = Field(default="no_results", description="Response status: no_results (not an error).")
    place: str = Field(default="", description="Marketplace queried.")
    skus: list[int] = Field(default_factory=list, description="SKUs for which MPStats returned no analytics.")


class MpStatsSelfCheckEntry(SelfCheckEntryBase):
    """MPStats sub-check entry: adds the baseline-comparison fields MPStats reports."""

    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class MpStatsSelfCheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    connector: str = Field(default="mpstats", description="Connector name: mpstats.")
    checks: dict[str, MpStatsSelfCheckEntry] = Field(default_factory=dict, description="Per-subcheck results.")
