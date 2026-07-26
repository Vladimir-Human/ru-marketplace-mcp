"""Yandex Market MCP connector.

Yandex Market exposes no usable JSON API — ``/api/resolve`` answers 403, the
internal product endpoint speaks gRPC, and the old public Content API is dead
(502). What it does serve, to ordinary clients and without a captcha, is fully
server-rendered HTML that embeds its own widget state as JSON. This connector
reads that state; the extraction rules live in ``yandex_connector.ssr``.

Pages it depends on (all verified live Jul 2026 from a datacenter IP):
  - ``https://market.yandex.ru/search?text=…&page=N`` — search (~2 MB)
  - ``https://market.yandex.ru/product/{id}`` — card + first ~13 reviews (~2.5 MB)

Behaviours that shape this code:

**Two prices, always.** Yandex leads with a subscriber price ("с Плюсом") that
runs 25-30% below the everyday price. Both are reported separately, because
quoting only the subscriber price misstates what most buyers pay.

**Reviews come from the card, not /reviews.** The dedicated reviews URL renders
zero reviews server-side (they load over XHR), while the product page ships the
first ~13 complete with pros/cons/dates/votes.

**Transient 302s.** Roughly one request in ten returns 302 with an empty body;
an immediate retry succeeds. Handled by the transport's retry budget.

**Captcha detection needs real markers.** Every healthy page contains an empty
``captchaService`` placeholder, so matching the substring "captcha" produces
false positives. Only SmartCaptcha markers count.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC. Use
``log_event`` (stderr) or the ``Context`` logging methods.
"""

from __future__ import annotations

import datetime
import os
import urllib.parse
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core.cache import TTLCache
from mcp_core.errors import (
    BadRequestError,
    NotFoundError,
    ParserDriftError,
    RateLimitedError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport import RateLimiter, build_client, get_text_with_retries, proxy_from_env
from pydantic import Field

from yandex_connector import ssr
from yandex_connector.models_output import (
    MetaOut,
    YandexCardResponse,
    YandexProduct,
    YandexReview,
    YandexSearchResponse,
    YandexSelfcheckEntry,
    YandexSelfcheckResponse,
)
from yandex_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "1.1.0"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://market.yandex.ru"

# A realistic desktop Chrome UA plus Russian locale is required: without them
# Yandex may refuse or serve a stripped page.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": f"{SITE_BASE}/",
    "Upgrade-Insecure-Requests": "1",
}

# 302 with an empty body is Yandex's transient hiccup, not a redirect worth
# following — retried alongside the usual gateway statuses.
_RETRY_STATUSES = frozenset({302, 502, 503, 504})

mcp = FastMCP(name="yandex-connector", version=SERVER_VERSION)
mcp.add_middleware(RetryMiddleware(max_retries=2, base_delay=1.0))

_limiter = RateLimiter(min_gap_s=_settings.min_gap)
_cache: TTLCache[str] = TTLCache(ttl_s=_settings.cache_ttl, max_entries=64)


def _proxy() -> str | None:
    return (_settings.proxy or "").strip() or proxy_from_env("YANDEX_PROXY")


async def _fetch_html(url: str, label: str, ctx: Context | None) -> str:
    """GET a Yandex Market page and return its HTML, cached and retry-aware."""

    async def fetch() -> str:
        if ctx is not None:
            await ctx.debug(f"{label}: {url}")
        client = build_client(timeout_s=_settings.timeout, headers=HEADERS, proxy=_proxy())
        async with client:
            try:
                status, html = await get_text_with_retries(
                    client,
                    url,
                    max_bytes=_settings.max_body_bytes,
                    retries=_settings.net_retries,
                    backoff_s=_settings.net_backoff_s,
                    limiter=_limiter,
                    # A 3xx/4xx body can still be the real page here, so it is never
                    # truncated as a mere error payload.
                    error_body_max_bytes=None,
                    retry_statuses=_RETRY_STATUSES,
                )
            except httpx.TransportError as exc:
                raise_tool_error(TransportDownError(f"{label}: {_redact(str(exc))}", provider="yandex"))
                raise AssertionError("unreachable") from exc  # pragma: no cover

        if status == 429:
            raise_tool_error(RateLimitedError("yandex"))
        if status == 404:
            raise_tool_error(NotFoundError(f"{label}: page not found", provider="yandex"))
        if status >= 500 or (status == 302 and not html.strip()):
            raise_tool_error(
                TransportDownError(
                    f"{label}: upstream HTTP {status} after retries", provider="yandex", status_code=status
                )
            )
        if not html.strip():
            raise_tool_error(TransportDownError(f"{label}: empty response body", provider="yandex"))
        return html

    return await _cache.get_or_fetch(url, fetch)


