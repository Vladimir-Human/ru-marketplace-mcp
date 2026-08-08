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
import math
import os
import re
import time
from collections.abc import Iterable
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
from mcp_core.errors import BadRequestError, raise_tool_error
from mcp_core.logging import log_event
from mcp_core.redact import redact_error_text as _redact
from pydantic import Field

from compare_connector.models_output import (
    CompareResponse,
    MarketOffer,
    SourceOutcome,
)

SERVER_VERSION = "1.4.0"
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

    try:
        from avito_connector import server as avito_server

        sources["avito"] = avito_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="avito", error=_redact(str(exc))[:120])

    try:
        from taobao_connector import server as taobao_server

        sources["taobao"] = taobao_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="taobao", error=_redact(str(exc))[:120])

    try:
        from megamarket_connector import server as megamarket_server

        sources["megamarket"] = megamarket_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="megamarket", error=_redact(str(exc))[:120])

    try:
        from lamoda_connector import server as lamoda_server

        sources["lamoda"] = lamoda_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="lamoda", error=_redact(str(exc))[:120])

    try:
        from dns_connector import server as dns_server

        sources["dns"] = dns_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="dns", error=_redact(str(exc))[:120])

    try:
        from citilink_connector import server as citilink_server

        sources["citilink"] = citilink_server
    except Exception as exc:
        log_event("compare.source_unavailable", source="citilink", error=_redact(str(exc))[:120])

    return sources


SOURCES = _available_sources()

# Marketplaces that support a text query. Detsky Mir is absent on purpose: its
# API has no working text search (see the detmir connector's module docstring),
# so including it would mean returning products unrelated to the query.
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
)

# Yuan sources rank separately from ruble ones: a baked-in CNY→RUB rate would go
# silently stale, and ranking a stale conversion against live ruble prices
# fabricates bargains. Taobao offers are reported with currency="cny" and are
# excluded from the cheapest-rub ranking.
FOREIGN_CURRENCY_SOURCES = ("taobao",)


