"""Cross-marketplace price comparison.

The other connectors each answer "what does this cost on X". This one answers the
question people actually ask — "where is this cheapest right now" — by querying
every installed marketplace concurrently and normalising the answers into one
ranked list.

Three design decisions worth stating, because they are what make the output
trustworthy:

**Partial results beat no results.** Russian marketplaces fail independently and
often: Ozon rejects datacenter IPs, Yandex occasionally answers 302, Detsky Mir
emits sporadic 502s. One source failing must never sink the comparison, so every
source is queried in parallel and its outcome reported per-source. A caller can
always see which marketplaces answered and which did not.

**Subscription prices are never silently compared against everyday prices.**
Yandex Market leads with a Plus-subscriber price 25-30% below the everyday one.
Ranking that against a Wildberries price would fabricate a bargain. Comparison
uses everyday prices; subscriber prices ride along in a separate field.

**Sources are optional at import time.** Ozon pulls in curl_cffi and Playwright,
which many users do not want. Each connector is imported defensively, and the
comparison runs with whatever is present.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import re
import time
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from mcp_core.errors import BadRequestError, raise_tool_error
from mcp_core.logging import log_event
from mcp_core.redact import redact_error_text as _redact
from pydantic import Field

from compare_connector.models_output import (
    CompareResponse,
    MarketOffer,
    SourceOutcome,
)

SERVER_VERSION = "1.1.0"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

# Per-source ceiling. Yandex pages are ~2 MB and WB search occasionally stalls, so
# a slow source must not hold the whole comparison hostage.
SOURCE_TIMEOUT_S = float(os.environ.get("COMPARE_SOURCE_TIMEOUT", "45"))

mcp = FastMCP(name="compare-connector", version=SERVER_VERSION)


def _available_sources() -> dict[str, Any]:
    """Import each marketplace connector defensively.

    A missing optional dependency (Ozon's curl_cffi/Playwright) or a broken
    install reduces coverage; it must not prevent the server from starting.
    """
    sources: dict[str, Any] = {}

    try:
        from wb_connector import server as wb_server

        sources["wildberries"] = wb_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="wildberries", error=_redact(str(exc))[:120])

    try:
        from yandex_connector import server as yandex_server

        sources["yandex_market"] = yandex_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="yandex_market", error=_redact(str(exc))[:120])

    try:
        from detmir_connector import server as detmir_server

        sources["detsky_mir"] = detmir_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="detsky_mir", error=_redact(str(exc))[:120])

    try:
        from ozon_connector import server as ozon_server

        sources["ozon"] = ozon_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="ozon", error=_redact(str(exc))[:120])

    return sources


SOURCES = _available_sources()

# Marketplaces that support a text query. Detsky Mir is absent on purpose: its
# API has no working text search (see the detmir connector's module docstring),
# so including it would mean returning products unrelated to the query.
SEARCHABLE = ("wildberries", "yandex_market", "ozon")


def _wb_product_url(nm_id: object) -> str:
    return f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" if nm_id else ""


# Ozon's search tiles carry display text, not numbers: "1 234 ₽" for a price and
# "4,8" for a rating, with non-breaking and narrow no-break spaces as thousands
# separators and a comma decimal mark.
_NUMERIC_JUNK_RE = re.compile(r"[^\d,.\-]")


def _as_price(value: object) -> float | None:
    """Coerce a marketplace price into a float, or ``None`` when there isn't one.

    Never returns 0.0 as a stand-in. A zero would rank a listing with no live
    offer as the cheapest result, which is the one outcome a price comparison must
    never produce.

    Handles Ozon's rouble display strings — "1 234 ₽", "1 234,50 ₽" — where the
    separators include U+00A0 and U+202F. ``MarketOffer.price_rub`` is typed
    ``float | None``, so passing the raw string through would raise a pydantic
    validation error and take down the whole Ozon source.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    if not isinstance(value, str):
        return None
    cleaned = _NUMERIC_JUNK_RE.sub("", value)
    if not cleaned:
        return None
    # A comma is a decimal mark here, not a thousands separator: Ozon writes
    # "1 234,50", never "1,234.50".
    cleaned = cleaned.replace(",", ".")
    if cleaned.count(".") > 1:
        head, _, tail = cleaned.rpartition(".")
        cleaned = head.replace(".", "") + "." + tail
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return parsed or None


def _as_count(value: object) -> int | None:
    """Coerce a rating/review count, tolerating "24 086 отзывов"-style text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _stock_from_label(value: object) -> bool | None:
    """Read Ozon's stock hint, e.g. "осталось 3 шт".

    Only a positive statement counts as in stock. Absence of a label means Ozon
    said nothing, which is ``None`` — not ``False``, because "unknown" and "out of
    stock" are different answers to a shopper.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if not isinstance(value, str) or not value.strip():
        return None
    digits = re.sub(r"[^\d]", "", value)
    if digits:
        return int(digits) > 0
    return True


async def _search_wildberries(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``wb_search`` results.

    Fields are read as typed attributes on ``WbCardItem`` rather than by string
    key, so a rename upstream fails mypy here instead of silently turning a price
    into ``None``. WB's search can return a distinct no-results response with no
    ``items`` at all, hence the ``getattr`` guard.
    """
    server = SOURCES["wildberries"]
    response = await server.wb_search(query=query, page=1)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="wildberries",
                product_id=str(item.nm_id or ""),
                title=item.name,
                brand=item.brand,
                seller=item.supplier,
                price_rub=item.price_rub,
                rating=item.review_rating,
                rating_count=item.feedbacks,
                in_stock=item.in_stock,
                url=_wb_product_url(item.nm_id),
            )
        )
    return offers


async def _search_yandex(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``yandex_search`` results.

    ``price_rub`` is the everyday price and the only one that ranks;
    ``price_with_plus`` needs a paid subscription, so it rides along in a separate
    field where it cannot masquerade as a bargain.
    """
    server = SOURCES["yandex_market"]
    response = await server.yandex_search(query=query, page=1, limit=limit)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="yandex_market",
                product_id=item.product_id,
                title=item.title,
                brand=item.brand,
                seller=item.seller,
                price_rub=item.price_rub,
                price_with_subscription_rub=item.price_with_plus,
                rating=item.rating,
                rating_count=item.rating_count,
                in_stock=item.in_stock,
                url=item.url,
            )
        )
    return offers