def _guard_parse_status(status: str, label: str) -> None:
    """Turn a parse status into the right error, or return for usable results."""
    if status == ssr.ParseStatus.CAPTCHA:
        raise_tool_error(
            RateLimitedError("yandex", retry_after_s=300.0),
        )
    if status == ssr.ParseStatus.NO_PRODUCTS_FOUND:
        # Neither products nor the "nothing found" banner: the page rendered
        # something we no longer understand. Report drift rather than "no results".
        raise_tool_error(
            ParserDriftError(
                f"{label}: page carried neither products nor an empty-result banner — "
                "the SSR structure has likely changed",
                provider="yandex",
            )
        )


def _to_product(raw: dict[str, Any]) -> YandexProduct:
    return YandexProduct(
        product_id=str(raw.get("product_id") or ""),
        sku_id=str(raw.get("sku_id") or ""),
        title=str(raw.get("title") or ""),
        brand=str(raw.get("brand") or ""),
        seller=str(raw.get("seller") or ""),
        price_rub=raw.get("price_rub"),
        price_with_plus=raw.get("price_with_plus"),
        price_old_rub=raw.get("price_old_rub"),
        currency=str(raw.get("currency") or "RUR"),
        rating=raw.get("rating"),
        rating_count=raw.get("rating_count"),
        in_stock=raw.get("in_stock"),
        is_express=bool(raw.get("is_express")),
        url=str(raw.get("url") or ""),
        image=str(raw.get("image") or ""),
    )


