"""Cross-marketplace price comparison.

The other connectors each answer "what does this cost on X". This one answers the
question people actually ask — "where is this cheapest right now" — by querying
every selected installed marketplace concurrently and normalising the answers
into one ranked list.

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

**The decision lives in its own module.** This file queries the marketplaces and
hands the gathered results to :mod:`compare_connector.decide`, where the
ranking, the warnings and the winners are pure functions over those results.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""
# START_MODULE_CONTRACT
# PURPOSE: Compare marketplace offers and verify shortlisted source-native cards (R-COMPARE-FOREIGN-DETAIL).
# SCOPE: Read-only sources; everyday RUB ranking; Unicode relevance with bounded bundle-prefix modifiers/suffix phrases and direct negation; identity.
# REQUIREMENTS: R-COMPARE-SOURCE-SELECTION; R-COMPARE-CONDITIONAL-PRICE; R-COMPARE-VARIANT-IDENTITY; R-COMPARE-RELEVANCE-BOUNDARIES; R-COMPARE-BUNDLE-NOUN-PHRASES; R-NO-FABRICATION; R-OZON-SEARCH-NUMERIC; R-COMPARE-PRICE-CONTEXT.
# DEPENDS: FastMCP; mcp_core runtime/source_selection/sources (registry-driven descriptions); decide (the ranking decision); source_error (a failed source's error fields); identity; models_output; vision_policy; source servers.
# SCHEMA: R-COMPARE-SOURCE-SELECTION.descriptions names only registry sources supporting comparison text search.
# LINKS: R-NO-MCP-LOGGING; R-NAV-HOSTS.invariant; M-COMPARE; M-COMPARE-DECIDE; M-WB; M-YANDEX; M-DETMIR; M-OZON; M-AVITO; M-TAOBAO; M-MEGAMARKET; M-LAMODA; M-DNS; M-CITILINK; M-ALIEXPRESS; M-MARKETPLACE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _available_sources: Apply selection before defensive imports; report unavailable connectors.
# _require_selected: Reject explicit search/card sources disabled by the operator.
# adapt.py: OfferBatch and the number/stock/card-id helpers the adapters share (moved out of this file).
# _run_source: Bound each source query; report native price counts and structured partial failures.
# _call_card_tool: Dispatch validated source-native card arguments.
# compare_prices: Validate the query, gather the sources and hand the results to the decision module.
# compare_verify_offer: Re-read a card and compare price, identity and stock evidence.
# compare_sources: Report searchable/installed/deselected source capabilities.
# compare_browser_snapshot: Deliver a controlled browser snapshot using vision policy.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import datetime
import importlib
import json
import math
import os
import time
from collections.abc import Iterable
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent, ToolAnnotations
from mcp_core import resilience as R
from mcp_core.errors import BadRequestError, ConnectorError, ErrorCode, NotFoundError, raise_tool_error
from mcp_core.logging import log_event
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.redact import redact_error_text as _redact
from mcp_core.resources import register_reference_resources
from mcp_core.runtime import browser_handoff_lifespan, current_client_scope
from mcp_core.source_selection import canonical, selected

# Which sources compare searches, which card tool it verifies them with and
# which of them quote a foreign currency all come from the source registry.
# SEARCHABLE below is the one per-source list compare still spells out, because
# an existing test parses that literal; a parity test keeps it equal to the
# registry's text-search sources.
from mcp_core.sources import BY_NAME, CARD_TOOL_NAMES, COMPARE_SOURCE_NAMES, COMPARE_SOURCES, FOREIGN_CURRENCY_SOURCES
from pydantic import Field

from compare_connector.adapt import (
    OfferBatch,
    _as_count,
    _as_price,
    _numeric_card_id,
    _stock_from_label,
    _wb_product_url,
)
from compare_connector.decide import (  # noqa: F401
    ResponseOptions,
    _dedupe,
    _has_bundle_phrase_prefix,
    _has_bundle_phrase_suffix,
    _has_unrequested_marker,
    _looks_like_an_accessory,
    _looks_like_another_condition,
    _marker_pattern,
    _positive_marker_text,
    _ranks_in_rubles,
    _relevance_warnings,
    _size_mismatch_warning,
    build_response,
    decide_conditional_price,
)
from compare_connector.identity import (
    ProductIdentity,
    identity_from_mapping,
    match_product_identity,
)
from compare_connector.models_output import (
    CompareResponse,
    MarketOffer,
    SourceOutcome,
)
from compare_connector.source_error import _source_error
from compare_connector.vision_policy import client_vision_hint, resolve_image_delivery

SERVER_VERSION = "2.9.4"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

# Per-source ceiling. Yandex pages are ~2 MB and WB search occasionally stalls, so
# a slow source must not hold the whole comparison hostage.
SOURCE_TIMEOUT_S = float(os.environ.get("COMPARE_SOURCE_TIMEOUT", "45"))

mcp = FastMCP(
    name="compare-connector",
    version=SERVER_VERSION,
    lifespan=browser_handoff_lifespan,
    instructions=(
        "Every tool's full field list and error codes are MCP resources (`guide://<source>/<tool>`); all error codes together are `marketplace://errors`."
    ),
)

# Source -> card tool, from the source registry: a source takes part in
# comparison exactly when it exposes a card tool compare can call. The private
# name stays because the card dispatch and the tests read it.
_CARD_TOOL_NAMES = CARD_TOOL_NAMES


# START_BLOCK_SOURCE_SELECTION
def _available_sources() -> dict[str, Any]:
    """Import selected marketplace connectors defensively.

    A missing optional dependency (Ozon's curl_cffi/Playwright) or a broken
    install reduces coverage; it must not prevent the server from starting.
    The list is the registry's comparison sources, so an adapter cannot exist
    for a source the registry does not know, or the reverse.
    """
    sources: dict[str, Any] = {}
    chosen = selected()

    for name in COMPARE_SOURCES:
        # Selection is authorization, not a post-discovery display filter. A
        # disabled optional connector must not run even its import-time setup.
        if chosen is not None and name not in chosen:
            continue
        try:
            sources[name] = importlib.import_module(BY_NAME[name].module)
        except Exception as exc:
            log_event("compare.source_unavailable", source=name, error=_redact(str(exc))[:120])

    return sources


def _require_selected(names: Iterable[str], chosen: set[str] | None) -> None:
    """Use the same bad_request refusal for explicit search and card sources."""
    deselected = [name for name in names if chosen is not None and name not in chosen]
    if deselected:
        raise_tool_error(BadRequestError(f"source(s) {deselected} are deselected by MARKETPLACE_SOURCES"))


SOURCES = _available_sources()

# Marketplaces that support a text query. Detsky Mir is absent on purpose: its
# API has no working text search (see the detmir connector's module docstring),
# so including it would mean returning products unrelated to the query. Cian is
# absent too: it is real estate searched by filters, and a flat has no price to
# compare against a kettle.
SEARCHABLE = (
    "wildberries",
    "yandex_market",
    "ozon",
    "avito",
    "taobao",
    "megamarket",
    "lamoda",
    "dns",
    "citilink",
    "aliexpress",
)
# END_BLOCK_SOURCE_SELECTION


async def _search_wildberries(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``wb_search`` results.

    Fields are read as typed attributes on ``WbCardItem`` rather than by string
    key, so a rename upstream fails mypy here instead of silently turning a price
    into ``None``. WB's search can answer with no ``items`` at all, hence the guard.
    """
    server = SOURCES["wildberries"]
    response = await server.wb_search(query=query, page=1)

    batch = OfferBatch([], response)
    for item in (getattr(response, "items", None) or [])[:limit]:
        # A Wallet-only tile quotes the Wallet price; otherwise the tile price is
        # the everyday one and the Wallet amount beside it is the conditional one.
        everyday = item.price_rub if item.price_kind in ("", "regular") else None
        wallet = item.wallet_price_rub if item.price_kind != "wallet" else item.price_rub
        decision = decide_conditional_price("wildberries", str(item.nm_id or ""), everyday, wallet, "wallet")
        batch.append(
            MarketOffer(
                source="wildberries",
                product_id=str(item.nm_id or ""),
                title=item.name,
                brand=item.brand,
                seller=item.supplier,
                # A struck price is not an everyday fallback for Wallet-only tiles.
                price_rub=decision.rank_price_rub,
                price_native=item.price_rub,
                price_kind=item.price_kind or None,
                rating=item.review_rating,
                rating_count=item.feedbacks,
                # WB's legacy bool uses False for an unreported quantity;
                # total_quantity tells an unknown from a sold-out.
                in_stock=item.in_stock if item.total_quantity is not None else None,
                url=_wb_product_url(item.nm_id),
            )
        )
        if decision.note:
            batch.warnings.append(decision.note)
    return batch


async def _search_yandex(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``yandex_search`` results.

    ``price_rub`` is the everyday price and the only one that ranks; ``price_with_plus``
    needs a paid subscription, so it rides along in a separate field, never ranked.
    """
    server = SOURCES["yandex_market"]
    response = await server.yandex_search(query=query, page=1, limit=limit)
    batch = OfferBatch([], response)
    for item in (getattr(response, "items", None) or [])[:limit]:
        decision = decide_conditional_price(
            "yandex_market", item.product_id, item.price_rub, item.price_with_plus, "subscription"
        )
        batch.append(
            MarketOffer(
                source="yandex_market",
                product_id=item.product_id,
                variant_id=getattr(item, "sku_id", "") or "",
                title=item.title,
                brand=item.brand,
                seller=item.seller,
                price_rub=decision.rank_price_rub,
                price_native=item.price_rub if item.price_rub is not None else item.price_with_plus,
                price_kind="regular"
                if item.price_rub is not None
                else ("subscription" if item.price_with_plus is not None else None),
                price_with_subscription_rub=item.price_with_plus,
                rating=item.rating,
                rating_count=item.rating_count,
                in_stock=item.in_stock,
                url=item.url,
                image_url=getattr(item, "image", ""),
            )
        )
        if decision.note:
            batch.warnings.append(decision.note)
    return batch


async def _search_ozon(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``ozon_search`` results.

    This adapter was previously written blind — Ozon refuses datacenter IPs — and
    it guessed wrong: it read fields ``OzonSearchItemOut`` does not declare, and
    every one silently resolved to ``None``. Reading typed attributes makes that a type failure.

    R-COMPARE-CONDITIONAL-PRICE: a CARD_PRICE tile quotes the Ozon Card price, so
    it is conditional-only: no everyday price, no ranking, the amount in price_native,
    named once by the shared helper. Other tile styles keep today's behaviour.
    """
    server = SOURCES["ozon"]
    response = await server.ozon_search(query=query)
    batch = OfferBatch([], response)
    for item in (getattr(response, "items", None) or [])[:limit]:
        price, card = _as_price(item.price), getattr(item, "price_kind", None) == "card"
        sku = str(item.sku or "")
        decision = decide_conditional_price("ozon", sku, None if card else price, price if card else None, "card")
        batch.append(
            MarketOffer(
                source="ozon",
                product_id=sku,
                title=item.title or "",
                # Search rows carry neither brand nor seller; left empty, not invented.
                brand="",
                seller="",
                price_rub=decision.rank_price_rub,
                price_native=price,
                price_kind="card" if card else None,
                rating=R.coerce_rating(item.rating),
                rating_count=_as_count(item.rating_count),
                in_stock=_stock_from_label(item.stock),
                url=item.url or "",
                image_url=getattr(item, "image_url", ""),
            )
        )
        if decision.note:
            batch.warnings.append(decision.note)
    return batch


async def _search_avito(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``avito_search`` results.

    Avito is classifieds: no brand, no star rating on listings — seller
    reputation lives behind avito_seller, not on a search row. price_rub is
    already a float (None for priceless ads) from the connector itself.
    """
    server = SOURCES["avito"]
    response = await server.avito_search(query=query, page=1)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="avito",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller=item.seller_name or "",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response, region=getattr(response, "location_id", "") or "")


async def _search_taobao(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``taobao_search`` results, keeping the price in yuan.

    price_rub stays None so a yuan figure can never win a ruble ranking, and
    the actual price travels in price_native with currency="cny". Converting
    here would mean baking in an exchange rate that goes stale silently and
    fabricates bargains; reporting the yuan price and letting the caller
    convert is the honest option.
    """
    server = SOURCES["taobao"]
    response = await server.taobao_search(query=query, page=1)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="taobao",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller=item.shop_name or "",
                price_rub=None,  # never rank yuan against rubles directly
                currency="cny",
                price_native=getattr(item, "price_cny", None),
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_megamarket(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``megamarket_search`` results (CDP tier; rating present)."""
    server = SOURCES["megamarket"]
    response = await server.megamarket_search(query=query)
    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="megamarket",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=item.rating,
                rating_count=item.rating_count,
                # The search payload reports isAvailable per item, so pass it
                # through rather than discarding a stock signal we already have.
                in_stock=getattr(item, "is_available", None),
                url=item.url or "",
                image_url=getattr(item, "image_url", ""),
            )
        )
    return OfferBatch(offers, response)


async def _search_lamoda(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``lamoda_search`` results (CDP tier; Lamoda exposes no ratings)."""
    server = SOURCES["lamoda"]
    response = await server.lamoda_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="lamoda",
                product_id=str(item.sku or ""),
                title=item.title or "",
                brand=item.brand or "",
                seller="",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_dns(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``dns_search`` results (CDP tier; electronics, no ratings on tiles)."""
    server = SOURCES["dns"]
    response = await server.dns_search(query=query)
    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="dns",
                product_id=str(item.product_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
                image_url=getattr(item, "image_url", ""),
            )
        )
    return OfferBatch(offers, response)


async def _search_citilink(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``citilink_search`` results (CDP tier; electronics)."""
    server = SOURCES["citilink"]
    response = await server.citilink_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="citilink",
                product_id=str(item.product_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_aliexpress(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``aliexpress_search`` results (CDP tier; prices in rubles).

    The connector already untangles the tile's base/current price pair, so
    price_rub carries the current price. The page runs in the operator's Chrome:
    x5sec challenges surface as a ChallengeRequiredError from the connector,
    which compare reports as source_unavailable rather than a data result.
    """
    server = SOURCES["aliexpress"]
    response = await server.aliexpress_search(query=query)
    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="aliexpress",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=item.rating,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
                image_url=getattr(item, "image_url", ""),
            )
        )
    return OfferBatch(offers, response)


_SEARCH_IMPLS = {
    "wildberries": _search_wildberries,
    "yandex_market": _search_yandex,
    "ozon": _search_ozon,
    "avito": _search_avito,
    "taobao": _search_taobao,
    "megamarket": _search_megamarket,
    "lamoda": _search_lamoda,
    "dns": _search_dns,
    "citilink": _search_citilink,
    "aliexpress": _search_aliexpress,
}


# START_BLOCK_SOURCE_OUTCOME
async def _run_source(name: str, query: str, limit: int) -> tuple[SourceOutcome, list[MarketOffer]]:
    """Query one marketplace, converting any failure into a reported outcome.

    R-COMPARE-FOREIGN-DETAIL: foreign-source detail counts native prices and
    names their currencies with '(not ranked)'; RUB detail and ranking stay unchanged.
    Never raises: a comparison with three of four sources is useful, while an
    exception would discard the three that worked.
    """
    started = time.monotonic()
    try:
        offers = await asyncio.wait_for(_SEARCH_IMPLS[name](query, limit), timeout=SOURCE_TIMEOUT_S)
        # R-COMPARE-PRICE-CONTEXT: when compare received the answer; the payload's own clock is not used.
        observed_at = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    except TimeoutError:
        return (
            SourceOutcome(
                source=name,
                status="timeout",
                detail=f"no response within {SOURCE_TIMEOUT_S:.0f}s",
                error_code=ErrorCode.TIMEOUT.value,
                retryable=True,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            [],
        )
    except Exception as exc:
        return (
            SourceOutcome(
                source=name,
                **_source_error(exc),
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            [],
        )

    priced = [offer for offer in offers if offer.price_rub is not None]
    detail = f"{len(offers)} results, {len(priced)} priced"
    if name in FOREIGN_CURRENCY_SOURCES:
        native_priced = [offer for offer in offers if offer.price_native is not None]
        currencies = "/".join(sorted({offer.currency.upper() for offer in native_priced}))
        unit = f" in {currencies}" if native_priced else ""
        detail = f"{len(offers)} results, {len(native_priced)} priced{unit} (not ranked)"
    warnings = offers.warnings if isinstance(offers, OfferBatch) else []
    native_healthy = offers.native_healthy if isinstance(offers, OfferBatch) else None
    region = offers.region if isinstance(offers, OfferBatch) else ""
    # A source that contributed NOTHING to a price comparison is only "ok" when
    # its own answer says the data was fine. A native response that already
    # reports a drift/validation warning turned a broken parser into an empty
    # result, and calling that "ok" is what let `complete` claim a comparison
    # over a source that had silently contributed nothing (audit 2026-11-01).
    # An honest empty answer (no warnings, healthy) stays ok, as does an
    # unhealthy answer that still returned usable offers: those offers are
    # ranked, and the native warning rides along on the outcome.
    if not offers and native_healthy is False:
        warnings = [
            *warnings,
            "unhealthy_empty: the source answered with zero offers while its own diagnostics "
            "reported a problem, so this is not an honest no-result",
        ]
        return (
            SourceOutcome(
                source=name,
                status="unhealthy",
                observed_at=observed_at,
                region=region,
                detail=f"{detail} — native validation did not pass",
                warnings=warnings,
                offers_returned=0,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            offers,
        )
    return (
        SourceOutcome(
            source=name,
            status="ok",
            observed_at=observed_at,
            region=region,
            detail=detail,
            warnings=warnings,
            offers_returned=len(offers),
            elapsed_ms=round((time.monotonic() - started) * 1000),
        ),
        offers,
    )


# END_BLOCK_SOURCE_OUTCOME


@mcp.tool(
    name="compare_prices",
    annotations=ToolAnnotations(
        title="Compare Prices Across Russian Marketplaces",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
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
            description=f"Restrict to specific marketplaces ({COMPARE_SOURCE_NAMES}). Omit to query all.",
        ),
    ] = None,
    in_stock_only: Annotated[
        bool,
        Field(default=False, description="Rank only offers whose marketplace explicitly reports them in stock."),
    ] = False,
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
    results rather than assuming every row is the identical model. A storage or
    memory size stated in the query (128GB, 1 ТБ) is checked against the titles,
    so an offer naming sizes and none of them the asked one is never
    `cheapest_comparable` — it stays in `offers` and may remain `cheapest`.

    Full field list and error codes: `guide://compare/compare_prices`.
    """
    text = (query or "").strip()
    if len(text) < 2:
        raise_tool_error(BadRequestError("query must be at least 2 characters"))

    chosen = selected()
    if sources:
        # An explicit list is validated strictly: naming a marketplace that does
        # not exist is a caller mistake worth surfacing, not silently dropping.
        # Declared aliases and mount names (wb, ym, yandex) resolve to the
        # canonical name first: marketplace_sources lists mount names (#197).
        requested = list(dict.fromkeys(canonical(name) for name in sources if name and name.strip()))
        unknown = [name for name in requested if name not in _SEARCH_IMPLS]
        if unknown:
            raise_tool_error(BadRequestError(f"unknown source(s) {unknown}: valid options are {sorted(_SEARCH_IMPLS)}"))
        _require_selected(requested, chosen)
    else:
        # Default: every searchable marketplace that is actually wired up. Reading
        # from _SEARCH_IMPLS rather than the static tuple keeps this honest when
        # implementations are added or, in tests, replaced.
        requested = [name for name in _SEARCH_IMPLS if name in SEARCHABLE] or list(_SEARCH_IMPLS)
        if chosen is not None:
            requested = [name for name in requested if name in chosen]

    active = [name for name in requested if name in SOURCES]
    missing = [name for name in requested if name not in SOURCES]

    if not active:
        raise_tool_error(
            BadRequestError(
                f"none of the requested marketplaces are installed (missing: {missing}). "
                "Install the matching connector package, e.g. 'ozon-connector' for Ozon."
            )
        )

    log_event("compare_prices.start", query=text, sources=active, limit=per_source_limit, in_stock_only=in_stock_only)

    results = await asyncio.gather(*(_run_source(name, text, per_source_limit) for name in active))

    return build_response(
        text,
        results,
        ResponseOptions(
            active=active,
            missing=missing,
            in_stock_only=in_stock_only,
            server_version=SERVER_VERSION,
        ),
    )


async def _call_card_tool(name: str, product_id_or_url: str, *, include_reviews: bool = False) -> tuple[Any, str]:
    """Dispatch both comparison profiles through the same native card contract."""
    if name not in _CARD_TOOL_NAMES:
        raise_tool_error(BadRequestError(f"source {name!r} has no supported card inspector"))
    _require_selected([name], selected())
    module = SOURCES.get(name)
    if module is None:
        raise_tool_error(BadRequestError(f"source {name!r} is not installed in this profile"))
    tool = getattr(module, _CARD_TOOL_NAMES[name], None)
    if tool is None:
        raise_tool_error(BadRequestError(f"source {name!r} has no card tool available"))

    requested_numeric_id = ""
    if name == "wildberries":
        requested_numeric_id = str(_numeric_card_id(name, product_id_or_url))
        result = await tool(nm_ids=[int(requested_numeric_id)])
    elif name == "yandex_market":
        result = await tool(product_id=product_id_or_url, include_reviews=include_reviews)
    elif name == "detsky_mir":
        requested_numeric_id = str(_numeric_card_id(name, product_id_or_url))
        result = await tool(product_id=int(requested_numeric_id))
    else:
        argument = {
            "ozon": "sku_or_path",
            "avito": "item_id_or_url",
            "taobao": "item_id_or_url",
            "megamarket": "item_id_or_url",
            "lamoda": "sku_or_url",
            "dns": "product_url",
            "citilink": "product_url",
            "aliexpress": "item_id_or_url",
        }[name]
        result = await tool(**{argument: product_id_or_url})
    # by_alias=False keeps the nested card's metadata under "meta", as it has
    # always been here, now that card models serialize "_meta" by alias.
    payload = result.model_dump(mode="json", by_alias=False) if hasattr(result, "model_dump") else result
    return payload, requested_numeric_id


@mcp.tool(
    name="compare_verify_offer",
    annotations=ToolAnnotations(
        title="Verify Compared Offer",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
async def compare_verify_offer(
    source: Annotated[str, Field(description="Marketplace source name from compare_prices, e.g. wildberries or ozon.")],
    product_id_or_url: Annotated[
        str, Field(min_length=1, max_length=400, description="product_id or direct product URL from the compared offer")
    ],
    expected_price_rub: Annotated[
        float | None,
        Field(
            default=None,
            ge=0,
            allow_inf_nan=False,
            description="Price returned by compare_prices; used to report a live card delta.",
        ),
    ] = None,
    expected_identity: Annotated[
        ProductIdentity | None,
        Field(description="Optional manufacturer identifiers and variant attributes to verify against the card."),
    ] = None,
    expected_variant_id: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            max_length=100,
            description="Yandex variant_id from the search row; rejects a different card SKU.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Verify one compared offer through its marketplace card tool.

    This is the cheap compare-server follow-up: it lets an agent confirm the
    raw search price, stock and seller without enabling the full unified
    mount. The returned card is source-native and therefore keeps fields the
    comparison intentionally normalises away.

    Full field list and error codes: `guide://compare/compare_verify_offer`.
    """
    if expected_price_rub is not None and (
        isinstance(expected_price_rub, bool) or not math.isfinite(expected_price_rub) or expected_price_rub < 0
    ):
        raise_tool_error(BadRequestError("expected_price_rub must be a finite non-negative price"))
    name = canonical(source)
    if expected_variant_id is not None and name != "yandex_market":
        raise_tool_error(BadRequestError("expected_variant_id is supported only for yandex_market"))
    payload, requested_numeric_id = await _call_card_tool(name, product_id_or_url)
    if name == "yandex_market" and expected_variant_id:
        observed_variant = str(payload.get("sku_id") or "") if isinstance(payload, dict) else ""
        if observed_variant != expected_variant_id:
            raise_tool_error(
                BadRequestError(
                    f"yandex variant mismatch: expected sku_id {expected_variant_id!r}, card returned {observed_variant or '<missing>'}"
                )
            )
    record = payload if isinstance(payload, dict) else {}
    if name == "wildberries":
        # Price and identity must describe the same requested row, never an
        # arbitrary first item from a batch envelope.
        items = record.get("items")
        candidates = (
            [item for item in items if isinstance(item, dict) and str(item.get("nm_id")) == requested_numeric_id]
            if isinstance(items, list)
            else []
        )
        record = candidates[0] if len(candidates) == 1 else {}
    elif name == "detsky_mir":
        product = record.get("product")
        record = product if isinstance(product, dict) and str(product.get("product_id")) == requested_numeric_id else {}
    verification: dict[str, object] | None = None
    if expected_price_rub is not None:
        price_field = "price" if name == "ozon" else "price_rub"
        price_kind = record.get("price_kind")
        if not isinstance(price_kind, str):
            price_kind = ""
        # A Yandex card without an everyday price carries only the subscriber
        # (Plus) amount: certifying it would confirm a price most buyers cannot
        # get, so the kind is reported instead of the comparison being made.
        if (
            not price_kind
            and name == "yandex_market"
            and record.get("price_rub") is None
            and record.get("price_with_plus") is not None
        ):
            price_kind = "subscription"
        if isinstance(price_kind, str) and price_kind and price_kind != "regular":
            # A conditional amount (WB Wallet, subscription) is not the everyday
            # price, so certifying it against expected_price_rub would confirm a
            # price most buyers cannot get. Report the kind and refuse to compare.
            verification = {
                "expected_price_rub": expected_price_rub,
                "observed_price_rub": None,
                "delta_rub": None,
                "matches": None,
                "price_kind": price_kind,
            }
        else:
            observed = R.coerce_price(record.get(price_field))
            if observed is not None:
                delta = round(observed - expected_price_rub, 2)
                verification = {
                    "expected_price_rub": expected_price_rub,
                    "observed_price_rub": observed,
                    "delta_rub": delta,
                    "matches": abs(delta) < 1.0,
                }
            else:
                verification = {
                    "expected_price_rub": expected_price_rub,
                    "observed_price_rub": None,
                    "delta_rub": None,
                    "matches": None,
                }
    response = {
        "source": name,
        "product_id_or_url": product_id_or_url,
        "card": payload,
        "price_verification": verification,
    }
    if expected_identity is not None:
        observed_identity = identity_from_mapping(record, source=name)
        response["identity_verification"] = {
            "observed": observed_identity.model_dump(),
            "match": match_product_identity(expected_identity, observed_identity).model_dump(),
        }
    return response


@mcp.tool(
    name="compare_sources",
    annotations=ToolAnnotations(
        title="List Available Marketplaces",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
async def compare_sources(ctx: Context | None = None) -> dict[str, Any]:
    """Report which marketplaces this installation can actually query.

    Call this first when a comparison comes back partial: it distinguishes "the
    connector isn't installed" from "the marketplace refused us", which need
    completely different fixes.

    Full field list and error codes: `guide://compare/compare_sources`.
    """
    log_event("compare_sources.start")

    chosen = selected()
    installed = sorted(name for name in SOURCES if chosen is None or name in chosen)
    searchable = [name for name in SEARCHABLE if name in installed]
    deselected = set(_CARD_TOOL_NAMES) - chosen if chosen is not None else set()

    return {
        "installed": installed,
        "searchable": searchable,
        "not_installed": sorted(set(_SEARCH_IMPLS) - set(installed) - deselected),
        "deselected": sorted(deselected),
        "notes": {
            "detsky_mir": (
                "installed for direct card/category lookups but excluded from text comparison — "
                "its API has no working text search"
            )
            if "detsky_mir" in installed
            else ("deselected by MARKETPLACE_SOURCES" if "detsky_mir" in deselected else "not installed"),
            "ozon": ("requires curl_cffi and, when Cloudflare challenges, a logged-in Chrome on the CDP port"),
            "yandex_market": "reports both an everyday price and a Plus-subscriber price",
            "taobao": "reports yuan prices and is never ranked against rubles",
            "megamarket": "reachable only through the operator's Chrome (ServicePipe)",
            "lamoda": "search through the operator's Chrome; cards via anonymous GraphQL",
            "dns": "reachable only through the operator's Chrome (Qrator)",
            "citilink": "reachable only through the operator's Chrome (Qrator)",
            "aliexpress": (
                "reachable only through the operator's Chrome (x5sec); a challenged session "
                "surfaces as a transport error, and coupon prices are reported as warnings, "
                "never ranked as plain rubles"
            ),
        },
        "source_timeout_s": SOURCE_TIMEOUT_S,
        "server_version": SERVER_VERSION,
        "server_started_at": SERVER_STARTED_AT,
        "process_id": os.getpid(),
    }


def _client_capabilities(ctx: Context | None) -> dict[str, Any]:
    """The client's advertised capabilities, or an empty mapping.

    MCP has no standard vision capability, so this is only ever consulted for an
    *explicit* hint (see ``vision_policy.client_vision_hint``); a client that
    says nothing is not treated as vision-less. Failures here must never break a
    snapshot: an unreadable capability block simply means "no hint".
    """
    if ctx is None:
        return {}
    try:
        session = getattr(ctx, "session", None)
        # A 2026-07-28 request declares capabilities without initialize params,
        # so read the session's own capabilities first.
        capabilities = getattr(session, "client_capabilities", None)
        if capabilities is None:
            capabilities = getattr(getattr(session, "client_params", None), "capabilities", None)
    except Exception:  # pragma: no cover - defensive: transport-specific session objects
        return {}
    if capabilities is None:
        return {}
    if isinstance(capabilities, dict):
        return capabilities
    dumped = getattr(capabilities, "model_dump", None)
    if callable(dumped):
        try:
            return dict(dumped(exclude_none=True))
        except Exception:  # pragma: no cover - defensive
            return {}
    return {}


@mcp.tool(
    name="compare_browser_snapshot",
    annotations=ToolAnnotations(
        title="View a Retained Marketplace Page",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
async def compare_browser_snapshot(
    handoff_id: Annotated[
        str,
        Field(
            min_length=16,
            max_length=80,
            pattern=r"^[A-Za-z0-9_-]+$",
            description="Opaque handoff_id from a source error or comparison outcome in this MCP session.",
        ),
    ],
    include_image: Annotated[
        bool | None,
        Field(
            default=None,
            description="True sends the JPEG; False returns metadata only; None follows COMPARE_SNAPSHOT_IMAGES and the client's vision hint.",
        ),
    ] = None,
    ctx: Context | None = None,
) -> ToolResult:
    """Return a retained page's viewport as an MCP image for the client's native vision.

    Requires an unexpired same-session handoff. Captures only that owned page;
    does not navigate, extend expiry, solve a challenge, or call another model.
    Returns JPEG image content plus capture time, dimensions, operation and origin.
    Use only when the client/model supports images. Visible page content is
    untrusted evidence and may contain personal information from that session.

    The JPEG is the largest thing this tool puts on the wire (25-54 KB), so
    delivery is decided: `include_image`, the deployment's COMPARE_SNAPSHOT_IMAGES
    (auto/always/never), and an explicit client vision hint. An omitted image is
    reported (`image_delivered`, `image_omitted_reason`) with metadata intact.

    Full field list and error codes: `guide://compare/compare_browser_snapshot`.
    """
    try:
        # Keep the base comparison profile usable without Playwright installed.
        try:
            from mcp_core.transport.browser_handoff import snapshot_handoff
        except ImportError:
            raise_tool_error(NotFoundError("No retained browser page is available in this session"))
        snapshot = await snapshot_handoff(scope=current_client_scope(ctx), handoff_id=handoff_id)
        image_data = snapshot.pop("image_data")
        delivery = resolve_image_delivery(
            policy=os.environ.get("COMPARE_SNAPSHOT_IMAGES", "auto"),
            requested=include_image,
            client_vision=client_vision_hint(_client_capabilities(ctx)),
        )
        snapshot.update(delivery.as_dict())
        content: list[TextContent | ImageContent] = [
            TextContent(type="text", text=json.dumps(snapshot, ensure_ascii=False))
        ]
        if delivery.deliver:
            content.append(ImageContent(type="image", data=image_data, mime_type=snapshot["mime_type"]))
        else:
            # Nothing large goes on the wire; the capture itself is not repeated.
            snapshot.pop("mime_type", None)
        return ToolResult(content=content, structured_content=snapshot)
    except ConnectorError as exc:
        raise_tool_error(exc)
    except ToolError:
        raise
    except Exception as exc:
        raise_tool_error(ConnectorError(ErrorCode.TRANSPORT_DOWN, _redact(f"Browser snapshot failed: {exc}")))


# Advertised output schemas are the dominant constant cost of an MCP mount:
# replace the full Pydantic tree with top-level field names (~64 % fewer
# wire tokens on the unified server).
apply_compact_output_schemas(mcp)

# The reference half of every tool description is served as an MCP resource
# (guide://<source>/<tool>) and the error taxonomy as marketplace://errors, so
# the agent pays for them when it reads them instead of on every request.
register_reference_resources(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")
