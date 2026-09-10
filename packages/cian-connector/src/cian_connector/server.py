"""Cian MCP connector: real-estate search and offer cards.

Cian (cian.ru) is a classifieds site for flats, houses and commercial property,
not a marketplace — but the same doctrine applies (docs/ADDING_A_SOURCE.md,
docs/ANTI_BOT.md § Cian). Probed 2026-09-09:

* Plain HTTP is a dead end: Cian's WAF answers every path, the JSON API
  included, with a 403 ``cian_waf_block`` page keyed on IP reputation. There is
  no captcha to solve and no fingerprint to clear.
* Inside the operator's Chrome (tier 2) the site's own JSON API answers an
  in-page ``fetch`` POST: structured offers, no HTML parsing. The offer card
  embeds the whole offer in ``window._cianConfig['frontend-offer-card']``.

So this connector is tier 2 only, and the in-page JavaScript is transport, not
parsing: it POSTs a ``jsonQuery`` or serialises a subtree of ``_cianConfig``,
and every field decision happens in Python where a fixture can test it.

Search is by filters (deal, property type, region, rooms, price, area), not by
text — Cian has no free-text search worth exposing. Region ids are Cian's own;
the four the connector knows are in ``KNOWN_REGIONS``.

Rent comes in two markets that Cian keeps apart and that must not be averaged
together: long-term (``for_day: "!1"``, priced per month) and daily
(``for_day: "1"``, priced per night, its own dailyFlatRent categories). The
``deal`` argument selects one; ``price_unit`` on every row says which reading a
price carries, because Cian leaves ``paymentPeriod`` null on daily offers.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.error_handling import RetryMiddleware
from mcp.types import ToolAnnotations
from mcp_core import resilience as R
from mcp_core.cache import TTLCache
from mcp_core.errors import (
    BadRequestError,
    NotFoundError,
    ParserDriftError,
    TransportDownError,
    raise_tool_error,
)
from mcp_core.logging import log_event
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.pacing import Pacer
from mcp_core.redact import redact_error_text as _redact
from mcp_core.transport.chrome_cdp import NavBlocked, open_page
from pydantic import Field

from cian_connector.models_output import (
    CianAgentOut,
    CianCardResponse,
    CianMetroOut,
    CianPriceChangeOut,
    CianSearchItemOut,
    CianSearchResponse,
    CianSelfcheckResponse,
    MetaOut,
)
from cian_connector.settings import get_settings

_settings = get_settings()

SERVER_VERSION = "2.1.0"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

SITE_BASE = "https://www.cian.ru"
SEARCH_API = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"
CARD_CONFIG_KEY = "frontend-offer-card"
TIMEOUT = _settings.timeout
MAX_BODY_BYTES = _settings.max_body_bytes
_min_gap = _settings.min_gap

# Region ids verified live on 2026-09-09 by the addresses that came back.
KNOWN_REGIONS: dict[str, str] = {
    "1": "Москва",
    "2": "Санкт-Петербург",
    "4593": "Московская область",
    "4588": "Ленинградская область",
}

DealType = Literal["sale", "rent", "daily"]
OfferType = Literal["flat", "room", "house", "commercial"]

# jsonQuery._type per (deal, property type). Rooms are flats with room=[0]
# (verified: that query returns category roomSale). Houses and commercial
# property have their own families (houseSale, officeSale … verified).
#
# Daily rent is the same ``*rent`` family with ``for_day: "1"`` instead of
# "!1" — Cian answers with its own dailyFlatRent / dailyRoomRent /
# dailyHouseRent categories. Commercial has no daily market at all (the query
# is accepted and returns zero), so the pair is absent and rejected by name
# rather than shipped as a tool call that always finds nothing.
_QUERY_TYPE: dict[tuple[str, str], str] = {
    ("sale", "flat"): "flatsale",
    ("rent", "flat"): "flatrent",
    ("daily", "flat"): "flatrent",
    ("sale", "room"): "flatsale",
    ("rent", "room"): "flatrent",
    ("daily", "room"): "flatrent",
    ("sale", "house"): "suburbansale",
    ("rent", "house"): "suburbanrent",
    ("daily", "house"): "suburbanrent",
    ("sale", "commercial"): "commercialsale",
    ("rent", "commercial"): "commercialrent",
}
# 1..6 rooms; Cian's own codes for a free layout (7) and a studio (9) —
# verified live 2026-09-09: room=[9] returns flatType studio, room=[7] openPlan.
_ROOM_CHOICES = frozenset({1, 2, 3, 4, 5, 6, 7, 9})
_REGION_RE = re.compile(r"^\d{1,7}$")
_OFFER_ID_RE = re.compile(r"cian\.ru/(?:sale|rent)/[a-z-]+/(\d{6,})(?:[/?#]|$)")
_WAF_MARKER = "cian_waf_block"
# The card transport's own envelope: only a state that actually carried the
# offer is worth caching.
_CARD_OK_RE = re.compile(r'"kind":\s*"ok"')
_FIRST_NUMBER_RE = re.compile(r"\d[\d\s  ]*")
_FLAT_TYPE_RU = {"studio": "студия", "openPlan": "свободная планировка"}

mcp = FastMCP(
    name="cian-connector",
    version=SERVER_VERSION,
    instructions=(
        "Cian real-estate listings (Russia): search flats, rooms, houses and "
        "commercial property for sale, long-term rent or daily rent, and read "
        "one offer's card. Read-only, no credentials; every read runs inside the "
        "operator's Chrome over CDP because Cian's WAF blocks plain HTTP. Start "
        "with cian_search (filters, not free text — region ids: 1 Москва, "
        "2 Санкт-Петербург, 4593 Московская область, 4588 Ленинградская область); "
        "cian_card takes an offer id or URL. Prices are rubles and price_unit "
        "says what one buys (total / month / day); a missing price is None, never 0."
    ),
)
mcp.add_middleware(RetryMiddleware())

_cache: TTLCache[tuple[int, str]] = TTLCache(ttl_s=_settings.cache_ttl, max_entries=256)
_pacer = Pacer(_min_gap)
_cdp_lock = asyncio.Lock()


async def _polite_wait() -> None:
    """Space this source's requests out, and back off if it refused us."""
    await _pacer.wait(min_gap=_min_gap)