@mcp.tool(
    name="yandex_search",
    annotations=ToolAnnotations(
        title="Yandex Market Search",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def yandex_search(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=200,
            description="Free-text search query in Russian, e.g. 'iphone 15' or 'стиральная машина узкая'.",
        ),
    ],
    page: Annotated[int, Field(default=1, ge=1, le=30, description="Page number, 1-based.")] = 1,
    limit: Annotated[int, Field(default=12, ge=1, le=48, description="Maximum products to return from the page.")] = 12,
    ctx: Context | None = None,
) -> YandexSearchResponse:
    """Search Yandex Market and return products with both prices, ratings and sellers.

    Yandex Market aggregates many sellers per product, which makes it the best
    single source for "what does this cost right now" across the Russian market —
    including goods Wildberries and Ozon do not carry.

    Each result reports `price_rub` (what anyone pays) and `price_with_plus`
    (requires a Yandex Plus subscription, typically 25-30% lower). Prefer
    `price_rub` when quoting a price to a person.

    Note `rating_count` counts star ratings, not written reviews; the written
    count is available per product via `yandex_card`.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    text = (query or "").strip()
    if len(text) < 2:
        raise_tool_error(BadRequestError("query must be at least 2 characters"))

    log_event("yandex_search.start", query=text, page=page, limit=limit)
    if ctx is not None:
        await ctx.info(f"yandex_search: {text!r} page={page}")

    params = {"text": text}
    if page > 1:
        params["page"] = str(page)
    url = f"{SITE_BASE}/search?{urllib.parse.urlencode(params)}"

    html = await _fetch_html(url, "yandex_search", ctx)
    parsed = ssr.parse_search(html)
    _guard_parse_status(parsed["status"], "yandex_search")

    # Dedupe by product_id BEFORE applying the limit. Yandex's SSR payload can
    # carry the same product more than once on a page — the parser keys on
    # snippet (an on-screen position), and one product legitimately occupies
    # several of those as different offers. Verified live: a 3-page walk of
    # "ноутбук" returned 1 repeat between pages 1-2 and 3 between pages 2-3.
    #
    # Slicing first would let a duplicate consume part of the caller's budget, so
    # limit=40 could yield 37 distinct products with no indication why. Deduping
    # first means the limit always describes distinct products.
    #
    # Order is preserved: Yandex's ranking is the product of the search, and
    # re-sorting it would discard information the caller asked for.
    items: list[YandexProduct] = []
    duplicates_dropped = 0
    seen_product_ids: set[str] = set()
    for raw in parsed["items"]:
        if len(items) >= limit:
            break
        product = _to_product(raw)
        # A blank product_id means the parser could not resolve one. Those cannot
        # be compared for identity, so they pass through rather than collapsing
        # into a single "" bucket that would drop unrelated products.
        if product.product_id:
            if product.product_id in seen_product_ids:
                duplicates_dropped += 1
                continue
            seen_product_ids.add(product.product_id)
        items.append(product)

    warnings: list[str] = []
    extraction = "ssr"

    if parsed["status"] == ssr.ParseStatus.OK_LDJSON_ONLY:
        # The widget state was unreadable but schema.org markup carried the first
        # screen — usable, with fewer fields and only the subscriber price.
        extraction = "ld+json"
        warnings.append(
            "degraded: widget state unavailable, fell back to schema.org markup "
            "(no seller/brand, price is the subscription price)"
        )
    if parsed["status"] == ssr.ParseStatus.EMPTY:
        warnings.append(f"no_results: Yandex Market found nothing for {text!r}")

    log_event(
        "yandex_search.done",
        query=text,
        returned=len(items),
        total=parsed.get("total"),
        status=parsed["status"],
        duplicates_dropped=duplicates_dropped,
    )
    return YandexSearchResponse(
        query=parsed.get("query") or text,
        page=parsed.get("page") or page,
        page_count=parsed.get("page_count"),
        total_available=parsed.get("total"),
        has_next_page=bool(parsed.get("has_next_page")),
        returned=len(items),
        items=items,
        meta=MetaOut(source="yandex_search", healthy=not warnings, warnings=warnings, extraction=extraction),
    )


@mcp.tool(
    name="yandex_card",
    annotations=ToolAnnotations(
        title="Yandex Market Product Card",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def yandex_card(
    product_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=32,
            description="Numeric Yandex Market product id — take it from yandex_search results.",
        ),
    ],
    include_reviews: Annotated[
        bool, Field(default=True, description="Include the server-rendered reviews (first ~13).")
    ] = True,
    ctx: Context | None = None,
) -> YandexCardResponse:
    """Fetch full detail for a Yandex Market product: prices, rating breakdown, reviews.

    Two things here are hard to get anywhere else. The **star distribution**
    (`rating_stars`) shows whether a 4.8 average hides a cluster of one-star
    complaints. And **reviews arrive with the card** in one request, complete with
    pros, cons and helpfulness votes.

    Reviews are capped at the ~13 Yandex renders server-side; the remainder load
    through an API this connector deliberately does not touch.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    pid = (product_id or "").strip()
    if not pid.isdigit():
        # The id is interpolated into a URL path, so it is validated as digits
        # rather than escaped — no traversal, no query injection.
        raise_tool_error(
            BadRequestError(
                f"invalid product_id {product_id!r}: expected digits only (take the id from a yandex_search result)"
            )
        )

    log_event("yandex_card.start", product_id=pid, include_reviews=include_reviews)
    if ctx is not None:
        await ctx.info(f"yandex_card: product_id={pid}")

    url = f"{SITE_BASE}/product/{pid}"
    html = await _fetch_html(url, "yandex_card", ctx)
    parsed = ssr.parse_card(html)
    _guard_parse_status(parsed["status"], "yandex_card")

    if not parsed.get("title"):
        raise_tool_error(
            ParserDriftError(
                f"yandex_card: no product title found for id={pid} — the page may not be a product page",
                provider="yandex",
            )
        )

    warnings: list[str] = []
    if parsed.get("rating") is None:
        # Real and common: a card defaulting to a resale/clearance offer carries no
        # rating at all. Say so instead of implying the product is unrated.
        warnings.append(
            "no_rating: this card's default offer has no ratings "
            "(common for resale/clearance offers) — search results usually carry one"
        )
    if parsed.get("price_rub") is None:
        warnings.append("no_price: no usable price on the page (item may be unavailable)")

    reviews = [YandexReview(**review) for review in parsed.get("reviews", [])] if include_reviews else []

    log_event(
        "yandex_card.done",
        product_id=pid,
        price=parsed.get("price_rub"),
        reviews=len(reviews),
        warnings=len(warnings),
    )
    return YandexCardResponse(
        product_id=parsed.get("product_id") or pid,
        sku_id=parsed.get("sku_id", ""),
        title=parsed.get("title", ""),
        brand=parsed.get("brand", ""),
        seller=parsed.get("seller", ""),
        description=parsed.get("description", ""),
        image=parsed.get("image", ""),
        price_rub=parsed.get("price_rub"),
        price_with_plus=parsed.get("price_with_plus"),
        price_before_discount_rub=parsed.get("price_before_discount_rub"),
        discount_percent=parsed.get("discount_percent"),
        currency=parsed.get("currency", "RUR"),
        offers_count=parsed.get("offers_count"),
        rating=parsed.get("rating"),
        rating_count=parsed.get("rating_count"),
        review_count=parsed.get("review_count"),
        rating_stars=parsed.get("rating_stars", {}),
        reviews=reviews,
        url=url,
        meta=MetaOut(source="yandex_card", healthy=not warnings, warnings=warnings, extraction="ssr"),
    )


