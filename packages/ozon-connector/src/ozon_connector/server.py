"""Ozon MCP connector.

Two-tier strategy (Nov 2026 verified on the operator's residential IP):
  Tier 1: curl_cffi impersonate=chrome124 — usually 403 because Cloudflare __cf_bm
    JS challenge. Tried first since 0 setup cost.
  Tier 2: Chrome CDP — connect to the operator's logged-in browser at 127.0.0.1:9222,
    fetch composer-api.bx from inside live session. Required Chrome started
    via scripts/start_chrome_cdp.ps1 (Windows) or scripts/start_chrome_cdp.sh (Linux/macOS).

SECURITY: Tier-2 fetch() runs INSIDE the operator's authenticated ozon.ru session.
sku_or_path inputs are normalized + allowlisted to prevent SSRF that would
exfiltrate /api/v3/personal/orders or navigate to /i/logout.

NEVER use print() in stdio MCP — corrupts JSON-RPC. Use ctx.info/error.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pickle
import posixpath
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, cast

import httpx
from curl_cffi import requests as cffi
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
from mcp_core.cache import TTLCache
from mcp_core.errors import (
    BadRequestError,
    ParserDriftError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.process import (
    safe_child_env,
    terminate_process_tree,
    worker_process_kwargs,
)
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport import proxy_from_env
from mcp_core.transport.chrome_cdp import NavBlocked, cdp_setup_hint, open_page
from pydantic import Field

from ozon_connector.models_output import (
    OzonCardResponse,
    OzonReviewsResponse,
    OzonSearchResponse,
    OzonSelfcheckResponse,
)
from ozon_connector.settings import get_settings

SERVER_VERSION = "1.1.0"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

mcp = FastMCP(
    name="ozon-connector",
    version=SERVER_VERSION,
)
mcp.add_middleware(RetryMiddleware(max_retries=3, base_delay=1.0))

# Strict allowlist for paths we are willing to navigate inside the operator's authenticated
# ozon.ru session. /product/<digits>/ and /search/?text=... are the only routes
# the tools actually need; everything else is a potential SSRF vector.
PRODUCT_PATH_RE = re.compile(r"^/product/\d+/?$")
PRODUCT_REVIEWS_PATH_RE = re.compile(r"^/product/\d+/reviews/?$")
# Paginated reviews path is built INTERNALLY from validated components (never the
# raw upstream nextButton string) before it reaches fetch() in the operator's session.
# Two accepted shapes: first page with optional ?sort=, and deep page with
# ?page=&page_key=&sort=.
PRODUCT_REVIEWS_PAGED_RE = re.compile(
    r"^/product/\d+/reviews/"
    r"(\?sort=[a-z_]+|\?page=\d+&page_key=[A-Za-z0-9_=\-]+&sort=[a-z_]+)?$"
)
_REVIEW_PAGE_KEY_RE = re.compile(r"^[A-Za-z0-9_=\-]+$")
# Verified live (Nov 2026) from webListReviews.sortings[].value — these are the
# ONLY three the product page itself offers. Friendly aliases map onto them.
_ALLOWED_REVIEW_SORTS = {
    "published_at_desc",  # "новые и полезные" (default)
    "score_desc",  # "с высокой оценкой"
    "score_asc",  # "с низкой оценкой" — surfaces 1-star complaints first
}
_REVIEW_SORT_ALIASES = {
    "recent": "published_at_desc",
    "default": "published_at_desc",
    "best": "score_desc",
    "highest": "score_desc",
    "worst": "score_asc",
    "lowest": "score_asc",
    "complaints": "score_asc",
}
PRODUCT_SLUG_RE = re.compile(r"^/product/(?:[^/]*-)?(\d+)/?$")

OZON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    "Referer": "https://www.ozon.ru/",
    "x-o3-app-name": "dweb_client",
    "x-o3-app-version": "release_2-7-1",
    "x-o3-page-type": "detail",
}
_settings = get_settings()
# auto-latest WITHIN the installed curl-cffi version; uv.lock pins that version.
# Run `uv lock --upgrade-package curl-cffi --project ozon` quarterly to refresh
# the JA3/JA4 fingerprint pool. Override via OZON_IMPERSONATE.
IMPERSONATE = _settings.impersonate
TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes  # 50 MB hard cap default; OZON_MAX_BODY_BYTES
_SELFCHECK_SKU = _settings.selfcheck_sku  # golden-fixture baseline SKU; OZON_SELFCHECK_SKU

# Polite rate limit (Ozon Cloudflare more aggressive than WB v4)
_last_request_ts = 0.0
_min_gap = _settings.min_gap
_rate_lock = asyncio.Lock()
_cdp_lock = asyncio.Lock()  # serialize CDP tab creation

# Caches the (status, body) of successful composer reads, keyed by canonical path.
# Small by design: Ozon bodies are large, and an agent revisits a handful of
# products, not hundreds. OZON_CACHE_TTL=0 disables.
_cache: TTLCache[tuple[int, str]] = TTLCache(ttl_s=_settings.cache_ttl, max_entries=64)


def _proxy() -> str | None:
    """Resolve Ozon's tier-1 proxy: explicit ``OZON_PROXY`` first, then the standard vars.

    Tier 2 is intentionally unaffected. That tier runs inside a Chrome instance
    the operator started themselves, so its egress is that browser's business —
    routing it from here would silently contradict the user's own browser config.
    """
    return (_settings.proxy or "").strip() or proxy_from_env("OZON_PROXY")


class _SyncCallTimeout(TimeoutError):
    pass


class _SyncCallError(RuntimeError):
    pass


_SYNC_WORKER_CODE = r"""
import importlib
import pickle
import re
import sys
from urllib.parse import urlsplit, urlunsplit

def _resolve(module_name, qualname):
    obj = importlib.import_module(module_name)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj

def _redact_url(url):
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return "<redacted-url>"
        path = parts.path or "/"
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{parts.port}" if parts.port is not None else host
        return urlunsplit((parts.scheme, netloc, path, "<redacted-query>" if parts.query else "", ""))
    except Exception:
        return "<redacted-url>"

def _redact(text):
    return re.sub(r"https?://[^\s'\"<>]+", lambda m: _redact_url(m.group(0)), str(text or ""))

try:
    out_path = sys.argv[1]
    module_name, qualname, args = pickle.loads(sys.stdin.buffer.read())
    result = _resolve(module_name, qualname)(*args)
    payload = ("ok", result)
except BaseException as exc:
    payload = ("error", (exc.__class__.__name__, _redact(exc)))
with open(out_path, "wb") as fh:
    fh.write(pickle.dumps(payload))