# ------------------------------------------------------------ transport ----

# Transport only: POST the query the way the site itself does and hand the
# body back. The body cap mirrors the other CDP connectors — an inflated body
# would otherwise OOM the connector through the CDP serialisation pipeline.
_JS_POST = """async (args) => {
    const res = await fetch(args.url, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*'},
        body: JSON.stringify(args.body)
    });
    const reader = res.body.getReader();
    let total = 0;
    const chunks = [];
    while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        total += value.length;
        if (total > args.cap) {
            return JSON.stringify({status: 0, text: 'BODY_CAP_EXCEEDED'});
        }
        chunks.push(value);
    }
    const buf = new Uint8Array(total);
    let off = 0;
    for (const c of chunks) { buf.set(c, off); off += c.length; }
    return JSON.stringify({status: res.status, text: new TextDecoder().decode(buf)});
}"""

# Transport only: serialise the offer subtree of the card micro-frontend's
# state. Which keys mean what is decided in Python (_parse_card).
_JS_CARD_CONFIG = """(args) => {
    const title = document.title || '';
    const url = location.href;
    if (/не найден|удален|удалён|снят/i.test(title)) {
        return JSON.stringify({kind: 'not_found', title, url});
    }
    const cfg = window._cianConfig;
    if (!cfg) return JSON.stringify({kind: 'no_config', title, url});
    const entry = cfg[args.key];
    let state = null;
    if (Array.isArray(entry)) {
        const hit = entry.find((x) => x && x.key === 'defaultState');
        state = hit ? hit.value : null;
    }
    const od = state && state.offerData ? state.offerData : null;
    if (!od || !od.offer) {
        return JSON.stringify({kind: 'no_offer', title, url, keys: Object.keys(cfg)});
    }
    const keep = {};
    for (const k of ['offer', 'agent', 'company', 'priceChanges', 'stats']) {
        if (k in od) keep[k] = od[k];
    }
    const text = JSON.stringify({kind: 'ok', title, url, offerData: keep});
    if (text.length > args.cap) return JSON.stringify({kind: 'too_big', title, url});
    return text;
}"""


async def _cdp_post_json(json_query: dict[str, Any], ctx: Context | None) -> tuple[int, str]:
    """POST ``jsonQuery`` to the search API from inside a cian.ru tab.

    Serialised via _cdp_lock so a burst of tool calls cannot spray tabs. The
    tab lands on the site root so the request carries the session's cookies
    and origin, exactly like the site's own search.
    """

    async def _attempt() -> tuple[int, str]:
        async with _cdp_lock, open_page(f"{SITE_BASE}/", wait_ms=2500) as page:
            raw = await asyncio.wait_for(
                page.evaluate(
                    _JS_POST,
                    {"url": SEARCH_API, "body": {"jsonQuery": json_query}, "cap": MAX_BODY_BYTES},
                ),
                timeout=30.0,
            )
        result = json.loads(raw) if isinstance(raw, str) else raw
        return int(result["status"]), str(result["text"])

    try:
        return await asyncio.wait_for(_attempt(), timeout=max(0.01, float(TIMEOUT)))
    except TimeoutError:
        return 0, f"CDP timeout after {TIMEOUT}s"


async def _cdp_page_config(url: str, ctx: Context | None) -> tuple[int, str]:
    """Open an offer page and serialise its ``_cianConfig`` card state."""

    async def _attempt() -> tuple[int, str]:
        async with _cdp_lock, open_page(url, wait_ms=2500) as page:
            raw = await asyncio.wait_for(
                page.evaluate(_JS_CARD_CONFIG, {"key": CARD_CONFIG_KEY, "cap": MAX_BODY_BYTES}),
                timeout=30.0,
            )
        return 200, raw if isinstance(raw, str) else json.dumps(raw)

    try:
        return await asyncio.wait_for(_attempt(), timeout=max(0.01, float(TIMEOUT)))
    except TimeoutError:
        return 0, f"CDP timeout after {TIMEOUT}s"