@mcp.tool(
    name="yandex_selfcheck",
    annotations=ToolAnnotations(
        title="Yandex Market Selfcheck",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def yandex_selfcheck(ctx: Context | None = None) -> YandexSelfcheckResponse:
    """Probe Yandex Market's search and card pages and report a tri-state verdict.

    ``success`` — the SSR state parsed as expected. ``drift_detected`` — pages
    load but no longer parse, so the extraction rules need updating.
    ``inconclusive`` — a transport block, geo restriction or captcha prevented a
    verdict; that says nothing about the parsers.

    This matters more here than for a JSON API: SSR extraction is inherently
    coupled to Yandex's front-end, so drift is a question of when.
    """
    log_event("yandex_selfcheck.start")
    if ctx is not None:
        await ctx.info("yandex_selfcheck: probing search and card pages")

    checks: dict[str, YandexSelfcheckEntry] = {}
    probe_product_id: str | None = None

    # 1) Search — also supplies a live product id for the card probe, so the
    #    canary never depends on a hardcoded SKU that may be delisted.
    try:
        search = await yandex_search(query=_settings.selfcheck_query, page=1, limit=5, ctx=None)
        priced = [item for item in search.items if item.price_rub or item.price_with_plus]
        healthy = bool(search.items) and bool(priced)
        checks["search"] = YandexSelfcheckEntry(
            state="healthy" if healthy else "drift",
            detail=f"returned={search.returned} total={search.total_available} priced={len(priced)}",
            notes=[] if healthy else ["search parsed but produced no priced items"],
        )
        if search.items:
            probe_product_id = search.items[0].product_id or None
    except Exception as exc:
        text = _redact(str(exc))
        drift = "parser_drift" in text
        checks["search"] = YandexSelfcheckEntry(
            state="drift" if drift else "inconclusive",
            detail=text[:200],
            notes=["SSR structure changed"] if drift else ["transport, geo block or captcha — parsers untested"],
        )

    # 2) Card, using the id search just produced.
    if probe_product_id:
        try:
            card = await yandex_card(product_id=probe_product_id, include_reviews=True, ctx=None)
            healthy = bool(card.title) and (card.price_rub is not None or card.price_with_plus is not None)
            checks["card"] = YandexSelfcheckEntry(
                state="healthy" if healthy else "drift",
                detail=f"title={card.title[:30]!r} price={card.price_rub} reviews={len(card.reviews)}",
                notes=[] if healthy else ["card parsed but carried no title or price"],
            )
        except Exception as exc:
            text = _redact(str(exc))
            drift = "parser_drift" in text
            checks["card"] = YandexSelfcheckEntry(
                state="drift" if drift else "inconclusive",
                detail=text[:200],
                notes=["SSR structure changed"] if drift else ["transport or captcha — parsers untested"],
            )
    else:
        checks["card"] = YandexSelfcheckEntry(
            state="inconclusive",
            detail="skipped: search returned no product id to probe",
            notes=["card probe depends on a live id from search"],
        )

    states = {entry.state for entry in checks.values()}
    if "drift" in states:
        status = "drift_detected"
    elif states == {"healthy"}:
        status = "success"
    else:
        status = "inconclusive"

    try:
        tool_count = len(await mcp.list_tools())
    except Exception:
        tool_count = 0

    log_event("yandex_selfcheck.done", status=status, checks=len(checks))
    return YandexSelfcheckResponse(
        status=status,
        checks=checks,
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=os.getpid(),
        config_loaded=True,
        tool_count=tool_count,
        cache_stats=_cache.stats.as_dict(),
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