"""


def _sync_call_in_process(func: Any, args: tuple[Any, ...], timeout_s: float) -> Any:
    if not _can_process_call(func):
        raise _SyncCallError(
            f"{getattr(func, '__module__', '')}.{getattr(func, '__qualname__', '')} is not subprocess-callable"
        )
    timeout = max(0.01, float(timeout_s))
    payload = pickle.dumps((func.__module__, func.__qualname__, args))
    fd, out_path = tempfile.mkstemp(prefix="ozon-sync-worker-", suffix=".pkl")
    os.close(fd)
    proc = subprocess.Popen(
        [sys.executable, "-c", _SYNC_WORKER_CODE, out_path],
        cwd=str(Path(__file__).resolve().parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=safe_child_env(),
        **worker_process_kwargs(),
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.close()
        proc.wait(timeout=timeout)
        if proc.returncode != 0:
            raise _SyncCallError(f"child exited {proc.returncode}")
        try:
            out = Path(out_path).read_bytes()
            kind, payload = pickle.loads(out)
        except Exception as exc:
            raise _SyncCallError("child returned malformed payload") from exc
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(proc)
        raise _SyncCallTimeout(f"sync child exceeded {timeout_s}s") from exc
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass
    if kind == "ok":
        return payload
    name, message = payload
    raise _SyncCallError(f"{name}: {message}")


def _can_process_call(func: Any) -> bool:
    return (
        bool(getattr(func, "__module__", ""))
        and bool(getattr(func, "__qualname__", ""))
        and "<locals>" not in getattr(func, "__qualname__", "")
    )


async def _run_sync_bounded(func: Any, *args: Any, timeout_s: float) -> Any:
    if _can_process_call(func):
        return await asyncio.to_thread(_sync_call_in_process, func, args, timeout_s)
    raise _SyncCallError(f"{getattr(func, '__qualname__', repr(func))} is not subprocess-callable")


async def _polite_wait():
    global _last_request_ts
    async with _rate_lock:
        elapsed = time.monotonic() - _last_request_ts
        if elapsed < _min_gap:
            await asyncio.sleep(_min_gap - elapsed)
        _last_request_ts = time.monotonic()


def _sync_curl_get(url: str, proxy: str | None = None) -> tuple[int, str]:
    """Tier-1: curl_cffi sync GET with INCREMENTAL body cap.

    Streams the response and aborts mid-download once MAX_BODY_BYTES is
    exceeded. Defense-in-depth match for httpx-based connectors which all
    stream rather than buffer-then-check.

    Maintenance note: IMPERSONATE='chrome' is auto-latest WITHIN the
    installed curl-cffi version; uv.lock pins that version. Run
    `uv lock --upgrade-package curl-cffi --project ozon` quarterly to
    keep the JA3/JA4 fingerprint pool current with Chrome stable.
    """
    chunks: list[bytes] = []
    total = 0
    encoding = "utf-8"
    # The proxy arrives as an explicit argument rather than an environment
    # variable because this function runs in a subprocess whose environment is
    # deliberately reduced to an allowlist (mcp_core.process.safe_child_env), and
    # that allowlist excludes proxy vars on purpose. Passing it as an argument
    # keeps the child's environment minimal while still honouring OZON_PROXY.
    kwargs: dict[str, Any] = {}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    r = cffi.get(
        url,
        headers=OZON_HEADERS,
        # curl_cffi types this as a Literal of known profiles, but the value is
        # operator-configurable via OZON_IMPERSONATE, so the check is deferred to
        # curl_cffi itself (which raises clearly on an unknown profile).
        impersonate=cast(Any, IMPERSONATE),
        timeout=TIMEOUT,
        stream=True,
        **kwargs,
    )
    try:
        encoding = r.encoding or "utf-8"
        for chunk in r.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise ValueError(f"body exceeds {MAX_BODY_BYTES} bytes (aborted at {total} during stream)")
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            text = body.decode(encoding, errors="replace")
        except (LookupError, TypeError):
            text = body.decode("utf-8", errors="replace")
        return r.status_code, text
    finally:
        try:
            r.close()
        except Exception:
            pass


def _canonical_composer_path(api_path: str) -> str:
    parts = urllib.parse.urlsplit(api_path)
    if parts.scheme or parts.netloc or parts.fragment:
        raise ValueError("composer path must be a relative ozon route")
    normalized_path = posixpath.normpath(parts.path or "/")
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if normalized_path == "/search":
        normalized_path = "/search/"
    if normalized_path.startswith("/product/"):
        if normalized_path.endswith("/reviews") or not normalized_path.endswith("/"):
            normalized_path += "/"
        candidate = urllib.parse.urlunsplit(("", "", normalized_path, parts.query, ""))
        slug_match = PRODUCT_SLUG_RE.match(normalized_path)
        if slug_match and not parts.query:
            return f"/product/{slug_match.group(1)}/"
        if PRODUCT_REVIEWS_PAGED_RE.match(candidate):
            return candidate
        raise ValueError("unsupported product composer path")
    if normalized_path != "/search/":
        raise ValueError("unsupported composer path")
    query_items = urllib.parse.parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False)
    allowed_keys = {"text", "page"}
    if not query_items or any(k not in allowed_keys for k, _ in query_items):
        raise ValueError("unsupported search query keys")
    text_values = [v for k, v in query_items if k == "text"]
    if len(text_values) != 1 or not text_values[0].strip():
        raise ValueError("search composer path requires one non-empty text parameter")
    page_values = [v for k, v in query_items if k == "page"]
    if len(page_values) > 1 or any(not v.isdigit() or int(v) < 1 for v in page_values):
        raise ValueError("search page must be a positive integer")
    canonical_items = [("text", text_values[0])]
    if page_values:
        canonical_items.append(("page", page_values[0]))
    return "/search/?" + urllib.parse.urlencode(canonical_items)


async def _cdp_fetch_json(api_url: str, ctx: Context | None) -> tuple[int, str]:
    """Tier-2: open ozon.ru in the operator's logged-in Chrome, fetch JSON from inside.

    Receives FULL api_url (composer-api.bx URL with ?url= query), not raw path.
    Serialized via _cdp_lock to prevent burst-call tab spam in the operator's Chrome.

    The fetch JS enforces the same MAX_BODY_BYTES cap as Tier-1 to defend
    against an upstream / MITM serving an inflated response that would
    OOM the connector through the CDP -> Python serialization pipeline.
    page.evaluate is wrapped in asyncio.wait_for to bound JS hangs.
    """

    async def _attempt() -> tuple[int, str]:
        async with _cdp_lock, open_page("https://www.ozon.ru/", wait_ms=4000) as page:
            raw = await asyncio.wait_for(
                page.evaluate(
                    """async (args) => {
                    const url = args.url;
                    const cap = args.cap;
                    const res = await fetch(url, {
                        credentials: 'include',
                        headers: {
                            'Accept': 'application/json',
                            'x-o3-app-name': 'dweb_client',
                            'x-o3-app-version': 'release_2-7-1',
                            'x-o3-page-type': 'detail'
                        }
                    });
                    // Stream-bounded read: stop accumulating once we exceed cap.
                    const reader = res.body.getReader();
                    let total = 0;
                    let chunks = [];
                    while (true) {
                        const {done, value} = await reader.read();
                        if (done) break;
                        total += value.length;
                        if (total > cap) {
                            return JSON.stringify({status: 0, text: 'BODY_CAP_EXCEEDED'});
                        }
                        chunks.push(value);
                    }
                    const buf = new Uint8Array(total);
                    let off = 0;
                    for (const c of chunks) { buf.set(c, off); off += c.length; }
                    const text = new TextDecoder().decode(buf);
                    return JSON.stringify({status: res.status, text: text});
                }""",
                    {"url": api_url, "cap": MAX_BODY_BYTES},
                ),
                timeout=30.0,
            )
        result = json.loads(raw) if isinstance(raw, str) else raw
        return result["status"], result["text"]

    try:
        return await asyncio.wait_for(_attempt(), timeout=max(0.01, float(TIMEOUT)))
    except TimeoutError:
        return 0, f"CDP timeout after {TIMEOUT}s"


def _price_str_to_float(s: Any) -> float | None:
    """Parse an Ozon price string like '3\u2009983\u2009₽' (thin-space grouped) to
    float rubles, via the shared tolerant parser (single source of truth).

    Audit 2026-06-01 (CONFIRMED on the LIVE ozon_card path): the old inline
    digit-only regex returned 0.0 for a dead '0 ₽' listing (ranks falsely as
    cheapest) and 19992999.0 for a '1 999 ₽ 2 999 ₽' range. R.coerce_price
    returns None for <=0 / ambiguous multi-number and the real value for a
    single decimal price, so the connector fails loud instead of emitting a
    corrupting number.
    """
    return R.coerce_price(s)


def _parse_widgets(payload: dict) -> dict[str, Any]:
    widgets = payload.get("widgetStates") or {}
    out: dict[str, Any] = {}

    for key, val in widgets.items():
        try:
            data = json.loads(val) if isinstance(val, str) else val
        except (json.JSONDecodeError, TypeError):
            continue
        # A widget value that decodes to null/list/scalar (not a dict) must not
        # crash the tool (audit 2026-06-01 CONFIRMED: webPrice=null raised
        # AttributeError before attach_meta). Skip non-dict widget bodies.
        if not isinstance(data, dict):
            continue

        if "webPrice" in key:
            # Nov 2026: webPrice carries clean display strings + availability.
            #   cardPrice = price with Ozon card (lowest), price = regular,
            #   originalPrice = strikethrough. isAvailable = sellable now.
            if "price" not in out and (data.get("price") or data.get("cardPrice")):
                out["price"] = _price_str_to_float(data.get("price"))
                out["card_price"] = _price_str_to_float(data.get("cardPrice"))
                out["price_original"] = _price_str_to_float(data.get("originalPrice"))
                out["is_available"] = data.get("isAvailable")

        elif "webSale" in key:
            # Fallback price source only if webPrice was absent. Normalize to
            # float for a consistent type contract with the webPrice path.
            cti = data.get("cellTrackingInfo")
            tracking = cti.get("product") if isinstance(cti, dict) else None
            if not isinstance(tracking, dict):
                tracking = {}  # cellTrackingInfo/product drift to list/scalar must not crash
            if tracking and "price" not in out:
                final = tracking.get("finalPrice")
                base = tracking.get("price")
                cur = final if final is not None else base
                if cur is not None:
                    # coerce_price handles str AND numeric (and None/<=0/junk),
                    # so a drifted money-object (finalPrice={...}) no longer
                    # crashes float() (audit wave-2 DRIFT, reproduced).
                    out["price"] = R.coerce_price(cur)
                    out["price_original"] = R.coerce_price(base)
                    offer = data.get("offer")
                    out["is_available"] = offer.get("isAvailable") if isinstance(offer, dict) else None

        elif "webReviewProductScore" in key:
            out["rating_score"] = data.get("totalScore")
            out["rating_count"] = data.get("reviewsCount")

        elif "webStickyProducts" in key or "webProductHeading" in key:
            if "title" not in out and data.get("title"):
                out["title"] = data["title"]
            seller = data.get("seller") or {}
            if isinstance(seller, dict) and seller and "seller" not in out:
                out["seller"] = {"name": seller.get("name"), "link": seller.get("link")}

        elif "webShortCharacteristics" in key:
            chars = data.get("characteristics")
            # a dict-wrapper or non-list drift must not crash the slice
            out["characteristics"] = chars[:30] if isinstance(chars, list) else []

    return out


async def _fetch_composer(api_path: str, ctx: Context | None) -> tuple[int, str, str]:
    """Try Tier-1 (curl_cffi); fall back to Tier-2 (CDP) on 403/non-200.

    Returns (status_code, body, tier_used).

    Successful reads are cached for ``OZON_CACHE_TTL``, keyed by the canonical
    composer path. Caching matters more here than in any other connector: a miss
    can cost a Cloudflare challenge plus a full browser round-trip through CDP,
    so replaying a known-good body is the difference between a fast answer and a
    multi-second one. Only 200s are stored — a cached 403 would keep reporting a
    block after the challenge cleared.
    """
    try:
        safe_path = _canonical_composer_path(api_path)
    except ValueError as exc:
        return 0, str(exc), "invalid_path"
    api_url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url={urllib.parse.quote(safe_path, safe='/')}"

    cached = _cache.get(safe_path)
    if cached is not None:
        status, body = cached
        return status, body, "cache"

    await _polite_wait()

    try:
        status, body = await _run_sync_bounded(_sync_curl_get, api_url, _proxy(), timeout_s=max(0.01, float(TIMEOUT)))
        if status == 200 and body and not body.lstrip().startswith("<"):
            _cache.set(safe_path, (status, body))
            return status, body, "curl_cffi"
        if ctx and status == 403:
            await ctx.debug("Ozon Tier-1 403 (Cloudflare __cf_bm); trying CDP")
    except Exception as exc:
        if ctx:
            await ctx.debug(f"Ozon Tier-1 exception: {exc}; trying CDP")

    try:
        status, body = await _cdp_fetch_json(api_url, ctx)
        if status == 200 and body and not body.lstrip().startswith("<"):
            _cache.set(safe_path, (status, body))
        return status, body, "cdp"
    except NavBlocked as exc:
        if exc.status:
            return exc.status, str(exc), "cdp_blocked"
        return 0, str(exc), "cdp_failed"
    except Exception as exc:
        return 0, str(exc), "cdp_failed"


def _ozon_blocked_error(tier: str, detail: str = "") -> TransportDownError:
    """Build the actionable 403-blocked error for the current tier.

    Cloudflare __cf_bm / Ozon WAF blocks are transport-layer access failures
    (not validation, not parser drift), so they surface as TransportDownError
    with status_code=403 and the operator's solve-the-challenge guidance inline.
    """
    message = (
        f"Ozon returned HTTP 403 via {tier}. "
        "CDP may be reachable, but the browser session is blocked by Ozon/Cloudflare. "
        "Open ozon.ru in the Chrome CDP profile, solve any captcha/challenge, then retry."
    )
    if detail:
        message += f" Detail: {detail[:160]}"
    return TransportDownError(message, status_code=403)


def _canonical_product_path_from_input(raw_input: str) -> tuple[str | None, dict[str, Any] | None]:
    raw = raw_input.strip()
    if raw.isdigit():
        return f"/product/{raw}/", None
    if raw.startswith("http"):
        parts = urllib.parse.urlsplit(raw)
        host = (parts.hostname or "").rstrip(".").lower()
        if parts.scheme != "https" or not (host == "ozon.ru" or host.endswith(".ozon.ru")):
            return None, {"status": "error", "type": "invalid_arguments", "message": "URL must be on ozon.ru"}
        raw = parts.path
    else:
        raw = urllib.parse.urlsplit(raw).path
    if not raw.startswith("/product/"):
        return None, {
            "status": "error",
            "type": "invalid_arguments",
            "message": "Pass SKU, /product/<digits>/ path, or full ozon.ru URL",
        }

    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    if not normalized.endswith("/"):
        normalized += "/"
    match = PRODUCT_SLUG_RE.match(normalized)
    if not match:
        return None, {
            "status": "error",
            "type": "invalid_arguments",
            "message": f"path must match /product/<digits>/ or /product/<slug>-<digits>/ — got {normalized!r} (SSRF protection)",
        }
    return f"/product/{match.group(1)}/", None


def _search_tile_product_link(link: str) -> tuple[str, str] | None:
    link = link.strip()
    if not link:
        return None
    if link.startswith("/"):
        canonical, _error = _canonical_product_path_from_input(link)
        if not canonical:
            return None
        return f"https://www.ozon.ru{link}", canonical
    parts = urllib.parse.urlsplit(link)
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme != "https" or "@" in parts.netloc or not (host == "ozon.ru" or host.endswith(".ozon.ru")):
        return None
    canonical, _error = _canonical_product_path_from_input(parts.path)
    if not canonical:
        return None
    return f"https://www.ozon.ru{canonical}", canonical


@mcp.tool(
    name="ozon_card",
    annotations=ToolAnnotations(
        title="Ozon Product Card",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def ozon_card(
    sku_or_path: Annotated[
        str,
        Field(
            description="SKU integer-as-string, full Ozon URL, or /product/<digits>/ path. "
            "Other paths are rejected (SSRF prevention).",
        ),
    ],
    ctx: Context | None = None,
) -> OzonCardResponse:
    """Fetch Ozon product card data via composer-api.bx.

    Tier-1 (curl_cffi) tried first. Falls back to Tier-2 (Chrome CDP at port
    9222) when Tier-1 hits Cloudflare 403. Tier-2 requires the operator running Chrome
    via scripts/start_chrome_cdp.ps1 (Windows) or scripts/start_chrome_cdp.sh (Linux/macOS) first.

    Args:
        sku_or_path: SKU integer-as-string, full Ozon URL, or /product/<digits>/ path.
                     Other paths are rejected (SSRF prevention).

    ## Return Format

    OzonCardResponse: {status, price, card_price, price_original, is_available,
    rating_score, rating_count, title, seller, characteristics, url, tier_used,
    meta} on success.

    ## Error Format

    Raises ToolError on validation (BadRequestError), transport/block
    (TransportDownError), or parser drift (ParserDriftError). No-results is NOT
    an error — an empty widgetStates payload returns a healthy response with
    null fields.
    """
    log_event("ozon_card.start", sku=str(sku_or_path)[:120])
    try:
        target_path, error = _canonical_product_path_from_input(sku_or_path)
        if error:
            log_event("ozon_card.invalid_input", error=error.get("type"))
            raise_tool_error(BadRequestError(error.get("message", "invalid sku_or_path")))

        if ctx is not None:
            await ctx.info(f"ozon_card: {target_path}")

        # _canonical_product_path_from_input returns (path, None) or (None, error),
        # and the error branch above raises — so the path is set by here.
        assert target_path is not None
        status_code, body, tier = await _fetch_composer(target_path, ctx)

        if status_code == 0:
            raise_tool_error(
                TransportDownError(
                    "Tier-1 (curl_cffi) blocked AND Tier-2 (CDP) unreachable. "
                    f"Start Chrome ({cdp_setup_hint()}) and ensure the CDP port is open. "
                    f"Last error: {body[:200]}"
                )
            )
        if status_code == 403:
            raise_tool_error(_ozon_blocked_error(tier, body))
        if status_code != 200:
            raise_tool_error(
                TransportDownError(
                    f"Ozon returned HTTP {status_code} via {tier}",
                    status_code=status_code,
                )
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise_tool_error(ParserDriftError(f"non-JSON response body via {tier}; preview: {body[:200]}"))
        if not isinstance(payload, dict) or not isinstance(payload.get("widgetStates", {}), dict):
            raise_tool_error(ParserDriftError("expected object payload with widgetStates object"))

        parsed = _parse_widgets(payload)
        parsed["status"] = "success"
        parsed["url"] = f"https://www.ozon.ru{target_path}"
        parsed["tier_used"] = tier
        result = R.attach_meta(parsed, R.validate_offer(parsed, require_title=False), source="ozon_card")
        log_event("ozon_card.done", tier=tier, status="success")
        return OzonCardResponse(**result)
    except ToolError:
        raise
    except Exception as exc:
        log_event("ozon_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"ozon_card failed: {exc}")))


def _ts_to_iso(ts: Any) -> str | None:
    """Convert a unix-seconds timestamp (int or numeric str) to UTC ISO-8601."""
    try:
        return datetime.datetime.fromtimestamp(int(ts), tz=datetime.UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _safe_review_page_path(sku: str, next_button: str) -> str | None:
    """Rebuild a paginated reviews path from VALIDATED components of the upstream
    nextButton. The raw upstream string is never forwarded to fetch() — page,
    page_key and sort are parsed out, each checked against a strict allowlist,
    and the path is reassembled then matched against PRODUCT_REVIEWS_PAGED_RE.
    Returns None if anything is missing or fails validation.
    """
    if not isinstance(next_button, str) or not next_button or not next_button.startswith("?"):
        return None
    qs = urllib.parse.parse_qs(next_button[1:])
    page = (qs.get("page") or [""])[0]
    page_key = (qs.get("page_key") or [""])[0]
    sort = (qs.get("sort") or ["published_at_desc"])[0]
    if not page.isdigit() or int(page) < 1 or int(page) > 1000:
        return None
    if not _REVIEW_PAGE_KEY_RE.match(page_key):
        return None
    if sort not in _ALLOWED_REVIEW_SORTS:
        return None
    path = f"/product/{sku}/reviews/?page={page}&page_key={page_key}&sort={sort}"
    return path if PRODUCT_REVIEWS_PAGED_RE.match(path) else None


def _parse_review_item(r: dict) -> dict[str, Any]:
    """Map one raw Ozon review object to our compact shape.

    Text fields are coerced to str before slicing: a drifted rich-text wrapper
    (comment as {'text': ...}) must not crash the whole tool (audit 2026-06-01
    CONFIRMED: dict comment raised KeyError on [:1500])."""
    content = r.get("content")
    author = r.get("author")
    usefulness = r.get("usefulness")
    # nested objects may drift to non-dict (list/str/null); coerce so a single
    # drifted review can't crash the whole tool (audit wave-2 DRIFT).
    content = content if isinstance(content, dict) else {}
    author = author if isinstance(author, dict) else {}
    usefulness = usefulness if isinstance(usefulness, dict) else {}

    def _text(v: object) -> str:
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            inner = v.get("text")
            if isinstance(inner, str):
                return inner
        return ""

    # firstName/lastName may drift to a non-string (rich-text object/null) — a
    # single such review must not crash the tool (audit wave-2 round-2 DRIFT).
    fn = author.get("firstName")
    ln = author.get("lastName")
    name = (fn if isinstance(fn, str) else "").strip()
    if isinstance(ln, str) and ln:
        name = f"{name} {ln}".strip()
    photos = content.get("photos")
    return {
        "score": content.get("score"),
        "text": _text(content.get("comment"))[:1500],
        "positive": _text(content.get("positive"))[:500],
        "negative": _text(content.get("negative"))[:500],
        "useful": usefulness.get("useful"),
        "photos": len(photos) if isinstance(photos, list) else 0,
        "author": name[:60],
        "date": _ts_to_iso(r.get("publishedAt") or r.get("createdAt")),
    }


@mcp.tool(
    name="ozon_reviews",
    annotations=ToolAnnotations(
        title="Ozon Product Reviews",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def ozon_reviews(
    sku_or_path: Annotated[
        str,
        Field(
            description="SKU integer-as-string, full Ozon URL, or /product/<digits>/ path. "
            "Normalized to /product/<digits>/reviews/ (SSRF-allowlisted).",
        ),
    ],
    limit: Annotated[
        int,
        Field(
            default=20,
            description="Max review texts to return (1..100). Distribution+total always full.",
        ),
    ] = 20,
    sort: Annotated[
        str,
        Field(
            default="recent",
            description='Review ordering. Aliases: "recent"/"default" -> newest, '
            '"best"/"highest" -> highest rated first, '
            '"worst"/"lowest"/"complaints" -> LOWEST rated first. '
            "Raw API values published_at_desc/score_desc/score_asc also accepted.",
        ),
    ] = "recent",
    ctx: Context | None = None,
) -> OzonReviewsResponse:
    """Fetch Ozon product review texts + star distribution via composer-api.bx.

    Tier-1 (curl_cffi) tried first, Tier-2 (Chrome CDP) fallback — same path
    as ozon_card. Returns review texts (comment/positive/negative), per-review
    score, helpfulness votes, author first name, date, plus the overall star
    distribution and total count.

    Pages are walked automatically (30/page) until `limit` texts are collected
    or pages run out, deduplicating by review uuid. Hard cap of 10 pages.

    ## Return Format

    OzonReviewsResponse: {status, sort, rating_score, reviews_count, distribution,
    returned, partial, stop_reason, last_error, requested_limit, reviews, meta}
    on success. A later-page failure with reviews already collected is a
    PARTIAL SUCCESS (partial=True, stop_reason set), NOT an error.

    ## Error Format

    Raises ToolError on validation (BadRequestError), transport/block
    (TransportDownError), or parser drift (ParserDriftError) — but ONLY when no
    reviews have been collected yet. Once at least one page yielded reviews, a
    later-page failure degrades to a partial-success return.
    """
    log_event("ozon_reviews.start", sku=str(sku_or_path)[:120], limit=limit, sort=sort)
    try:
        result = await _ozon_reviews_impl(sku_or_path, limit, sort, ctx)
        log_event("ozon_reviews.done", status=result.status, returned=result.returned)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("ozon_reviews.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"ozon_reviews failed: {exc}")))


async def _ozon_reviews_impl(
    sku_or_path: str,
    limit: int,
    sort: str,
    ctx: Context | None,
) -> OzonReviewsResponse:
    """ozon_reviews implementation — kept separate so the public tool can wrap
    it in a catch-all that converts unexpected exceptions to a typed ToolError.

    First-page failures raise ToolError (BadRequestError / TransportDownError /
    ParserDriftError). Once at least one review is collected, a later-page
    failure degrades to a partial-success return (partial=True, stop_reason set)
    rather than raising — no-results is NOT an error, and partial data is NOT an
    error.
    """
    if limit < 1 or limit > 100:
        raise_tool_error(BadRequestError("limit must be an integer from 1 to 100"))

    sort_norm = (sort or "recent").strip().lower()
    api_sort = _REVIEW_SORT_ALIASES.get(sort_norm, sort_norm)
    if api_sort not in _ALLOWED_REVIEW_SORTS:
        raise_tool_error(
            BadRequestError(
                f"sort must be one of {sorted(_REVIEW_SORT_ALIASES)} or {sorted(_ALLOWED_REVIEW_SORTS)} — got {sort!r}"
            )
        )

    canonical_path, error = _canonical_product_path_from_input(sku_or_path)
    if error:
        raise_tool_error(BadRequestError(error.get("message", "invalid sku_or_path")))
    # Same contract as above: the error branch raises, so the path is set.
    assert canonical_path is not None
    sku = canonical_path.strip("/").split("/")[1]
    base_reviews = f"/product/{sku}/reviews/"
    if not PRODUCT_REVIEWS_PATH_RE.match(base_reviews):
        raise_tool_error(BadRequestError("SSRF guard: bad reviews path"))
    # First-page path carries ?sort= directly (verified: works without page_key).
    target_path = base_reviews if api_sort == "published_at_desc" else f"{base_reviews}?sort={api_sort}"
    if not PRODUCT_REVIEWS_PAGED_RE.match(target_path):
        raise_tool_error(BadRequestError("SSRF guard: bad reviews path"))

    if ctx is not None:
        await ctx.info(f"ozon_reviews: {target_path} (limit={limit}, sort={api_sort})")

    def _widget(widgets: dict, prefix: str) -> dict:
        for k, v in widgets.items():
            if k.startswith(prefix):
                try:
                    parsed = json.loads(v) if isinstance(v, str) else v
                except (json.JSONDecodeError, TypeError):
                    return {}
                # a drifted widget body (null/list/scalar) must yield {} so the
                # caller's .get() chain can't crash (audit wave-2 DRIFT).
                return parsed if isinstance(parsed, dict) else {}
        return {}

    MAX_PAGES = 10  # 10 * 30 ≈ 300 reviews ceiling regardless of limit
    collected: list[dict[str, Any]] = []
    seen_uuids: set[str] = set()
    score_first: dict[str, Any] = {}
    total_from_paging: Any = None
    tier_used: str | None = None
    next_path: str | None = target_path
    pages = 0
    partial = False
    stop_reason: str | None = None
    last_error: dict[str, Any] | None = None

    while next_path and len(collected) < limit and pages < MAX_PAGES:
        status_code, body, tier = await _fetch_composer(next_path, ctx)
        tier_used = tier
        if status_code == 0:
            if not collected:
                raise_tool_error(
                    TransportDownError(
                        f"Tier-1 (curl_cffi) blocked AND Tier-2 (CDP) unreachable. "
                        f"Start Chrome ({cdp_setup_hint()}) and ensure the CDP port is open. "
                        f"Last error: {body[:300]}"
                    )
                )
            partial = True
            stop_reason = "all_tiers_failed"
            last_error = {"type": "all_tiers_failed", "message": body[:300]}
            break
        if status_code == 403:
            if not collected:
                raise_tool_error(_ozon_blocked_error(tier, body))
            partial = True
            stop_reason = "blocked"
            last_error = {"type": "blocked", "code": 403, "tier": tier, "message": body[:200]}
            break
        if status_code != 200:
            if not collected:
                raise_tool_error(
                    TransportDownError(
                        f"Ozon returned HTTP {status_code} via {tier}",
                        status_code=status_code,
                    )
                )
            partial = True
            stop_reason = "http"
            last_error = {"type": "http", "code": status_code, "tier": tier, "preview": body[:200]}
            break
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            if not collected:
                raise_tool_error(ParserDriftError(f"non-JSON response body via {tier}; preview: {body[:200]}"))
            partial = True
            stop_reason = "parse"
            last_error = {"type": "parse", "tier": tier, "preview": body[:200]}
            break
        if not isinstance(payload, dict) or not isinstance(payload.get("widgetStates", {}), dict):
            if not collected:
                raise_tool_error(ParserDriftError("expected object payload with widgetStates object"))
            partial = True
            stop_reason = "parse"
            last_error = {"type": "parse", "tier": tier, "message": "expected object payload with widgetStates object"}
            break

        widgets = payload.get("widgetStates") or {}
        pages += 1
        if not score_first:
            score_first = _widget(widgets, "webReviewProductScore")

        list_w = _widget(widgets, "webListReviews")
        paging = list_w.get("paging")
        if not isinstance(paging, dict):
            paging = {}  # a non-dict paging drift must not crash the nested .get
        if total_from_paging is None:
            total_from_paging = paging.get("total")

        reviews_list = list_w.get("reviews")
        for r in reviews_list if isinstance(reviews_list, list) else []:
            if len(collected) >= limit:
                break
            if not isinstance(r, dict):
                continue
            raw_uuid = r.get("uuid")
            uuid = str(raw_uuid) if isinstance(raw_uuid, (str, int)) and not isinstance(raw_uuid, bool) else ""
            if uuid and uuid in seen_uuids:
                continue
            if uuid:
                seen_uuids.add(uuid)
            collected.append(_parse_review_item(r))

        nb = paging.get("nextButton")
        if nb:
            candidate_next = _safe_review_page_path(sku, nb)
            if not candidate_next:
                partial = True
                stop_reason = "invalid_next_button"
                last_error = {"type": "invalid_next_button"}
                next_path = None
            else:
                next_path = candidate_next
        else:
            next_path = None

    if next_path and len(collected) < limit and pages >= MAX_PAGES:
        partial = True
        stop_reason = "max_pages"
        last_error = {"type": "max_pages", "max_pages": MAX_PAGES}

    distribution: dict[str, Any] = {}
    score_rows = score_first.get("score") or []
    if not isinstance(score_rows, list):
        score_rows = []
    for row in score_rows:
        if isinstance(row, dict) and row.get("title"):
            stars = re.sub(r"[^\d]", "", str(row["title"]))
            if stars:
                distribution[stars] = row.get("value")

    return OzonReviewsResponse(
        **R.attach_meta(
            {
                "status": "success",
                "url": f"https://www.ozon.ru{target_path}",
                "tier_used": tier_used,
                "sort": api_sort,
                "rating_score": score_first.get("totalScore"),
                "reviews_count": score_first.get("reviewsCount") or total_from_paging,
                "distribution": distribution,
                "pages_fetched": pages,
                "returned": len(collected),
                "partial": partial,
                "stop_reason": stop_reason,
                "last_error": last_error,
                "requested_limit": limit,
                "reviews": collected,
            },
            R.validate_review_block(
                {
                    "returned": len(collected),
                    "reviews": collected,
                    "reviews_count": score_first.get("reviewsCount") or total_from_paging,
                    "rating_score": score_first.get("totalScore"),
                }
            ),
            source="ozon_reviews",
        )
    )


def _atom_text(atom: dict, type_key: str) -> str | None:
    """Extract text from a mainState atom of given type."""
    if atom.get("type") != type_key:
        return None
    inner = atom.get(type_key) or {}
    if isinstance(inner, dict) and "text" in inner:
        v = inner["text"]
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and "text" in v:
            nested = v["text"]
            return nested if isinstance(nested, str) else None
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, dict) and "text" in first:
                nested = first["text"]
                return nested if isinstance(nested, str) else None
    return None


def _parse_search_tile(item: Any) -> dict[str, Any]:
    """Parse a single tileGridDesktop item (Ozon search result, Nov 2026 schema).

    Top-level keys: action, brandLogo, id, isAdult, mainState, multiButton,
    sku, tileImage, topRightButtons, trackingInfo.
    Title and price live as atoms inside mainState (priceV2, textAtom, etc.).
    """
    if not isinstance(item, dict):
        return {}
    raw_sku = item.get("sku") or item.get("id")
    out: dict[str, Any] = {}
    if isinstance(raw_sku, (str, int)) and not isinstance(raw_sku, bool):
        out["sku"] = raw_sku

    action = item.get("action") or {}
    link = action.get("link") if isinstance(action, dict) else None
    if isinstance(link, str) and link:
        normalized_link = _search_tile_product_link(link)
        if normalized_link:
            url, canonical = normalized_link
            out["url"] = url
            out["canonical_path"] = canonical
            out["card_input"] = canonical

    main_state = item.get("mainState") or []
    if not isinstance(main_state, list):
        main_state = []
    title = None
    price_text = None
    price_original_text = None
    rating = None
    rating_count = None
    stock_label = None
    for atom in main_state:
        if not isinstance(atom, dict):
            continue
        t = atom.get("type")
        if t == "textAtom":
            inner = atom.get("textAtom") or {}
            if not isinstance(inner, dict):
                continue
            text = inner.get("text")
            if not title and isinstance(text, str):
                title = text
        elif t == "textDS":
            inner = atom.get("textDS") or {}
            if not isinstance(inner, dict):
                continue
            text = inner.get("text")
            test_info = inner.get("testInfo") or {}
            test_id = test_info.get("automatizationId") if isinstance(test_info, dict) else None
            if isinstance(text, str) and not title and (atom.get("id") == "name" or test_id == "tile-name"):
                title = text
            elif isinstance(text, str) and not stock_label and ("осталось" in text or "шт" in text):
                stock_label = text
        elif t == "priceV2":
            pv = atom.get("priceV2") or {}
            if not isinstance(pv, dict):
                continue
            raw_price_entries = pv.get("price") or []
            if not isinstance(raw_price_entries, list):
                continue
            entries = [
                e for e in raw_price_entries if isinstance(e, dict) and isinstance(e.get("text"), str) and e.get("text")
            ]
            # Visual order on the page: entries[0] is the current/sale price (big),
            # entries[1] (if present) is the strikethrough original. textStyle values
            # vary across categories ("PRICE", "BIG_PRICE", "SALE_PRICE", etc.) so we
            # rely on order, not on the style label.
            if entries and not price_text:
                price_text = entries[0].get("text")
            if len(entries) >= 2 and not price_original_text:
                price_original_text = entries[1].get("text")
        elif t == "labelList":
            ll = atom.get("labelList") or {}
            if not isinstance(ll, dict):
                continue
            raw_items = ll.get("items") or []
            if not isinstance(raw_items, list):
                continue
            for it in raw_items:
                if not isinstance(it, dict):
                    continue  # a non-object label item must not crash the tile
                tit = it.get("title") or ""
                if isinstance(tit, str) and ("осталось" in tit or "шт" in tit):
                    stock_label = tit
        elif t == "labelListV2":
            llv2 = atom.get("labelListV2") or {}
            if not isinstance(llv2, dict):
                continue
            # A tile carries TWO labelListV2 atoms: "tile-list-labels"
            # (badges like "Оригинал") and "tile-list-rating" (star rating +
            # review count). Only the rating list holds what we want; skip the
            # other so badge text can't be mistaken for a rating/count.
            test_info = llv2.get("testInfo") or {}
            auto_id = (test_info.get("automatizationId") if isinstance(test_info, dict) else None) or ""
            if auto_id and auto_id != "tile-list-rating":
                continue
            raw_items = llv2.get("items") or []
            if not isinstance(raw_items, list):
                continue
            for sub in raw_items:
                if not isinstance(sub, dict) or sub.get("type") != "text":
                    continue
                txt_obj = sub.get("text") or {}
                txt = txt_obj.get("text") if isinstance(txt_obj, dict) else None
                if not isinstance(txt, str) or not txt:
                    continue
                # Rating, e.g. "4,8" or "4.8"
                if not rating and re.match(r"^\d[.,]\d", txt):
                    rating = txt
                # Review count: Nov-2026 format "24 086 отзывов" (no parens);
                # older format was "(15 374)". Extract the integer either way.
                elif not rating_count and ("отзыв" in txt or ("(" in txt and ")" in txt)):
                    digits = re.sub(r"[^\d]", "", txt)
                    rating_count = int(digits) if digits else txt

    out["title"] = title
    out["price"] = price_text
    out["price_original"] = price_original_text
    out["rating"] = rating
    out["rating_count"] = rating_count
    out["stock"] = stock_label
    return out


def _aggregate_offer_warnings(items: list[dict]) -> list[str]:
    """Roll per-item validation into connector-level warnings. Reports the COUNT
    of items hitting each invariant so a systemic drift (every tile missing a
    price) is loud, while a single odd listing stays quiet-ish.
    """
    if not items:
        return ["no_items: search returned zero parseable product tiles (grid widget drift?)"]
    from collections import Counter

    tally: Counter = Counter()
    for it in items:
        for w in R.validate_offer(it, require_title=True):
            tally[w.split(":")[0]] += 1
    n = len(items)
    out: list[str] = []
    # half the items = systemic drift, but a 1-item result must still warn
    # (parity with wb fix, audit 2026-06-01: max(2,...) suppressed n==1 drift).
    threshold = max(1, n // 2)
    for code, hits in tally.items():
        if hits >= threshold:
            out.append(f"{code}: {hits}/{n} items affected (likely schema drift)")
    return out


@mcp.tool(
    name="ozon_search",
    annotations=ToolAnnotations(
        title="Ozon Catalog Search",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def ozon_search(
    query: Annotated[
        str,
        Field(
            description="Russian search text.",
        ),
    ],
    page: Annotated[
        int,
        Field(
            default=1,
            description="Result page (1..10).",
        ),
    ] = 1,
    ctx: Context | None = None,
) -> OzonSearchResponse:
    """Search Ozon catalog. Tier-1 curl_cffi → Tier-2 CDP fallback.

    Returns sku/title/price/rating per item. Schema parses Nov 2026
    `tileGridDesktop-*` widgets with `mainState` atom structure.

    ## Return Format

    OzonSearchResponse: {status, query, page, tier_used, count, items, meta} on
    success. Zero matching items is NOT an error — it returns a healthy response
    with count=0 and empty items.

    ## Error Format

    Raises ToolError on validation (BadRequestError), transport/block
    (TransportDownError), or parser drift (ParserDriftError).
    """
    log_event("ozon_search.start", query=str(query)[:100], page=page)
    try:
        result = await _ozon_search_impl(query, page, ctx)
        log_event("ozon_search.done", status=result.status, count=result.count)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("ozon_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"ozon_search failed: {exc}")))


async def _ozon_search_impl(
    query: str,
    page: int,
    ctx: Context | None,
) -> OzonSearchResponse:
    """ozon_search implementation — kept separate so the public tool can wrap
    it in a catch-all that converts unexpected exceptions to a typed ToolError.
    """
    if page < 1 or page > 10:
        raise_tool_error(BadRequestError("page must be an integer from 1 to 10"))

    search_params = httpx.QueryParams({"text": query, "page": str(page)})
    search_path = f"/search/?{search_params}"
    if ctx is not None:
        await ctx.info(f"ozon_search: {query} page={page}")

    status_code, body, tier = await _fetch_composer(search_path, ctx)

    if status_code == 0:
        raise_tool_error(
            TransportDownError(
                f"Tier-1 (curl_cffi) blocked AND Tier-2 (CDP) unreachable. "
                f"Start Chrome ({cdp_setup_hint()}) and ensure the CDP port is open. "
                f"Last error: {body[:300]}"
            )
        )
    if status_code == 403:
        raise_tool_error(_ozon_blocked_error(tier, body))
    if status_code != 200:
        raise_tool_error(
            TransportDownError(
                f"Ozon returned HTTP {status_code} via {tier}",
                status_code=status_code,
            )
        )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise_tool_error(ParserDriftError(f"non-JSON response body via {tier}; preview: {body[:200]}"))
    if not isinstance(payload, dict) or not isinstance(payload.get("widgetStates", {}), dict):
        raise_tool_error(ParserDriftError("expected object payload with widgetStates object"))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    widgets = payload.get("widgetStates") or {}

    # Nov 2026 schema: tileGridDesktop-* contains items array of product tiles.
    for key, val in widgets.items():
        if not key.startswith("tileGridDesktop-"):
            continue
        try:
            data = json.loads(val) if isinstance(val, str) else val
        except (json.JSONDecodeError, TypeError):
            continue
        # widget body or its items may drift to a non-object — must not crash
        # the slice/loop before per-item guards (audit wave-2 round-2 DRIFT).
        if not isinstance(data, dict):
            continue
        tile_items = data.get("items")
        if not isinstance(tile_items, list):
            continue
        for item in tile_items[:50]:
            if not isinstance(item, dict):
                continue  # a null/non-object tile must not crash the search
            parsed = _parse_search_tile(item)
            sku = parsed.get("sku")
            if not isinstance(sku, (str, int)) or isinstance(sku, bool):
                continue
            sku_key = str(sku)
            if not sku_key or sku_key in seen:
                continue
            if not parsed.get("title"):
                continue  # skip non-product tiles (banners, ads)
            seen.add(sku_key)
            items.append(parsed)

    return OzonSearchResponse(
        **R.attach_meta(
            {
                "status": "success",
                "query": query,
                "page": page,
                "tier_used": tier,
                "count": len(items),
                "items": items[:30],
            },
            _aggregate_offer_warnings(items[:30]),
            source="ozon_search",
        )
    )


@mcp.tool(
    name="ozon_selfcheck",
    annotations=ToolAnnotations(
        title="Ozon Self-check",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def ozon_selfcheck(ctx: Context | None = None) -> OzonSelfcheckResponse:
    """Structural drift canary for Ozon (tri-state: success / drift_detected /
    inconclusive). Fetches live search/card/reviews + a non-default reviews sort
    and compares the widget-prefix SHAPE against the critical set, plus a parse
    smoke. Detects "a widget we depend on vanished" BEFORE it silently breaks a
    parser.

    Tri-state (audit 2026-06-01): a Cloudflare 403 / CDP-down / non-200 / non-JSON
    body is `inconclusive` (transport — Ozon's tier-1 curl_cffi is often blocked),
    NEVER drift. Only a reached-200 JSON body missing a critical widget or failing
    the parse smoke is `drift`. The reviews_sort subcheck exercises the
    sort-param path (score_asc) that ozon_reviews pagination depends on.

    ## Return Format

    OzonSelfcheckResponse: {status, healthy, connector, checks, server_version,
    server_started_at, process_id} — tri-state per subcheck
    (healthy/drift/inconclusive). Inconclusive and drift_detected are NOT errors;
    they are valid canary verdicts returned as a normal response.

    ## Error Format

    Raises ToolError (TransportDownError) ONLY on an unexpected internal bug that
    prevents the canary from producing any verdict. Transport/block/parse
    failures of individual sub-checks map to inconclusive entries, not errors.
    """
    log_event("ozon_selfcheck.start")
    try:
        result = await _ozon_selfcheck_impl(ctx)
        log_event("ozon_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("ozon_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"ozon_selfcheck failed: {exc}")))


async def _ozon_selfcheck_impl(ctx: Context | None) -> OzonSelfcheckResponse:
    """ozon_selfcheck implementation — kept separate so the public tool can wrap
    it in a catch-all that converts unexpected exceptions to a typed ToolError.
    """
    checks: dict[str, dict] = {}

    async def _check(name: str, path: str, expected_prefixes: set[str], smoke: Callable[[dict], dict], baseline: str):
        # Whole subcheck under a hard wall-clock bound + catch-all: _fetch_composer
        # (curl_cffi thread + CDP fallback) has no total-time deadline and could
        # wedge, and a transport/decode exception must map to inconclusive, never
        # crash or hang the canary (audit 2026-06-01 CRASH_HANG).
        try:
            async with asyncio.timeout(60):
                status, body, tier = await _fetch_composer(path, ctx)
        except TimeoutError:
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason="timeout", notes=[f"{name} fetch exceeded 60s wall-clock"]
            )
            return
        except Exception as exc:
            checks[name] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="transport_down",
                notes=[f"fetch raised {type(exc).__name__}: {str(exc)[:120]}"],
            )
            return
        if status != 200:
            # Ozon tier-1 curl_cffi is frequently Cloudflare-blocked and tier-2 CDP
            # may be down — transport, not selector drift.
            reason = "rate_limited" if status == 429 else ("blocked" if status in (403, 401, 407) else "transport_down")
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason=reason, notes=[f"http {status} via {tier}"], code=status
            )
            return
        try:
            payload = json.loads(body) if isinstance(body, str) else json.loads(bytes(body).decode("utf-8", "replace"))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError):
            checks[name] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="transport_down",
                notes=["payload not JSON (interstitial / challenge body)"],
            )
            return
        if not isinstance(payload, dict):
            checks[name] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="transport_down",
                notes=[f"payload JSON not an object (got {type(payload).__name__})"],
            )
            return
        live_prefixes = R.widget_prefixes(payload.get("widgetStates") or {})
        d = R.diff_keys(expected_prefixes, live_prefixes)
        try:
            sm = smoke(payload)
        except Exception as exc:
            checks[name] = R.selfcheck_entry(
                "drift",
                baseline=baseline,
                missing_widgets=d["missing"],
                notes=[f"smoke raised {type(exc).__name__}: {str(exc)[:120]}"],
            )
            return
        if d["missing"]:
            checks[name] = R.selfcheck_entry(
                "drift",
                baseline=baseline,
                missing_widgets=d["missing"],
                added_widgets=d["added"][:10],
                smoke=sm,
                notes=[f"critical widget(s) vanished: {d['missing']}"],
            )
        elif not sm.get("ok"):
            checks[name] = R.selfcheck_entry(
                "drift",
                baseline=baseline,
                missing_widgets=[],
                smoke=sm,
                notes=["widgets present but parse smoke yielded nothing (inner shape drift)"],
            )
        else:
            checks[name] = R.selfcheck_entry("healthy", baseline=baseline, added_widgets=d["added"][:10], smoke=sm)

    def _smoke_card(payload: dict) -> dict:
        p = _parse_widgets(payload)
        w = R.validate_offer(p, require_title=False)
        return {
            "ok": bool(p.get("price") and p.get("rating_score")) and not w,
            "warnings": w,
            "price": p.get("price"),
            "rating": p.get("rating_score"),
        }

    def _smoke_search(payload: dict) -> dict:
        items = []
        for key, val in (payload.get("widgetStates") or {}).items():
            if not key.startswith("tileGridDesktop-"):
                continue
            data = json.loads(val) if isinstance(val, str) else val
            for it in (data.get("items") or [])[:50]:
                parsed = _parse_search_tile(it)
                if parsed.get("title"):
                    items.append(parsed)
        return {"ok": len(items) > 0, "parsed_items": len(items)}

    def _smoke_reviews(payload: dict) -> dict:
        widgets = payload.get("widgetStates") or {}
        wlr: dict[str, Any] = next(
            (json.loads(v) if isinstance(v, str) else v for k, v in widgets.items() if k.startswith("webListReviews")),
            {},
        )
        revs = wlr.get("reviews") or []
        texts = sum(1 for r in revs if (r.get("content") or {}).get("comment"))
        return {"ok": texts > 0, "reviews_in_page": len(revs), "with_text": texts}

    await _check(
        "search", "/search/?text=сетевой фильтр&page=1", {"tileGridDesktop"}, _smoke_search, baseline="сетевой фильтр"
    )
    await _check(
        "card",
        f"/product/{_SELFCHECK_SKU}/",
        {"webPrice", "webReviewProductScore"},
        _smoke_card,
        baseline=_SELFCHECK_SKU,
    )
    await _check(
        "reviews",
        f"/product/{_SELFCHECK_SKU}/reviews/",
        {"webListReviews", "webReviewProductScore"},
        _smoke_reviews,
        baseline=_SELFCHECK_SKU,
    )
    # reviews_sort: the non-default sort-param path ozon_reviews pagination uses.
    # A drift in how Ozon honours ?sort= (or the widget under it) would silently
    # break sorted/paged review fetches — covered separately from first-page.
    await _check(
        "reviews_sort",
        f"/product/{_SELFCHECK_SKU}/reviews/?sort=score_asc",
        {"webListReviews", "webReviewProductScore"},
        _smoke_reviews,
        baseline=f"{_SELFCHECK_SKU}?sort=score_asc",
    )

    try:
        settings = get_settings()
        config_loaded = bool(settings)
    except Exception:
        config_loaded = False
    try:
        tool_count = len(await mcp.list_tools())
    except Exception:
        tool_count = 0

    result = R.selfcheck_result(
        "ozon",
        checks,
        required=("search", "card", "reviews", "reviews_sort"),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=os.getpid(),
    )
    result["config_loaded"] = config_loaded
    result["tool_count"] = tool_count
    return OzonSelfcheckResponse(**result)


if __name__ == "__main__":
    mcp.run(transport="stdio")