def _looks_like_json(body: str) -> bool:
    head = body.lstrip()[:1]
    return head in ("{", "[")


async def _fetch_search(json_query: dict[str, Any], ctx: Context | None) -> tuple[int, str, str]:
    """Run one search query over CDP. Returns (status, body, tier).

    Only JSON-shaped 200s are cached — a cached block page would keep
    reporting a block after the operator passed the check in the browser.
    """
    key = "search:" + json.dumps(json_query, sort_keys=True, ensure_ascii=False)
    cached = _cache.get(key)
    if cached is not None:
        status, body = cached
        return status, body, "cache"

    await _polite_wait()
    try:
        status, body = await _cdp_post_json(json_query, ctx)
    except NavBlocked as exc:
        _pacer.record_refusal()
        return exc.status or 0, str(exc), "cdp_blocked"
    except Exception as exc:
        return 0, _redact(str(exc)), "cdp_failed"
    if status == 200 and _looks_like_json(body):
        _pacer.record_success()
        _cache.set(key, (status, body))
    return status, body, "cdp"


async def _fetch_card(offer_id: int, ctx: Context | None) -> tuple[int, str, str]:
    """Read one offer's card state over CDP. Returns (status, body, tier).

    The page is requested under ``/sale/flat/``: Cian redirects a rent or
    non-flat id to its real deal/type URL (verified live), so one URL shape
    serves every category and the connector never has to guess the type.
    """
    key = f"card:{offer_id}"
    cached = _cache.get(key)
    if cached is not None:
        status, body = cached
        return status, body, "cache"

    await _polite_wait()
    try:
        status, body = await _cdp_page_config(f"{SITE_BASE}/sale/flat/{offer_id}/", ctx)
    except NavBlocked as exc:
        _pacer.record_refusal()
        return exc.status or 0, str(exc), "cdp_blocked"
    except Exception as exc:
        return 0, _redact(str(exc)), "cdp_failed"
    if status == 200 and _looks_like_json(body) and _CARD_OK_RE.search(body[:80]):
        _pacer.record_success()
        _cache.set(key, (status, body))
    return status, body, "cdp"


def _blocked_error(tier: str, detail: str = "") -> TransportDownError:
    message = (
        f"Cian answered with its WAF block page via {tier}. "
        "Open cian.ru in the Chrome CDP profile, pass the check if one is shown, then retry; "
        "from a datacenter IP the block is expected until the browser session is warmed up."
    )
    hint = _pacer.rotation_hint()
    if hint:
        message += f" {hint}"
    if detail:
        message += f" Detail: {detail[:160]}"
    return TransportDownError(message, status_code=403)


def _raise_for_fetch_failure(status: int, body: str, tier: str, what: str) -> None:
    """Map a failed fetch to the shared error taxonomy."""
    if status in (401, 403, 429) or _WAF_MARKER in body[:2000]:
        _pacer.record_refusal()
        raise_tool_error(_blocked_error(tier, body))
    if status == 404:
        raise_tool_error(NotFoundError(f"Cian {what} not found (404)."))
    raise_tool_error(TransportDownError(f"Cian {what} fetch failed: HTTP {status} via {tier}. {body[:120]}"))


# -------------------------------------------------------------- parsing ----


def _s(value: Any) -> str | None:
    """A non-empty string, else None — never a stringified None or number."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _b(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_float(value: Any) -> float | None:
    """Areas and heights come as strings ('38.1'); a non-positive value is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            number = float(value.replace(",", ".").replace(" ", "").replace(" ", ""))
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def _price_of(offer: dict[str, Any]) -> float | None:
    """The price is ``bargainTerms.priceRur``; a new-building card carries only
    ``bargainTerms.price`` and ``priceTotalRur`` (verified 2026-09-09), so all
    three are read, in that order. None when none of them holds a real price."""
    terms = _d(offer.get("bargainTerms"))
    price = R.coerce_price(R.first_present(terms, "priceRur", "price"))
    if price is None:
        price = R.coerce_price(R.first_present(offer, "priceTotalRur", "priceTotal"))
    return price


def _clean_url(value: Any) -> str | None:
    url = _s(value)
    if url is None:
        return None
    return url.split("?", 1)[0].split("#", 1)[0]


def _address(geo: dict[str, Any]) -> str | None:
    user_input = _s(geo.get("userInput"))
    if user_input:
        return user_input
    parts: list[str] = []
    for node in geo.get("address") or []:
        name = _s(_d(node).get("fullName")) or _s(_d(node).get("name"))
        if name:
            parts.append(name)
    return ", ".join(parts) or None


