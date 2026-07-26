"""Detsky Mir MCP connector.

The easiest of the Russian marketplaces to read: ``api.detmir.ru`` answers plain
anonymous HTTPS — no credentials, no TLS impersonation, no browser. Verified
live Jul 2026 from a datacenter IP, which Ozon and Megamarket both refuse.

Endpoints this connector depends on:
  - ``GET /v2/products/{id}``  — single product card (~100 KB).
  - ``GET /v4/products?filter=…`` — category listing, paginated.
  - ``https://www.detmir.ru/catalog/search/?q=…`` — text search (SSR HTML).

Three upstream quirks worth knowing, each verified live:

1. **404 inside a 200.** A missing product returns HTTP 200 with
   ``{"status": 404}`` in the body. Status codes alone cannot be trusted, so the
   body is always inspected.
2. **v4, not v2, for listings.** The old ``/v2/products?filter=`` listing path is
   gone (404). Every parser written before ~2025 is broken.
3. **There is no usable text search — deliberately not exposed.** Every text key
   inside the v4 ``filter`` (``q:``, ``phrase:``, ``search:``, ``text:``) is
   silently ignored and returns the whole 300k-item catalog. The site's
   ``/catalog/search/`` route answers HTTP 404 and renders a promo carousel, not
   results — scraping it yields plausible-looking products that have nothing to do
   with the query, which is worse than no tool at all. Discovery therefore goes
   through ``detmir_categories`` -> ``detmir_category``.

Sporadic 502s and read timeouts are normal here; transport retries absorb them.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC. Use
``log_event`` (stderr) or the ``Context`` logging methods.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import urllib.parse
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
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
from mcp_core.transport import DEFAULT_USER_AGENT, RateLimiter, build_client, get_text_with_retries, proxy_from_env
from pydantic import Field

from detmir_connector.models_output import (
    DetmirCardResponse,
    DetmirCategoriesResponse,
    DetmirCategory,
    DetmirListResponse,
    DetmirProduct,
    DetmirSelfcheckEntry,
    DetmirSelfcheckResponse,
    MetaOut,
)
from detmir_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "1.1.0"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

API_BASE = "https://api.detmir.ru"
SITE_BASE = "https://www.detmir.ru"

HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": f"{SITE_BASE}/",
    "Origin": SITE_BASE,
}

# Product ids appear in page URLs as /product/index/id/<digits>/ — the only
# reliable handle on search results, since the SSR markup itself is volatile.
_PRODUCT_ID_RE = re.compile(r"/product/index/id/(\d{3,12})")

# A category alias is a URL slug. Anything else is rejected before it can be
# interpolated into a filter expression.
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")

mcp = FastMCP(name="detmir-connector", version=SERVER_VERSION)
mcp.add_middleware(RetryMiddleware(max_retries=2, base_delay=1.0))

_limiter = RateLimiter(min_gap_s=_settings.min_gap)
_cache: TTLCache[Any] = TTLCache(ttl_s=_settings.cache_ttl, max_entries=256)


def _proxy() -> str | None:
    return (_settings.proxy or "").strip() or proxy_from_env("DETMIR_PROXY")


# Region codes are ISO 3166-2:RU (RU-MOW Moscow, RU-SPE St Petersburg, ...). Validated
# rather than escaped, because the value is interpolated into a filter expression.
_REGION_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{2,4}$")


def _resolve_region(region: str | None) -> str:
    """Pick the region for one call: the argument wins, else ``DETMIR_REGION``.

    A per-call parameter matters because prices and — far more visibly — offline
    store availability differ by city. Verified live on one product: 152 stores
    carried it in RU-MOW, 37 in RU-SPE, 2 in RU-KHA. Before this parameter existed
    the only way to compare cities was restarting the server with a different
    environment variable, which no agent mid-conversation can do.
    """
    candidate = (region or "").strip().upper() or _settings.region.strip().upper()
    if not _REGION_RE.match(candidate):
        raise_tool_error(
            BadRequestError(
                f"invalid region {region or _settings.region!r}: expected an ISO code like 'RU-MOW' or 'RU-SPE'"
            )
        )
    return candidate


async def _fetch_json(url: str, label: str, ctx: Context | None) -> Any:
    """GET ``url`` and parse it as JSON, with caching and bounded retries."""

    async def fetch() -> Any:
        if ctx is not None:
            await ctx.debug(f"{label}: {url}")
        client = build_client(timeout_s=_settings.timeout, headers=HEADERS, proxy=_proxy())
        async with client:
            try:
                status, text = await get_text_with_retries(
                    client,
                    url,
                    max_bytes=_settings.max_body_bytes,
                    retries=_settings.net_retries,
                    backoff_s=_settings.net_backoff_s,
                    limiter=_limiter,
                )
            except httpx.TransportError as exc:
                raise_tool_error(TransportDownError(f"{label}: {_redact(str(exc))}", provider="detmir"))
                raise AssertionError("unreachable") from exc  # pragma: no cover

        if status == 429:
            raise_tool_error(RateLimitedError("detmir"))
        if status >= 500:
            raise_tool_error(
                TransportDownError(f"{label}: upstream HTTP {status}", provider="detmir", status_code=status)
            )
        if status != 200:
            raise_tool_error(
                TransportDownError(f"{label}: unexpected HTTP {status}", provider="detmir", status_code=status)
            )

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise_tool_error(ParserDriftError(f"{label}: response was not valid JSON ({exc})", provider="detmir"))
            raise AssertionError("unreachable") from exc  # pragma: no cover

    was_cached = _cache.get(url) is not None
    payload = await _cache.get_or_fetch(url, fetch)
    if ctx is not None and was_cached:
        await ctx.debug(f"{label}: cache hit")
    return payload


def _body_error_status(payload: Any) -> int | None:
    """Extract an error status embedded in a 200 body.

    Detsky Mir signals a missing resource with ``{"status": 404}`` and an HTTP 200,
    so this check is what stands between a caller and a silently empty product.
    """
    if isinstance(payload, dict):
        status = payload.get("status")
        if isinstance(status, int) and status >= 400:
            return status
    return None


def _first_brand(raw: Any) -> str:
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                title = str(entry.get("title") or "").strip()
                if title:
                    return title
            elif isinstance(entry, str) and entry.strip():
                return entry.strip()
    elif isinstance(raw, str):
        return raw.strip()
    return ""


def _price_from(node: Any) -> float | None:
    """Pull a rouble amount out of the several shapes prices arrive in.

    Upstream uses ``{"price": 699, "currency": "RUB"}`` in some places and a bare
    number in others; ``coerce_price`` rejects zero and negatives so a dead
    listing can never masquerade as the cheapest option.
    """
    if isinstance(node, dict):
        return R.coerce_price(R.first_present(node, "price", "value", "amount", default=None))
    return R.coerce_price(node)


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a dict, else an empty dict.

    Upstream fields drift between object, null and scalar. Coercing to an empty
    dict keeps the parser branch-free and lets a missing node read as absent
    rather than raising.
    """
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return ``value`` when it is a list, else an empty list."""
    return value if isinstance(value, list) else []


def _parse_product(raw: Any) -> DetmirProduct:
    """Flatten one raw API product into the connector's output shape."""
    if not isinstance(raw, dict):
        return DetmirProduct()

    available = _as_dict(raw.get("available"))
    online = _as_dict(available.get("online"))
    offline = _as_dict(available.get("offline"))
    stores = _as_list(offline.get("stores"))

    vendor_node = _as_dict(raw.get("vendor"))
    link_node = _as_dict(raw.get("link"))
    pictures = _as_list(raw.get("pictures"))
    picture = ""
    if pictures and isinstance(pictures[0], dict):
        picture = str(pictures[0].get("web") or pictures[0].get("original") or pictures[0].get("url") or "")

    product_id = R.coerce_int(R.first_present(raw, "id", "product_id", default=None))
    url = str(link_node.get("web_url") or "")
    if not url and product_id:
        url = f"{SITE_BASE}/product/index/id/{product_id}/"

    prices_node = _as_dict(raw.get("prices"))
    price = _price_from(raw.get("price"))
    if price is None:
        price = _price_from(R.first_present(prices_node, "sale", "current", default=None))
    if price is None:
        price = _price_from(raw.get("final_price"))

    return DetmirProduct(
        product_id=product_id,
        title=str(R.first_present(raw, "title", "name", default="") or ""),
        article=str(R.first_present(raw, "article", "code", default="") or ""),
        brand=_first_brand(raw.get("brands") or raw.get("brand")),
        price_rub=price,
        old_price_rub=_price_from(raw.get("old_price")),
        discount_percent=R.coerce_int(R.first_present(raw, "discount_percentage", "discount", default=None)),
        rating=R.coerce_price(R.first_present(raw, "rating", default=None)),
        review_count=R.coerce_int(R.first_present(raw, "review_count", "reviews_count", default=None)),
        questions_count=R.coerce_int(R.first_present(raw, "questions_count", default=None)),
        availability=str(R.first_present(raw, "availability", default="") or ""),
        available_online=bool(online) if available else None,
        available_offline=bool(offline) if available else None,
        store_count=len(stores) if offline else None,
        is_marketplace=raw.get("is_from_marketplace") if isinstance(raw.get("is_from_marketplace"), bool) else None,
        vendor=str(vendor_node.get("title") or "") if vendor_node else "",
        url=url,
        picture=picture,
    )