def _dedupe(offers: Iterable[MarketOffer]) -> list[MarketOffer]:
    """Drop repeats of the same listing, keyed on (source, product_id).

    A marketplace can return the same product twice — colour variants sharing
    an id, a pagination overlap — and every copy would otherwise rank
    separately. That inflates total_offers and, worse, lets one listing occupy
    both the cheapest slot and the runner-up, making a single offer look like
    two independent confirmations of a price.

    Order is preserved so the per-source ordering the marketplaces chose
    survives. Offers with no product_id cannot be compared this way and are all
    kept: dropping them on a blank key would silently merge distinct listings.
    """
    seen: set[tuple[str, str]] = set()
    out: list[MarketOffer] = []
    for offer in offers:
        if not offer.product_id:
            out.append(offer)
            continue
        key = (offer.source, offer.product_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


# Words that mark a listing as something you buy *for* a product rather than
# the product itself. A search for "iphone 15" matches a case, a screen
# protector and a cable, all of them cheaper than the phone, and whichever is
# cheapest becomes the headline answer. Observed live in July 2026: a query for
# iPhone 15 ranked a 34 224 ₽ listing above genuine 52 049 ₽ ones.
_ACCESSORY_MARKERS = (
    "чехол",
    "чехол-книжка",
    "бампер",
    "накладка",
    "защитное стекло",
    "стекло",
    "плёнка",
    "пленка",
    "кабель",
    "адаптер",
    "зарядка",
    "зарядное",
    "держатель",
    "ремешок",
    "подставка",
    "наклейка",
    "case",
    "cover",
    "screen protector",
    "cable",
    "charger",
    "strap",
)


# Words that mark a listing as a different *condition* of the product, not a
# different product. This is the case the accessory list above misses entirely
# and the one that actually bit: checked live in July 2026, the cheap iPhone 15
# rows on Wildberries were "Восстановленный" and "Витринный образец" — a
# refurbished phone and a display unit, ranked against new ones from other
# marketplaces. Nothing in the title looks like an accessory, and the price is
# not low enough to trip a median check. Only the wording gives it away.
_CONDITION_MARKERS = (
    "восстановленный",
    "восстановленная",
    "витринный образец",
    "витринный",
    "уценка",
    "уценённый",
    "уцененный",
    "б/у",
    "бывший в употреблении",
    "как новый",
    "refurbished",
    "renewed",
    "pre-owned",
    "used",
    "open box",
)


def _looks_like_another_condition(title: str, query: str) -> bool:
    """Whether a title advertises a used, refurbished or display unit.

    A query that asks for one of these is answered correctly by them, so a
    marker present in the query itself never counts.
    """
    low_title = title.lower()
    low_query = query.lower()
    return any(m in low_title and m not in low_query for m in _CONDITION_MARKERS)


def _looks_like_an_accessory(title: str, query: str) -> bool:
    """Whether a title reads as an accessory the query did not ask for.

    Asking for a case and getting cases is correct, so a marker present in the
    query itself never counts.
    """
    low_title = title.lower()
    low_query = query.lower()
    return any(m in low_title and m not in low_query for m in _ACCESSORY_MARKERS)


def _relevance_warnings(query: str, priced: list[MarketOffer]) -> list[str]:
    """Flag a cheapest offer that probably answers a different question.

    Deliberately warnings, never filtering. Dropping a row on a heuristic would
    hide a real bargain, and no threshold tuned without live data deserves that
    power. Saying "this looks like an accessory, check it" costs the caller
    nothing and is the difference between a wrong answer and a checked one.
    """
    if not priced:
        return []
    warnings: list[str] = []
    cheapest = priced[0]

    if _looks_like_an_accessory(cheapest.title, query):
        warnings.append(
            f"relevance: the cheapest offer ({cheapest.source}, {cheapest.price_rub} ₽) is titled "
            f"{cheapest.title[:60]!r}, which reads as an accessory rather than {query!r} itself — verify before quoting it"
        )

    if _looks_like_another_condition(cheapest.title, query):
        warnings.append(
            f"condition: the cheapest offer ({cheapest.source}, {cheapest.price_rub} ₽) is titled "
            f"{cheapest.title[:60]!r} — that is a used, refurbished or display unit, so it is not "
            f"comparable with new goods from the other marketplaces"
        )

    # A price far under the middle of the pack is usually a different product,
    # a different configuration or a grey import. Half the median is
    # conservative on purpose: normal cross-marketplace spread does not reach it.
    if len(priced) >= 3:
        prices = sorted(offer.price_rub or 0.0 for offer in priced)
        median = prices[len(prices) // 2]
        low = cheapest.price_rub or 0.0
        if median > 0 and low < median * 0.5:
            warnings.append(
                f"price_outlier: the cheapest offer is {low} ₽ against a median of {median} ₽ across "
                f"{len(priced)} offers — check it is the same product and configuration"
            )
    return warnings


def _ranks_in_rubles(offer: MarketOffer) -> bool:
    """Whether an offer may enter the cheapest-in-rubles ranking.

    Both halves matter. price_rub is None for anything unpriced, and the
    currency check stops a future adapter from filling price_rub with a figure
    that is not roubles — the failure mode where a ¥300 listing is announced as
    the cheapest option against ₽-priced rivals.
    """
    return offer.price_rub is not None and offer.currency == "rub"


def _wb_product_url(nm_id: object) -> str:
    return f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" if nm_id else ""


# Ozon's search tiles carry display text, not numbers: "1 234 ₽" for a price and
# "4,8" for a rating, with non-breaking and narrow no-break spaces as thousands
# separators and a comma decimal mark.
def _as_price(value: object) -> float | None:
    """Coerce a marketplace price into a float, or ``None`` when there isn't one.

    Delegates to the shared ``mcp_core.coerce_price`` — the single source of the
    doctrine "never 0, never negative, never non-finite, never raises, never
    fabricates". A private duplicate once lived here and silently drifted away
    from that doctrine (cycles 24-25); the audit that followed ended here.

    Live evidence for the delegation (Ozon rendered search + card pages captured
    2026-08-07 through the operator's Chrome, raw HTML in
    ``.agent/captures/``): all 144 distinct price-like strings on the pages
    parse identically through ``coerce_price``, except range strings like
    "1 000–2 000 ₽", which the old duplicate concatenated into a fabricated
    price and the shared parser correctly rejects as ambiguous.
    """
    return R.coerce_price(value)


def _as_count(value: object) -> int | None:
    """Coerce a rating/review count, tolerating "24 086 отзывов"-style text.

    Parity with mcp_core.coerce_int where the formats overlap: non-finite
    floats (json.loads admits NaN/Infinity by default) degrade to None instead
    of letting int() raise and abort the whole tool, and a signed string is
    ambiguous — dropping the sign would fabricate a plausible-but-wrong count.
    The sign guard covers the unicode minus (U+2212) and the en/em dashes
    (U+2013/U+2014) a marketplace renders ranges with: ASCII-only [-+] let a
    range like '1 000–2 000' digit-concatenate into 10002000. Letters stay
    tolerated (they are the 'отзывов' label, not ambiguity).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    if re.search(r"[-+\u2212\u2013\u2014]\s*\d", value):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _stock_from_label(value: object) -> bool | None:
    """Read Ozon's stock hint, e.g. "осталось 3 шт".

    Only a positive statement counts as in stock. Absence of a label means Ozon
    said nothing, which is ``None`` — not ``False``, because "unknown" and "out of
    stock" are different answers to a shopper. A non-finite number is likewise
    no statement at all: NaN would compare as not-in-stock and inf as in-stock,
    two lies where the answer must stay unknown.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0 if math.isfinite(value) else None
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
    return offers


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
    return offers


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
            )
        )
    return offers


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
    return offers


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
            )
        )
    return offers


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
    return offers


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

    ## Return Format

    CompareResponse: {query, sources_queried, sources_ok, complete,
    total_offers, cheapest, price_spread_rub, offers, source_outcomes,
    warnings, server_version}. offers is ranked by everyday price_rub —
    cheapest first, offers without a rouble price after the ranked ones.
    warnings carries validation/completeness warnings.

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
    offers: list[MarketOffer] = _dedupe(offer for _, source_offers in results for offer in source_offers)

    for name in missing:
        outcomes.append(
            SourceOutcome(
                source=name,
                status="not_installed",
                detail="connector package is not installed in this environment",
            )
        )

    # Rank on everyday ruble prices only. The currency check is explicit rather
    # than implied by price_rub being None: an adapter that starts populating
    # price_rub from a foreign-currency field should fail this filter, not
    # quietly win the comparison.
    priced = sorted(
        (offer for offer in offers if _ranks_in_rubles(offer)),
        key=lambda offer: offer.price_rub or 0.0,
    )
    # Unpriced offers keep their place at the end rather than being dropped:
    # "found, but not priced in rubles" is information, and for Taobao the yuan
    # price still rides along in price_native.
    unpriced = [offer for offer in offers if not _ranks_in_rubles(offer)]
    ranked = priced + unpriced
    foreign = [offer for offer in offers if offer.currency != "rub"]

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
    warnings.extend(_relevance_warnings(text, priced))
    if foreign:
        currencies = ", ".join(sorted({offer.currency for offer in foreign}))
        warnings.append(
            f"foreign_currency: {len(foreign)} offer(s) are priced in {currencies}, not roubles — "
            f"their price is in price_native and they are excluded from the ranking and from cheapest"
        )

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

    ## Return Format

    Plain object: {installed, searchable, not_installed, notes,
    source_timeout_s, server_version, server_started_at, process_id}. notes
    explains per-source access quirks (CDP-only sources, currencies, missing
    text search).

    ## Error Format

    Never raises ToolError: pure introspection of which connector packages
    are installed — nothing here touches the network.
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
            "taobao": "reports yuan prices and is never ranked against rubles",
            "megamarket": "reachable only through the operator's Chrome (ServicePipe)",
            "lamoda": "search through the operator's Chrome; cards via anonymous GraphQL",
            "dns": "reachable only through the operator's Chrome (Qrator)",
            "citilink": "reachable only through the operator's Chrome (Qrator)",
        },
        "source_timeout_s": SOURCE_TIMEOUT_S,
        "server_version": SERVER_VERSION,
        "server_started_at": SERVER_STARTED_AT,
        "process_id": os.getpid(),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