def _metro_rows(geo: dict[str, Any]) -> list[dict[str, Any]]:
    """Stations as Cian ranks them: the one it marks ``isDefault`` (the station
    shown on the tile) first, then by travel time. Minutes alone would put a
    7-minute bus ride ahead of a 10-minute walk, which is not what the site
    shows and not what a reader means by "nearest"."""
    rows: list[tuple[bool, dict[str, Any]]] = []
    for node in geo.get("undergrounds") or []:
        station = _d(node)
        if not station:
            continue
        rows.append(
            (
                station.get("isDefault") is True,
                {
                    "name": _s(station.get("name")),
                    "minutes": R.coerce_int(station.get("time")),
                    "mode": _s(station.get("transportType")),
                },
            )
        )
    rows.sort(key=lambda item: (not item[0], item[1]["minutes"] is None, item[1]["minutes"] or 0))
    return [row for _, row in rows]


def _rooms(offer: dict[str, Any], flat_type: str | None) -> int | None:
    if flat_type in _FLAT_TYPE_RU:
        return None
    rooms = R.coerce_int(offer.get("roomsCount"))
    return rooms if rooms else None


def _compose_title(
    offer: dict[str, Any],
    rooms: int | None,
    flat_type: str | None,
    area: float | None,
    floor: int | None,
    floors: int | None,
) -> str | None:
    own = _s(offer.get("title"))
    if own:
        return own
    short = _s(offer.get("formattedShortInfo"))
    if short:
        return re.sub(r"\s+", " ", short)
    bits: list[str] = []
    if rooms:
        bits.append(f"{rooms}-комн. квартира")
    elif flat_type in _FLAT_TYPE_RU:
        bits.append(_FLAT_TYPE_RU[flat_type])
    if area:
        bits.append(f"{area:g} м²")
    if floor and floors:
        bits.append(f"{floor}/{floors} эт.")
    elif floor:
        bits.append(f"{floor} эт.")
    return ", ".join(bits) or None


def _price_unit(category: str | None, deal_type: str | None, period: str | None) -> str | None:
    """What one ``price_rub`` actually buys: the whole property, a month, a night.

    Cian encodes this only in the category (``dailyFlatRent`` and friends) and
    leaves ``paymentPeriod`` null on daily offers, so a caller comparing a
    nightly 5 000 ₽ against a monthly 90 000 ₽ has nothing to go on unless the
    connector says it outright.
    """
    if category and "daily" in category.lower():
        return "day"
    if deal_type == "sale":
        return "total"
    if deal_type == "rent":
        # Long-term rent is quoted monthly; anything else Cian names, we pass
        # through rather than flatten to a month we did not verify.
        return "month" if period in (None, "monthly") else period
    return None


def _offer_row(offer: dict[str, Any]) -> dict[str, Any]:
    """The fields shared by a search hit and a card, read from one offer object."""
    terms = _d(offer.get("bargainTerms"))
    building = _d(offer.get("building"))
    geo = _d(offer.get("geo"))
    user = _d(offer.get("user"))
    newbuilding = _d(offer.get("newbuilding"))
    photos = offer.get("photos")
    flat_type = _s(offer.get("flatType"))
    rooms = _rooms(offer, flat_type)
    total_area = _as_float(offer.get("totalArea"))
    floor = R.coerce_int(offer.get("floorNumber"))
    floors = R.coerce_int(building.get("floorsCount"))
    metro = _metro_rows(geo)
    return {
        "offer_id": R.coerce_int(offer.get("id")),
        "title": _compose_title(offer, rooms, flat_type, total_area, floor, floors),
        "deal_type": _s(offer.get("dealType")),
        "category": _s(offer.get("category")),
        "price_rub": _price_of(offer),
        "price_unit": _price_unit(_s(offer.get("category")), _s(offer.get("dealType")), _s(terms.get("paymentPeriod"))),
        "price_period": _s(terms.get("paymentPeriod")),
        "lease_term": _s(terms.get("leaseTermType")),
        "deposit_rub": R.coerce_price(terms.get("deposit")),
        "rooms": rooms,
        "flat_type": flat_type,
        "is_apartments": _b(offer.get("isApartments")),
        "total_area_m2": total_area,
        "living_area_m2": _as_float(offer.get("livingArea")),
        "kitchen_area_m2": _as_float(offer.get("kitchenArea")),
        "floor": floor,
        "floors_total": floors,
        "build_year": R.coerce_int(building.get("buildYear")),
        "address": _address(geo),
        "metro": metro,
        "newbuilding_name": _s(newbuilding.get("name")),
        "url": _clean_url(offer.get("fullUrl")),
        "agency_name": _s(R.first_present(user, "agencyName", "companyName")),
        "seller_type": _s(user.get("userType")),
        "is_by_homeowner": _b(offer.get("isByHomeowner")),
        "created_at": _s(offer.get("creationDate")),
        "photos": len(photos) if isinstance(photos, list) else 0,
    }