def _product_node(payload: Any) -> dict | None:
    """Unwrap the card payload, which nests the product under ``item``."""
    if isinstance(payload, dict):
        item = payload.get("item")
        if isinstance(item, dict):
            return item
        if "title" in payload or "id" in payload:
            return payload
    return None


@mcp.tool(
    name="detmir_card",
    annotations=ToolAnnotations(
        title="Detsky Mir Product Card",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def detmir_card(
    product_id: Annotated[
        int,
        Field(
            gt=0,
            description="Numeric Detsky Mir product id — the digits in /product/index/id/<id>/.",
        ),
    ],
    region: Annotated[
        str,
        Field(
            default="",
            max_length=16,
            description="ISO region code for prices and offline stock, e.g. 'RU-MOW' or 'RU-SPE'. Defaults to DETMIR_REGION.",
        ),
    ] = "",
    ctx: Context | None = None,
) -> DetmirCardResponse:
    """Fetch price, rating, stock and seller for one Detsky Mir product.

    Covers the kids-and-baby category that the general marketplaces cover
    unevenly, and distinguishes Detsky Mir's own stock from third-party
    marketplace sellers.

    **Region matters most here.** ``store_count`` is the number of physical shops
    holding the item, and it swings hard by city — one item verified live sat in
    152 Moscow stores, 37 in St Petersburg, 2 in Khabarovsk. Pass ``region`` to
    ask about a specific city; it overrides ``DETMIR_REGION`` for this call only,
    so one session can compare cities.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    region_used = _resolve_region(region)
    log_event("detmir_card.start", product_id=product_id, region=region_used)
    if ctx is not None:
        await ctx.info(f"detmir_card: product_id={product_id} region={region_used}")

    # The region MUST travel as filter=withregion:..., not ?withregion=...:
    # verified live that the query-parameter form is silently ignored, which left
    # this tool reporting store_count=0 for every city while labelling the
    # response with the configured region. The filter form returns the real
    # per-city counts.
    #
    # It also belongs in the URL because _fetch_json caches by URL — passing the
    # region out of band would let a Moscow response answer a St Petersburg
    # request from cache.
    query = urllib.parse.urlencode({"filter": f"withregion:{region_used}"})
    url = f"{API_BASE}/v2/products/{product_id}?{query}"
    payload = await _fetch_json(url, "detmir_card", ctx)

    body_status = _body_error_status(payload)
    if body_status == 404:
        # HTTP 200 with a 404 body — the upstream quirk this connector guards against.
        raise_tool_error(NotFoundError(f"no Detsky Mir product with id={product_id}", provider="detmir"))
    if body_status is not None:
        raise_tool_error(TransportDownError(f"detmir_card: upstream body status {body_status}", provider="detmir"))

    node = _product_node(payload)
    if node is None:
        raise_tool_error(
            ParserDriftError(
                f"detmir_card: could not locate a product in the payload (keys={list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__})",
                provider="detmir",
            )
        )

    product = _parse_product(node)
    warnings: list[str] = []
    if not product.title:
        warnings.append("no_title: upstream returned a product without a title")
    if product.price_rub is None:
        warnings.append("no_price: no usable price in the payload (item may be delisted)")

    log_event("detmir_card.done", product_id=product_id, price=product.price_rub, warnings=len(warnings))
    return DetmirCardResponse(
        product=product,
        region=region_used,
        meta=MetaOut(source="detmir_card", healthy=not warnings, warnings=warnings, cached=_cache.get(url) is not None),
    )


@mcp.tool(
    name="detmir_category",
    annotations=ToolAnnotations(
        title="Detsky Mir Category Listing",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def detmir_category(
    alias: Annotated[
        str,
        Field(
            min_length=1,
            max_length=80,
            description="Category slug from a catalog URL, e.g. 'pups' in /catalog/index/name/pups/.",
        ),
    ],
    limit: Annotated[int, Field(default=20, ge=1, le=60, description="Items per page.")] = 20,
    offset: Annotated[int, Field(default=0, ge=0, le=10_000, description="Items to skip, for pagination.")] = 0,
    region: Annotated[
        str,
        Field(
            default="",
            max_length=16,
            description="ISO region code for prices and offline stock, e.g. 'RU-MOW' or 'RU-SPE'. Defaults to DETMIR_REGION.",
        ),
    ] = "",
    ctx: Context | None = None,
) -> DetmirListResponse:
    """List products in a Detsky Mir category, with the total match count.

    This is the reliable way to enumerate the catalog: unlike text search, it is a
    real JSON endpoint with proper pagination and an upstream total, so it
    supports "what's available and how much does it cost" without scraping.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    slug = (alias or "").strip().lower()
    if not _ALIAS_RE.match(slug):
        # The alias lands inside a filter expression, so it is validated as a
        # slug rather than escaped — no separators, no traversal, no injection.
        raise_tool_error(
            BadRequestError(
                f"invalid category alias {alias!r}: expected a URL slug like 'pups' (lowercase letters, digits, - and _)"
            )
        )

    region_used = _resolve_region(region)
    log_event("detmir_category.start", alias=slug, limit=limit, offset=offset, region=region_used)
    if ctx is not None:
        await ctx.info(f"detmir_category: {slug} limit={limit} offset={offset} region={region_used}")

    filter_expr = f"categories[].alias:{slug};withregion:{region_used}"
    query = urllib.parse.urlencode(
        {
            "filter": filter_expr,
            "limit": limit,
            "offset": offset,
            "meta": "*",
        }
    )
    url = f"{API_BASE}/v4/products?{query}"
    payload = await _fetch_json(url, "detmir_category", ctx)

    if isinstance(payload, list):
        # Without meta=* the endpoint returns a bare array; tolerate both shapes.
        items_raw: list[Any] = payload
        meta_node: dict[str, Any] = {}
    elif isinstance(payload, dict):
        body_status = _body_error_status(payload)
        if body_status is not None:
            raise_tool_error(
                TransportDownError(f"detmir_category: upstream body status {body_status}", provider="detmir")
            )
        items_raw = _as_list(payload.get("items"))
        meta_node = _as_dict(payload.get("meta"))
    else:
        raise_tool_error(
            ParserDriftError(
                f"detmir_category: expected an object or array, got {type(payload).__name__}", provider="detmir"
            )
        )
        raise AssertionError("unreachable")  # pragma: no cover

    products = [_parse_product(item) for item in items_raw]
    total = R.coerce_int(R.first_present(meta_node, "length", "total", default=None))
    title = str(meta_node.get("title") or "")

    warnings: list[str] = []
    if not products:
        warnings.append(f"empty: no products for alias {slug!r} — check the slug against a catalog URL")

    log_event("detmir_category.done", alias=slug, returned=len(products), total=total)
    return DetmirListResponse(
        query=slug,
        mode="category",
        total_available=total,
        category_title=title,
        returned=len(products),
        offset=offset,
        items=products,
        region=region_used,
        meta=MetaOut(source="detmir_category", healthy=not warnings, warnings=warnings),
    )


@mcp.tool(
    name="detmir_categories",
    annotations=ToolAnnotations(
        title="Detsky Mir Catalog Categories",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def detmir_categories(
    parent: Annotated[
        str,
        Field(
            default="top",
            max_length=80,
            description="'top' for the 27 top-level sections, or a parent category id to list its children.",
        ),
    ] = "top",
    limit: Annotated[int, Field(default=30, ge=1, le=100, description="Maximum categories to return.")] = 30,
    region: Annotated[
        str,
        Field(
            default="",
            max_length=16,
            description="ISO region code for prices and offline stock, e.g. 'RU-MOW' or 'RU-SPE'. Defaults to DETMIR_REGION.",
        ),
    ] = "",
    ctx: Context | None = None,
) -> DetmirCategoriesResponse:
    """Browse the Detsky Mir catalog tree and get the aliases `detmir_category` needs.

    This is the discovery step: Detsky Mir has **no working text search** (see the
    module docstring), so the way to find products is to walk the tree and then
    list a category. Each node carries its `alias` and a `products_count`, so you
    can see where the inventory actually is before fetching a listing.

    ## Error Format

    On validation or transport/parse failure, raises ToolError with a JSON
    message describing the error code and whether it is retryable.
    """
    region_used = _resolve_region(region)
    requested = (parent or "top").strip()
    if requested.lower() in ("", "top", "root", "all"):
        filter_expr = f"level:1;withregion:{region_used}"
        resolved = "top"
    elif requested.isdigit():
        filter_expr = f"parent_id:{requested};withregion:{region_used}"
        resolved = requested
    else:
        # Only 'top' or a numeric id are accepted: the upstream filter is a
        # semicolon-delimited expression, so free text could alter its meaning.
        raise_tool_error(
            BadRequestError(
                f"invalid parent {parent!r}: pass 'top' or a numeric category id "
                "(get ids from a previous detmir_categories call)"
            )
        )
        raise AssertionError("unreachable")  # pragma: no cover

    log_event("detmir_categories.start", parent=resolved, limit=limit, region=region_used)
    if ctx is not None:
        await ctx.info(f"detmir_categories: parent={resolved} limit={limit} region={region_used}")

    query = urllib.parse.urlencode({"filter": filter_expr, "limit": limit, "meta": "*"})
    url = f"{API_BASE}/v2/categories?{query}"
    payload = await _fetch_json(url, "detmir_categories", ctx)

    if not isinstance(payload, dict):
        raise_tool_error(
            ParserDriftError(f"detmir_categories: expected an object, got {type(payload).__name__}", provider="detmir")
        )
        raise AssertionError("unreachable")  # pragma: no cover

    body_status = _body_error_status(payload)
    if body_status is not None:
        raise_tool_error(
            TransportDownError(f"detmir_categories: upstream body status {body_status}", provider="detmir")
        )

    # This endpoint returns its rows under "data", unlike /v4/products which uses
    # "items" — both are accepted so a rename upstream degrades to a warning.
    rows = _as_list(payload.get("data")) or _as_list(payload.get("items"))
    meta_node = _as_dict(payload.get("meta"))

    items: list[DetmirCategory] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        items.append(
            DetmirCategory(
                category_id=R.coerce_int(R.first_present(raw, "id", default=None)),
                alias=str(R.first_present(raw, "alias", "code", default="") or ""),
                title=str(R.first_present(raw, "title", "name", default="") or ""),
                full_name=str(R.first_present(raw, "full_name", default="") or ""),
                level=R.coerce_int(R.first_present(raw, "level", default=None)),
                products_count=R.coerce_int(R.first_present(raw, "products_count", default=None)),
                parent_id=R.coerce_int(R.first_present(raw, "parentId", "parent_id", default=None)),
                url=str(R.first_present(raw, "web_url", "url", default="") or ""),
            )
        )

    warnings: list[str] = []
    if not items:
        warnings.append(f"empty: no categories under {resolved!r}")

    log_event("detmir_categories.done", parent=resolved, returned=len(items))
    return DetmirCategoriesResponse(
        parent=resolved,
        returned=len(items),
        total_available=R.coerce_int(R.first_present(meta_node, "total", "length", default=None)),
        items=items,
        region=region_used,
        meta=MetaOut(source="detmir_categories", healthy=not warnings, warnings=warnings),
    )


@mcp.tool(
    name="detmir_selfcheck",
    annotations=ToolAnnotations(
        title="Detsky Mir Selfcheck",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def detmir_selfcheck(ctx: Context | None = None) -> DetmirSelfcheckResponse:
    """Probe every Detsky Mir endpoint family and report a tri-state verdict.

    ``success`` — everything answered with the expected shape.
    ``drift_detected`` — an endpoint answered but the payload no longer parses;
    the connector needs updating. ``inconclusive`` — transport or geo blocking
    prevented a verdict, which says nothing about the parsers.

    Run it after install and whenever results look wrong.
    """
    log_event("detmir_selfcheck.start")
    if ctx is not None:
        await ctx.info("detmir_selfcheck: probing endpoint families")

    checks: dict[str, DetmirSelfcheckEntry] = {}

    async def probe(name: str, coro: Any, verify: Any) -> None:
        try:
            result = await coro
        except Exception as exc:
            text = _redact(str(exc))
            drift = "parser_drift" in text or "not valid JSON" in text
            checks[name] = DetmirSelfcheckEntry(
                state="drift" if drift else "inconclusive",
                detail=text[:200],
                notes=["upstream reachable but unparseable"]
                if drift
                else ["transport or geo block — parsers untested"],
            )
            return
        ok, detail = verify(result)
        checks[name] = DetmirSelfcheckEntry(state="healthy" if ok else "drift", detail=detail)

    await probe(
        "card",
        detmir_card(product_id=_settings.selfcheck_product_id, ctx=None),
        lambda r: (
            bool(r.product.title) and r.product.price_rub is not None,
            f"title={r.product.title[:40]!r} price={r.product.price_rub}",
        ),
    )
    await probe(
        "category",
        detmir_category(alias=_settings.selfcheck_category, limit=3, offset=0, ctx=None),
        lambda r: (
            r.returned > 0 and any(p.price_rub for p in r.items),
            f"returned={r.returned} total={r.total_available}",
        ),
    )
    await probe(
        "categories",
        detmir_categories(parent="top", limit=5, ctx=None),
        lambda r: (
            r.returned > 0 and any(c.alias for c in r.items),
            f"returned={r.returned} total={r.total_available}",
        ),
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

    log_event("detmir_selfcheck.done", status=status, checks=len(checks))
    return DetmirSelfcheckResponse(
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