async def _search_ozon(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``ozon_search`` results.

    This adapter was previously written blind — Ozon refuses datacenter IPs, so it
    could not be exercised from CI or a sandbox — and it guessed wrong. It read
    ``price_rub``, ``reviews_count``, ``feedbacks``, ``name``, ``id`` and
    ``brand``; ``OzonSearchItemOut`` declares none of those. Every one silently
    resolved to ``None``, so Ozon offers arrived with no review count at all and
    depended on a fallback key for the price.

    Reading typed attributes makes that class of error a type failure rather than
    a quiet blank. Ozon reports no brand or seller on a search row, so those stay
    empty by definition rather than by accident, and ``stock`` — which the old
    version ignored entirely — now populates ``in_stock``.
    """
    server = SOURCES["ozon"]
    response = await server.ozon_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="ozon",
                product_id=str(item.sku or ""),
                title=item.title or "",
                # Ozon search rows carry neither brand nor seller; a card lookup
                # does. Left empty rather than invented.
                brand="",
                seller="",
                price_rub=_as_price(item.price),
                rating=_as_price(item.rating),
                rating_count=_as_count(item.rating_count),
                in_stock=_stock_from_label(item.stock),
                url=item.url or "",
            )
        )
    return offers


_SEARCH_IMPLS = {
    "wildberries": _search_wildberries,
    "yandex_market": _search_yandex,
    "ozon": _search_ozon,
}


async def _run_source(name: str, query: str, limit: int) -> tuple[SourceOutcome, list[MarketOffer]]:
    """Query one marketplace, converting any failure into a reported outcome.

    Never raises: a comparison with three of four sources is useful, while an
    exception would discard the three that worked.
    """
    started = time.monotonic()
    try:
        offers = await asyncio.wait_for(_SEARCH_IMPLS[name](query, limit), timeout=SOURCE_TIMEOUT_S)
    except TimeoutError:
        return (
            SourceOutcome(
                source=name,
                status="timeout",
                detail=f"no response within {SOURCE_TIMEOUT_S:.0f}s",
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            [],
        )
    except Exception as exc:
        detail = _redact(str(exc))[:200]
        status = "blocked" if any(word in detail for word in ("transport_down", "rate_limited", "403")) else "error"
        return (
            SourceOutcome(
                source=name,
                status=status,
                detail=detail,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            [],
        )

    priced = [offer for offer in offers if offer.price_rub is not None]
    return (
        SourceOutcome(
            source=name,
            status="ok",
            detail=f"{len(offers)} results, {len(priced)} priced",
            offers_returned=len(offers),
            elapsed_ms=round((time.monotonic() - started) * 1000),
        ),
        offers,
    )


@mcp.tool(
    name="compare_prices",
    annotations=ToolAnnotations(
        title="Compare Prices Across Russian Marketplaces",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def compare_prices(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=200,
            description="What to price, in Russian — e.g. 'стиральная машина узкая' or 'iphone 15 128'.",
        ),
    ],
    per_source_limit: Annotated[
        int, Field(default=5, ge=1, le=20, description="How many offers to take from each marketplace.")
    ] = 5,
    sources: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="Restrict to specific marketplaces (wildberries, yandex_market, ozon). Omit to query all.",
        ),
    ] = None,
    ctx: Context | None = None,
) -> CompareResponse:
    """Price one product across every configured Russian marketplace at once.

    Queries each marketplace concurrently and returns a single list ranked by
    price, plus a per-source report of what answered and what did not. This is
    the tool for "where is X cheapest" — running the per-marketplace search tools
    one at a time gives the same data far more slowly and without the ranking.

    Two things to read carefully in the output:

    - `cheapest` is chosen on everyday prices. Yandex Market's subscriber price
      appears as `price_with_subscription_rub` and is deliberately excluded from
      ranking, since it requires a paid Yandex Plus subscription.
    - `source_outcomes` shows which marketplaces answered. A blocked or timed-out
      source means the comparison is partial, not that the product is absent
      there — `complete` tells you which case you are in.

    Titles are matched loosely: marketplaces name things differently, so scan the
    results rather than assuming every row is the identical model.

    ## Error Format

    On validation failure, raises ToolError with a JSON message describing the
    error code and whether it is retryable. Individual source failures do NOT
    raise — they are reported in `source_outcomes`.
    """
    text = (query or "").strip()
    if len(text) < 2:
        raise_tool_error(BadRequestError("query must be at least 2 characters"))

    if sources:
        # An explicit list is validated strictly: naming a marketplace that does
        # not exist is a caller mistake worth surfacing, not silently dropping.
        requested = [name.strip().lower() for name in sources if name and name.strip()]
        unknown = [name for name in requested if name not in _SEARCH_IMPLS]
        if unknown:
            raise_tool_error(BadRequestError(f"unknown source(s) {unknown}: valid options are {sorted(_SEARCH_IMPLS)}"))
    else:
        # Default: every searchable marketplace that is actually wired up. Reading
        # from _SEARCH_IMPLS rather than the static tuple keeps this honest when
        # implementations are added or, in tests, replaced.
        requested = [name for name in _SEARCH_IMPLS if name in SEARCHABLE] or list(_SEARCH_IMPLS)

    active = [name for name in requested if name in SOURCES]
    missing = [name for name in requested if name not in SOURCES]

    if not active:
        raise_tool_error(
            BadRequestError(
                f"none of the requested marketplaces are installed (missing: {missing}). "
                "Install the matching connector package, e.g. 'ozon-connector' for Ozon."
            )
        )

    log_event("compare_prices.start", query=text, sources=active, limit=per_source_limit)
    if ctx is not None:
        await ctx.info(f"compare_prices: {text!r} across {', '.join(active)}")

    results = await asyncio.gather(*(_run_source(name, text, per_source_limit) for name in active))

    outcomes = [outcome for outcome, _ in results]
    offers: list[MarketOffer] = [offer for _, source_offers in results for offer in source_offers]

    for name in missing:
        outcomes.append(
            SourceOutcome(
                source=name,
                status="not_installed",
                detail="connector package is not installed in this environment",
            )
        )

    # Rank on everyday prices only. Unpriced offers keep their place at the end
    # rather than being dropped: "found but no price" is information.
    priced = sorted(
        (offer for offer in offers if offer.price_rub is not None),
        key=lambda offer: offer.price_rub or 0.0,
    )
    unpriced = [offer for offer in offers if offer.price_rub is None]
    ranked = priced + unpriced

    ok_sources = [outcome.source for outcome in outcomes if outcome.status == "ok"]
    failed_sources = [outcome.source for outcome in outcomes if outcome.status != "ok"]

    cheapest = priced[0] if priced else None
    price_spread = None
    if len(priced) >= 2:
        low, high = priced[0].price_rub, priced[-1].price_rub
        if low and high:
            price_spread = round(high - low, 2)

    warnings: list[str] = []
    if failed_sources:
        warnings.append(
            f"partial: {len(failed_sources)} of {len(outcomes)} marketplaces did not answer "
            f"({', '.join(failed_sources)}) — prices below cover only {', '.join(ok_sources) or 'none'}"
        )
    if not priced:
        warnings.append("no_prices: no marketplace returned a usable price for this query")

    log_event(
        "compare_prices.done",
        query=text,
        offers=len(ranked),
        ok_sources=len(ok_sources),
        failed=len(failed_sources),
    )
    return CompareResponse(
        query=text,
        sources_queried=active,
        sources_ok=ok_sources,
        complete=not failed_sources,
        total_offers=len(ranked),
        cheapest=cheapest,
        price_spread_rub=price_spread,
        offers=ranked,
        source_outcomes=outcomes,
        warnings=warnings,
        server_version=SERVER_VERSION,
    )


@mcp.tool(
    name="compare_sources",
    annotations=ToolAnnotations(
        title="List Available Marketplaces",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def compare_sources(ctx: Context | None = None) -> dict[str, Any]:
    """Report which marketplaces this installation can actually query.

    Call this first when a comparison comes back partial: it distinguishes "the
    connector isn't installed" from "the marketplace refused us", which need
    completely different fixes.
    """
    log_event("compare_sources.start")
    if ctx is not None:
        await ctx.info("compare_sources: reporting installed marketplaces")

    installed = sorted(SOURCES)
    searchable = [name for name in SEARCHABLE if name in SOURCES]

    return {
        "installed": installed,
        "searchable": searchable,
        "not_installed": sorted(set(_SEARCH_IMPLS) - set(SOURCES)),
        "notes": {
            "detsky_mir": (
                "installed for direct card/category lookups but excluded from text comparison — "
                "its API has no working text search"
            )
            if "detsky_mir" in SOURCES
            else "not installed",
            "ozon": ("requires curl_cffi and, when Cloudflare challenges, a logged-in Chrome on the CDP port"),
            "yandex_market": "reports both an everyday price and a Plus-subscriber price",
        },
        "source_timeout_s": SOURCE_TIMEOUT_S,
        "server_version": SERVER_VERSION,
        "server_started_at": SERVER_STARTED_AT,
        "process_id": os.getpid(),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