def _parse_offers(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Offers and the total from a search payload. ValueError on the wrong shape."""
    if not isinstance(payload, dict):
        raise ValueError("search payload is not a JSON object")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("search payload has no data object")
    offers = data.get("offersSerialized")
    if not isinstance(offers, list):
        raise ValueError("search payload has no offersSerialized list")
    rows = [_offer_row(offer) for offer in offers if isinstance(offer, dict)]
    total = R.coerce_int(R.first_present(data, "aggregatedCount", "offerCount"))
    if total is None and R.first_present(data, "aggregatedCount", "offerCount") == 0:
        total = 0
    return rows, total


def _views(stats: Any) -> int | None:
    """``stats.totalViewsFormattedString`` is '12907 просмотров, 98 за сегодня'."""
    text = _s(_d(stats).get("totalViewsFormattedString"))
    if not text:
        return None
    match = _FIRST_NUMBER_RE.search(text)
    return R.coerce_int(match.group(0)) if match else None


def _price_history(changes: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for change in changes if isinstance(changes, list) else []:
        node = _d(change)
        rows.append(
            {
                "changed_at": _s(node.get("changeTime")),
                "price_rub": R.coerce_price(_d(node.get("priceData")).get("price")),
            }
        )
    return rows


def _agent(offer_data: dict[str, Any], offer: dict[str, Any]) -> dict[str, Any] | None:
    agent = _d(offer_data.get("agent"))
    if not agent:
        user = _d(offer.get("user"))
        if not user:
            return None
        return {
            "name": _s(R.first_present(user, "agencyName", "companyName")),
            "agent_id": R.coerce_int(R.first_present(user, "cianUserId", "userId")),
            "account_type": _s(user.get("accountType")),
            "user_type": _s(user.get("userType")),
            "is_developer": _b(user.get("isBuilder")),
            "offers_count": None,
            "on_cian_since": None,
        }
    return {
        "name": _s(R.first_present(agent, "name", "companyName")),
        "agent_id": R.coerce_int(R.first_present(agent, "cianUserId", "id")),
        "account_type": _s(agent.get("accountType")),
        "user_type": _s(agent.get("userType")),
        "is_developer": _b(agent.get("isDeveloper")),
        "offers_count": R.coerce_int(agent.get("offersCount")),
        "on_cian_since": _s(agent.get("creationDate")),
    }


def _parse_card(offer_data: Any) -> dict[str, Any]:
    """A card from the ``offerData`` subtree. ValueError on the wrong shape."""
    if not isinstance(offer_data, dict):
        raise ValueError("card state is not an object")
    offer = offer_data.get("offer")
    if not isinstance(offer, dict) or not offer:
        raise ValueError("card state has no offer object")
    row = _offer_row(offer)
    if row["offer_id"] is None:
        raise ValueError("card offer has no id")
    building = _d(offer.get("building"))
    agent = _agent(offer_data, offer)
    row.update(
        {
            "building_material": _s(R.first_present(building, "materialType", "houseMaterialType")),
            "ceiling_height_m": _as_float(building.get("ceilingHeight")),
            "description": _s(offer.get("description")),
            "updated_at": _s(offer.get("editDate")),
            "views": _views(offer_data.get("stats")),
            "price_history": _price_history(offer_data.get("priceChanges")),
            "agent": agent,
        }
    )
    # A card's offer object carries no ``user`` (the one at offerData level is
    # the viewer, not the publisher); the publisher lives in ``agent``.
    if agent:
        row["agency_name"] = row["agency_name"] or agent["name"]
        row["seller_type"] = row["seller_type"] or agent["user_type"]
    return row


def _extract_offer_id(raw: str) -> int | None:
    """Bare digits or a cian.ru offer URL; anything else is None.

    Offer ids run 9 digits today; the 6-digit floor rejects a short fragment
    ('123') instead of fetching an id that never existed.
    """
    raw = raw.strip()
    if raw.isdigit():
        return int(raw) if len(raw) >= 6 else None
    match = _OFFER_ID_RE.search(raw)
    return int(match.group(1)) if match else None


def _range(low: float | int | None, high: float | int | None) -> dict[str, float | int]:
    bounds: dict[str, float | int] = {}
    if low is not None:
        bounds["gte"] = low
    if high is not None:
        bounds["lte"] = high
    return bounds


def _build_json_query(
    *,
    deal: str,
    offer_type: str,
    region: str,
    rooms: list[int] | None,
    price_min: int | None,
    price_max: int | None,
    area_min: float | None,
    area_max: float | None,
    page: int,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "_type": _QUERY_TYPE[(deal, offer_type)],
        "engine_version": {"type": "term", "value": 2},
        "region": {"type": "terms", "value": [int(region)]},
        "page": {"type": "term", "value": page},
    }
    if offer_type == "room":
        query["room"] = {"type": "terms", "value": [0]}
    elif offer_type == "flat" and rooms:
        query["room"] = {"type": "terms", "value": sorted(set(rooms))}
    if deal in ("rent", "daily"):
        # "!1" = not daily (the site's default for "Снять"); "1" = daily only.
        # Omitting the key mixes both markets in one page, which is never what
        # a caller means.
        query["for_day"] = {"type": "term", "value": "1" if deal == "daily" else "!1"}
    if price_min is not None or price_max is not None:
        query["price"] = {"type": "range", "value": _range(price_min, price_max)}
    if area_min is not None or area_max is not None:
        query["total_area"] = {"type": "range", "value": _range(area_min, area_max)}
    return query


def _validate_region(region: str | None) -> str:
    if region is not None and not region.strip():
        raise_tool_error(BadRequestError("region must not be empty"))
    value = (region or _settings.region).strip()
    if not _REGION_RE.match(value):
        known = ", ".join(f"{k} = {v}" for k, v in KNOWN_REGIONS.items())
        raise_tool_error(BadRequestError(f"region must be a Cian region id in digits ({known}), got {value!r}"))
    return value


# ---------------------------------------------------------------- tools ----


@mcp.tool(
    name="cian_search",
    annotations=ToolAnnotations(
        title="Cian Real-Estate Search",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def cian_search(
    deal: Annotated[
        DealType,
        Field(
            description=(
                "'sale' (купить), 'rent' (снять на длительный срок) or 'daily' (снять посуточно). "
                "'rent' and 'daily' are separate markets on Cian and never mix in one result page; "
                "a 'daily' price is per night, a 'rent' price per month — read price_unit on each item."
            )
        ),
    ],
    offer_type: Annotated[
        OfferType,
        Field(
            description=(
                "Property type: 'flat' (квартира), 'room' (комната), 'house' (дом/участок), 'commercial'. "
                "'commercial' has no daily market on Cian and is rejected with deal='daily'."
            )
        ),
    ] = "flat",
    region: Annotated[
        str | None,
        Field(
            description=(
                "Cian region id, digits only: 1 = Москва, 2 = Санкт-Петербург, 4593 = Московская область, "
                "4588 = Ленинградская область. Other regions need their Cian id. Default CIAN_REGION (1)."
            )
        ),
    ] = None,
    rooms: Annotated[
        list[int] | None,
        Field(
            description=(
                "Flats only: room counts to include, any of 1..6; 7 = свободная планировка, 9 = студия. "
                "Omit for any number of rooms. Ignored for room/house/commercial."
            )
        ),
    ] = None,
    price_min: Annotated[
        int | None,
        Field(ge=1, description="Minimum price in rubles: total for sale, per month for rent, per night for daily."),
    ] = None,
    price_max: Annotated[
        int | None,
        Field(ge=1, description="Maximum price in rubles: total for sale, per month for rent, per night for daily."),
    ] = None,
    area_min: Annotated[float | None, Field(gt=0, description="Minimum total area in m².")] = None,
    area_max: Annotated[float | None, Field(gt=0, description="Maximum total area in m².")] = None,
    page: Annotated[int, Field(ge=1, le=100, description="Result page (1-based), 28 offers per page.")] = 1,
    ctx: Context | None = None,
) -> CianSearchResponse:
    """Search Cian offers by filters (there is no free-text search).

    Long-term rent (``deal='rent'``) and daily rent (``deal='daily'``) are two
    separate markets: one page never mixes them, and their prices are not
    comparable — a daily offer is priced per night. ``price_unit`` on every
    item says which it is.

    ## Return Format

    CianSearchResponse: {status, deal, offer_type, region, page, tier_used,
    count, total_count, items[], _meta}. Each item carries offer_id, title,
    price_rub (None when Cian shows no price — never 0), price_unit
    ('total' / 'month' / 'day'), rooms, areas, floor, address, nearest metro,
    url, agency and publication data.

    ## Error Format

    ToolError: BadRequestError on malformed arguments (region not digits, room
    code outside 1-6/7/9, min above max, deal='daily' with
    offer_type='commercial'); TransportDownError when Cian's WAF blocks the
    browser session or CDP is down (with the fix inline); ParserDriftError when
    a 200 body is not the expected envelope.
    """
    log_event("cian_search.start", deal=deal, offer_type=offer_type, page=page)
    try:
        if (deal, offer_type) not in _QUERY_TYPE:
            raise_tool_error(
                BadRequestError(
                    f"Cian has no {deal} market for {offer_type}: the query is accepted upstream and returns "
                    "nothing. Daily rent covers flat, room and house only."
                )
            )
        region_id = _validate_region(region)
        if rooms is not None:
            bad = sorted({r for r in rooms if r not in _ROOM_CHOICES})
            if bad:
                raise_tool_error(
                    BadRequestError(f"rooms must be any of 1-6, 7 (свободная планировка) or 9 (студия); got {bad}")
                )
        if price_min is not None and price_max is not None and price_min > price_max:
            raise_tool_error(BadRequestError("price_min must not exceed price_max"))
        if area_min is not None and area_max is not None and area_min > area_max:
            raise_tool_error(BadRequestError("area_min must not exceed area_max"))

        query = _build_json_query(
            deal=deal,
            offer_type=offer_type,
            region=region_id,
            rooms=rooms,
            price_min=price_min,
            price_max=price_max,
            area_min=area_min,
            area_max=area_max,
            page=page,
        )
        status, body, tier = await _fetch_search(query, ctx)
        if status != 200:
            _raise_for_fetch_failure(status, body, tier, "search")
        if _WAF_MARKER in body[:2000]:
            _raise_for_fetch_failure(403, body, tier, "search")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise_tool_error(ParserDriftError(f"non-JSON search body via {tier}; preview: {body[:200]}"))
        try:
            rows, total = _parse_offers(payload)
        except ValueError as exc:
            raise_tool_error(ParserDriftError(f"search envelope changed: {exc}"))

        warnings: list[str] = []
        if not rows and total:
            warnings.append("empty_items_with_nonzero_total")
        items = [
            CianSearchItemOut(**{**row, "metro": CianMetroOut(**row["metro"][0]) if row["metro"] else None})
            for row in rows
        ]
        result = CianSearchResponse(
            deal=deal,
            offer_type=offer_type,
            region=region_id,
            page=page,
            tier_used=tier,
            count=len(items),
            total_count=total,
            items=items,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="cian_search")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("cian_search.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"cian_search failed: {exc}")))


@mcp.tool(
    name="cian_card",
    annotations=ToolAnnotations(
        title="Cian Offer Card",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def cian_card(
    offer_id_or_url: Annotated[
        str, Field(min_length=1, max_length=300, description="Cian offer id (digits) or a full cian.ru offer URL.")
    ],
    ctx: Context | None = None,
) -> CianCardResponse:
    """Read one Cian offer: price and its history, layout, building, address,
    metro, description, publisher.

    ## Return Format

    CianCardResponse: {status, offer_id, title, deal_type, category, price_rub,
    price_unit, price_period, lease_term, deposit_rub, rooms, areas, floor, floors_total,
    build_year, building_material, ceiling_height_m, address, metro[],
    description, photos, created_at, updated_at, views, price_history[], agent,
    url, tier_used, _meta}. price_rub is None when not stated — never 0.

    ## Error Format

    ToolError: BadRequestError when no offer id can be extracted; NotFoundError
    when Cian says the offer is gone; TransportDownError on a WAF block or CDP
    failure; ParserDriftError when the page no longer embeds the offer state.
    """
    log_event("cian_card.start", input=offer_id_or_url[:80])
    try:
        offer_id = _extract_offer_id(offer_id_or_url)
        if offer_id is None:
            raise_tool_error(
                BadRequestError(
                    f"could not extract an offer id from {offer_id_or_url!r}; "
                    "pass bare digits or a full cian.ru offer URL"
                )
            )
        status, body, tier = await _fetch_card(offer_id, ctx)
        if status != 200:
            _raise_for_fetch_failure(status, body, tier, "card")
        try:
            envelope = json.loads(body)
        except json.JSONDecodeError:
            raise_tool_error(ParserDriftError(f"non-JSON card state via {tier}; preview: {body[:200]}"))
        if not isinstance(envelope, dict):
            raise_tool_error(ParserDriftError("card state is not a JSON object"))
        kind = envelope.get("kind")
        title = _s(envelope.get("title")) or ""
        if kind == "not_found":
            raise_tool_error(NotFoundError(f"Cian reports offer {offer_id} as removed: {title[:80]}"))
        if kind == "too_big":
            raise_tool_error(TransportDownError(f"Cian card state for {offer_id} exceeds the body cap"))
        if kind != "ok":
            if "Ошибка" in title or _WAF_MARKER in body[:2000]:
                _raise_for_fetch_failure(403, body, tier, "card")
            raise_tool_error(
                ParserDriftError(
                    f"offer page no longer embeds the card state ({kind}); "
                    f"page title: {title[:80]!r}; config keys: {envelope.get('keys')}"
                )
            )
        try:
            row = _parse_card(envelope.get("offerData"))
        except ValueError as exc:
            raise_tool_error(ParserDriftError(f"card state changed: {exc}"))

        warnings: list[str] = []
        if row["price_rub"] is None:
            warnings.append("price_missing")
        agent = row.pop("agent")
        metro = row.pop("metro")
        history = row.pop("price_history")
        # The card's offer object carries no fullUrl; the page URL Chrome
        # landed on (after Cian's own redirect) is the canonical one.
        row["url"] = row["url"] or _clean_url(envelope.get("url")) or f"{SITE_BASE}/sale/flat/{offer_id}/"
        result = CianCardResponse(
            **row,
            metro=[CianMetroOut(**station) for station in metro],
            price_history=[CianPriceChangeOut(**change) for change in history],
            agent=CianAgentOut(**agent) if agent else None,
            tier_used=tier,
        )
        attached = R.attach_meta(result.model_dump(by_alias=True, exclude={"meta"}), warnings, source="cian_card")
        result.meta = MetaOut(**attached["_meta"])
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("cian_card.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"cian_card failed: {exc}")))


# ------------------------------------------------------------ selfcheck ----


async def cian_selfcheck(ctx: Context | None = None) -> CianSelfcheckResponse:
    """Structural drift canary for Cian (tri-state: success / drift_detected /
    inconclusive). Not an MCP tool — ``marketplace-mcp doctor`` runs it.

    A WAF block or CDP-down is ``inconclusive`` (transport), NEVER drift: from
    a cold session that is the expected state. Only a reached-200 body that
    fails the parse smoke is ``drift``. The search probe supplies the live id
    for the card probe, so the canary never depends on a hardcoded offer.

    ## Return Format

    CianSelfcheckResponse: {status, healthy, connector, checks, server_version,
    server_started_at, process_id}.

    ## Error Format

    Raises ToolError (TransportDownError) ONLY on an unexpected internal bug
    that prevents the canary from producing any verdict.
    """
    log_event("cian_selfcheck.start")
    try:
        result = await _cian_selfcheck_impl(ctx)
        log_event("cian_selfcheck.done", status=result.status)
        return result
    except ToolError:
        raise
    except Exception as exc:
        log_event("cian_selfcheck.error", error=_redact(str(exc)), exc_type=type(exc).__name__)
        raise_tool_error(TransportDownError(_redact(f"cian_selfcheck failed: {exc}")))


async def _cian_selfcheck_impl(ctx: Context | None) -> CianSelfcheckResponse:
    checks: dict[str, dict[str, Any]] = {}

    async def _probe(name: str, fetch: Any, baseline: str) -> Any:
        """Run one fetch; record inconclusive/drift entries; return the parsed
        JSON on a reached-200, else None."""
        try:
            async with asyncio.timeout(60):
                status, body, tier = await fetch()
        except TimeoutError:
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason="timeout", notes=["fetch exceeded 60s"]
            )
            return None
        except Exception as exc:
            checks[name] = R.selfcheck_entry(
                "inconclusive",
                baseline=baseline,
                reason="transport_down",
                notes=[f"raised {type(exc).__name__}: {str(exc)[:120]}"],
            )
            return None
        if status != 200 or _WAF_MARKER in body[:2000]:
            reason = "rate_limited" if status == 429 else ("blocked" if status in (401, 403, 407) else "transport_down")
            if _WAF_MARKER in body[:2000]:
                reason = "blocked"
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason=reason, notes=[f"http {status} via {tier}"], code=status
            )
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            checks[name] = R.selfcheck_entry(
                "inconclusive", baseline=baseline, reason="transport_down", notes=["body not JSON (block page?)"]
            )
            return None

    def _record(name: str, baseline: str, smoke: Any, payload: Any) -> Any:
        try:
            verdict = smoke(payload)
        except Exception as exc:
            checks[name] = R.selfcheck_entry(
                "drift", baseline=baseline, reason="parse_smoke_failed", notes=[str(exc)[:160]]
            )
            return None
        checks[name] = R.selfcheck_entry("healthy", baseline=baseline, notes=[verdict[0]])
        return verdict[1]

    def _search_smoke(payload: Any) -> tuple[str, int | None]:
        rows, total = _parse_offers(payload)
        if not rows:
            raise ValueError("no offers parsed from a generic query")
        priced = [row for row in rows if row["price_rub"] is not None]
        if not priced:
            raise ValueError("no offer carries a price — bargainTerms family lost")
        if all(row["offer_id"] is None for row in rows):
            raise ValueError("offers carry no id")
        if all(row["address"] is None for row in rows):
            raise ValueError("offers carry no address — geo family lost")
        return f"{len(rows)} offers parsed, total {total}", priced[0]["offer_id"]

    def _card_smoke(envelope: Any) -> tuple[str, None]:
        if not isinstance(envelope, dict):
            raise ValueError("card state is not an object")
        kind = envelope.get("kind")
        if kind != "ok":
            raise ValueError(f"offer page no longer embeds the card state ({kind}); keys={envelope.get('keys')}")
        row = _parse_card(envelope.get("offerData"))
        if row["price_rub"] is None:
            raise ValueError("card parsed without a price — bargainTerms family lost")
        return "card state intact", None

    query = _build_json_query(
        deal="sale",
        offer_type="flat",
        region=_validate_region(None),
        rooms=None,
        price_min=None,
        price_max=None,
        area_min=None,
        area_max=None,
        page=1,
    )
    search_payload = await _probe("search", lambda: _fetch_search(query, ctx), "search-offers-desktop-v2")
    offer_id: int | None = None
    if search_payload is not None:
        offer_id = _record("search", "search-offers-desktop-v2", _search_smoke, search_payload)

    if offer_id is not None:
        card_payload = await _probe("card", lambda: _fetch_card(offer_id, ctx), "offer-card-config-v1")
        if card_payload is not None:
            _record("card", "offer-card-config-v1", _card_smoke, card_payload)
    elif "card" not in checks:
        checks["card"] = R.selfcheck_entry(
            "inconclusive",
            baseline="offer-card-config-v1",
            reason="dependency_unavailable",
            notes=["no live offer id from the search probe"],
        )

    result_dict = R.selfcheck_result(
        "cian",
        checks,
        required=("search", "card"),
        server_version=SERVER_VERSION,
        server_started_at=SERVER_STARTED_AT,
        process_id=None,
    )
    return CianSelfcheckResponse(**result_dict)


# Advertised output schemas are the dominant constant cost of an MCP mount:
# replace the full Pydantic tree with top-level field names.
apply_compact_output_schemas(mcp)
