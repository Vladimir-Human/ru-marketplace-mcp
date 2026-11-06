"""Avito item-page state: availability (active, reserved or unknown), deal terms, seller checks, location.

The rendered item page that ``avito_card`` already reads carries the page's own
state as ``window.__staticRouterHydrationData = JSON.parse("...")``; its
``loaderData`` holds ``buyerItem`` with the item and its safe-deal block. The
status is read from that same document — no extra request to Avito — and only
from service data: hydration flags and the safe-deal widget Avito renders
itself. Nothing the seller writes (title, description, seller name, the
contacts block that shows that name) is read for the status: a title such as
«Не продано, торг» or «Зарезервировано? нет, свободно» is data, not a verdict.

Verified live 2026-11-05/06 on reserved and active private listings: the
schema.org offer said ``availability: InStock`` and ``item.isActive`` was true
for both, while the reserved page showed «Товар зарезервирован» in the
safe-deal widget and ``item.deliveryInfo.isReserved`` /
``safeDealDesktop.reserved`` were true. So neither JSON-LD availability nor
``isActive`` decides: ``reserved`` needs an explicit reservation signal,
``active`` needs ``isActive: true`` with an explicit ``isReserved: false``,
signals that contradict each other are ``unknown`` with both sides in the
evidence, and silence is ``unknown`` rather than a guess.

Closed and sold listings are out of scope here: no captured page shows how
Avito marks them, so they are not classified (a missing ad stays not_found).

Search hits carry ``isReserved`` on the js/items entry itself (live 2026-11-05:
15 of 50 hits on one page were reserved).
"""
# START_MODULE_CONTRACT
# PURPOSE: Classify availability from explicit service signals; read deal/seller/location fields the page states.
# SCOPE: Pure parsing of the already-fetched item HTML and js/items entries; no I/O; seller-written text never sets a status or flag.
# DEPENDS: stdlib json/re/HTMLParser.
# LINKS: M-AVITO; R-NO-FABRICATION; packages/avito-connector/tests/test_item_state.py.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# load_hydration: Decode window.__staticRouterHydrationData from the page, bounded in size.
# buyer_item: Return the page's buyerItem only when its item id matches the requested one.
# marker_texts: Visible text of the service data-marker elements (scripts ignored).
# classify: availability + evidence from hydration flags and the safe-deal banner; contradictions or silence -> unknown.
# card_availability: classify() over one item page.
# search_availability: active/reserved/unknown from a js/items entry's own isReserved.
# card_details: Deal terms, delivery/payment, bargain words, badges, seller checks/tenure, location, date, views.
# is_company: The page state's own seller type; None when the page is silent.
# END_MODULE_MAP

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Final, Literal

Availability = Literal["active", "reserved", "unknown"]
ACTIVE: Final = "active"
RESERVED: Final = "reserved"
UNKNOWN: Final = "unknown"
INACTIVE = frozenset({RESERVED})

_HYDRATION_RE = re.compile(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:[^"\\]|\\.)*)"\)', re.S)
_MAX_STATE_CHARS = 4 * 1024 * 1024

# The safe-deal widget's own label on a reserved listing (live 2026-11-05/06).
_RESERVED_BANNER = re.compile(r"Товар\s+зарезервирован", re.I)
# Service elements only. Avito renders the safe-deal widget itself; the title
# (item-view/title-info), description, seller block and the contacts block
# (item-view/item-view-contacts, which shows the seller's chosen name) carry
# seller-written text and are never read for the status.
_SERVICE_MARKERS = ("safedeal-item-card",)


class _MarkerText(HTMLParser):
    """Collect the visible text inside elements carrying selected data-marker values."""

    _VOID = frozenset(
        {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    )

    def __init__(self, markers: tuple[str, ...]) -> None:
        super().__init__()
        self.markers = set(markers)
        self.stack: list[tuple[str, str | None]] = []
        self.found: dict[str, list[str]] = {}
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._VOID:
            return
        marker = dict(attrs).get("data-marker")
        self.stack.append((tag, marker if marker in self.markers else None))
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for name, _marker in self.stack[index:]:
                    if name in ("script", "style"):
                        self.hidden = max(0, self.hidden - 1)
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        for _tag, marker in self.stack:
            if marker:
                self.found.setdefault(marker, []).append(data)

    def text(self, marker: str) -> str:
        return " ".join(" ".join(self.found.get(marker, [])).split())


def marker_texts(html: str, markers: tuple[str, ...] = _SERVICE_MARKERS) -> dict[str, str]:
    parser = _MarkerText(markers)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return {}
    return {marker: parser.text(marker) for marker in markers if parser.text(marker)}


def load_hydration(html: str) -> dict[str, Any] | None:
    match = _HYDRATION_RE.search(html)
    if not match or len(match.group(1)) > _MAX_STATE_CHARS:
        return None
    try:
        data = json.loads(json.loads('"' + match.group(1) + '"'))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def buyer_item(state: dict[str, Any] | None, item_id: int | None) -> dict[str, Any] | None:
    """The page's own buyerItem, only when its item id matches the requested one."""
    loader = state.get("loaderData") if isinstance(state, dict) else None
    if not isinstance(loader, dict):
        return None
    for value in loader.values():
        if isinstance(value, dict) and isinstance(value.get("buyerItem"), dict):
            candidate = value["buyerItem"]
            item = candidate.get("item")
            if isinstance(item, dict) and (item_id is None or item.get("id") == item_id):
                return candidate
    return None


def _bool_at(container: Any, key: str) -> bool | None:
    """The value at ``key`` when it is a real boolean; anything else is no signal."""
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, bool) else None


def classify(bi: dict[str, Any] | None, texts: dict[str, str]) -> tuple[Availability, list[str]]:
    """Availability from explicit service signals only; silence is ``unknown``.

    Reservation signals: ``item.deliveryInfo.isReserved`` / ``safeDealDesktop.reserved``
    true, or «Товар зарезервирован» in the safe-deal widget. Free signals: the same
    flags false. Both kinds present is a contradiction: ``unknown`` with every
    signal in the evidence. ``active`` needs ``item.isActive: true`` and an
    explicit ``deliveryInfo.isReserved: false``. ``buyerItem.reservationInfo`` is
    not read: its shape on a reserved listing has not been captured.
    """
    item = bi.get("item") if isinstance(bi, dict) else None
    reserved: list[str] = []
    free: list[str] = []
    delivery_reserved: bool | None = None
    if isinstance(bi, dict) and isinstance(item, dict):
        delivery_reserved = _bool_at(item.get("deliveryInfo"), "isReserved")
        if delivery_reserved is True:
            reserved.append("item.deliveryInfo.isReserved=true")
        elif delivery_reserved is False:
            free.append("item.deliveryInfo.isReserved=false")
        safe_deal_reserved = _bool_at(bi.get("safeDealDesktop"), "reserved")
        if safe_deal_reserved is True:
            reserved.append("safeDealDesktop.reserved=true")
        elif safe_deal_reserved is False:
            free.append("safeDealDesktop.reserved=false")
    for marker in _SERVICE_MARKERS:
        hit = _RESERVED_BANNER.search(texts.get(marker, ""))
        if hit:
            reserved.append(f"{marker}: «{hit.group(0)}»")
    if reserved and free:
        return UNKNOWN, ["contradicting status signals", *reserved, *free]
    if reserved:
        return RESERVED, reserved
    is_active = _bool_at(item, "isActive")
    if is_active is True and delivery_reserved is False:
        return ACTIVE, ["item.isActive=true", *free]
    if is_active is False:
        return UNKNOWN, ["item.isActive=false (the page does not say why)", *free]
    return UNKNOWN, ["no explicit status signal on the page", *free]


def card_availability(html: str, item_id: int | None) -> tuple[Availability, list[str]]:
    return classify(buyer_item(load_hydration(html), item_id), marker_texts(html))


def search_availability(item: dict[str, Any]) -> Availability:
    """A search hit is a published listing; only its own isReserved flag tells reserved from active."""
    value = item.get("isReserved")
    if value is True:
        return RESERVED
    if value is False:
        return ACTIVE
    return UNKNOWN


# --------------------------------------------------------------------------
# Deal terms, seller checks and location from the same page state.
# --------------------------------------------------------------------------

# safeDealDesktop.services[].type -> a stable public name.
_SERVICE_NAMES = {
    "delivery": "pvz",
    "deliveryCourier": "courier",
    "deliveryPostamat": "postamat",
    "deliveryCNC": "click_and_collect",
    "deliveryT2D": "to_door",
    "deliveryDBS": "seller_delivery",
    "deliveryCDBS": "seller_delivery_cdbs",
    "deliveryCourierD2D": "courier_door_to_door",
    "deliveryDBSOwnCourier": "seller_own_courier",
}
_VERIFICATION_NAMES = {
    "isInnVerified": "ИНН подтверждён",
    "isVerifiedSeller": "Проверенный продавец",
    "hasRealtyVerificationBadge": "Проверка недвижимости",
    "officialDealerBadge": "Официальный дилер",
    "verifiedPartnerBadge": "Проверенный партнёр",
    "carOwnerBadge": "Собственник авто",
}
# Seller icon badges that state a check; the rest («Эковклад», charity) are promotions.
_CHECK_BADGES = re.compile(r"подтвержд|надёжн|надежн|проверен|документ|реквизит", re.I)
# «торг» as a word (not «торговый», «торгуюсь»). Negations are matched first and
# cut out of the text, so «Торг: нет» or «торг не предлагать» never counts as «торг».
_TORG = r"(?<![а-яё])торг(?![а-яё])"
_NO_BARGAIN = re.compile(
    r"без\s+торга"
    r"|торга\s+нет"
    r"|не\s+торгуюсь|не\s+торгуемся|торговаться\s+не\b"
    rf"|{_TORG}\s*[:\-–—]?\s*(?:нет|не\b|неуместен|невозмож|исключ)",
    re.I,
)
_BARGAIN = re.compile(_TORG, re.I)
# The explicit «Торг» parameter some categories carry: its value decides.
_PARAM_YES = re.compile(r"^(?:да|есть|возможен|уместен)$", re.I)
_PARAM_NO = re.compile(r"^(?:нет|невозможен|неуместен)$", re.I)
_MAX_SERVICES = 10
_MAX_CHECKS = 10


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _text(value: Any, limit: int = 200) -> str | None:
    return " ".join(value.split())[:limit] if isinstance(value, str) and value.strip() else None


def _dict(container: Any, key: str) -> dict[str, Any]:
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text_bargain(text: str) -> bool | None:
    """True/False from the words alone; both a negation and a plain «торг» -> None."""
    negated = bool(_NO_BARGAIN.search(text))
    plain = bool(_BARGAIN.search(_NO_BARGAIN.sub(" ", text)))
    if negated and plain:
        return None
    if negated:
        return False
    return True if plain else None


def bargain(description: str | None, params: list[dict[str, str]]) -> bool | None:
    """True/False only from explicit statements; the «Предложить цену» form is not «торг».

    The explicit «Торг» parameter decides first (да/нет); otherwise the listing
    text, where negations («Торг: нет», «торг не …», «без торга») are read
    before a plain «торг». A text that contradicts the parameter, or itself, is
    None: the seller's words are data, not a flag to trust over the page.
    """
    param: bool | None = None
    for entry in params:
        if entry["name"].strip().lower() != "торг":
            continue
        value = entry["value"].strip()
        if _PARAM_YES.match(value):
            param = True
        elif _PARAM_NO.match(value):
            param = False
        else:
            return None
    words = _text_bargain(description or "")
    if param is not None and words is not None and words != param:
        return None
    return param if param is not None else words


def is_company(bi: dict[str, Any] | None) -> bool | None:
    """The page state's own seller type; None when the page does not say."""
    # Only buyerItem.seller.isCompany: the top-level buyerItem.isCompany is not
    # used as a fallback (review #198); the DOM label is checked by the caller.
    if not isinstance(bi, dict):
        return None
    value = _dict(bi, "seller").get("isCompany")
    return value if isinstance(value, bool) else None


def _params(bi: dict[str, Any]) -> list[dict[str, str]]:
    params: list[dict[str, str]] = []
    for entry in (_dict(bi, "paramsBlock").get("items") or [])[:40]:
        if not isinstance(entry, dict):
            continue
        name, value = _text(entry.get("title"), 80), _text(entry.get("description"))
        if name and value:
            params.append({"name": name, "value": value})
    return params


def _delivery_services(safe_deal: dict[str, Any]) -> list[str]:
    services: list[str] = []
    raw = safe_deal.get("services")
    for service in (raw if isinstance(raw, list) else [])[: _MAX_SERVICES * 3]:
        if (
            isinstance(service, dict)
            and service.get("available") is True
            and service.get("enabled") is True
            and isinstance(service.get("type"), str)
        ):
            kind: str = service["type"]
            name = _SERVICE_NAMES.get(kind, kind)[:40]
            if name not in services:
                services.append(name)
                if len(services) == _MAX_SERVICES:
                    break
    return services


def _seller_checks(item: dict[str, Any]) -> list[str]:
    checks: list[str] = []
    verification = _dict(_dict(item, "seller"), "verification")
    for key, label in _VERIFICATION_NAMES.items():
        # Only a real true: a string such as "false" or a pending-status object
        # is not a confirmed check (live captures carry booleans or null).
        if verification.get(key) is True:
            checks.append(label)
    badge_bar = _dict(item, "sellerBadgeBar")
    icons, badges = badge_bar.get("iconsBadges"), badge_bar.get("badges")
    candidates = (icons if isinstance(icons, list) else [])[: _MAX_CHECKS * 2] + (
        badges if isinstance(badges, list) else []
    )[: _MAX_CHECKS * 2]
    for badge in candidates:
        if len(checks) >= _MAX_CHECKS:
            break
        title = _text(badge.get("title"), 80) if isinstance(badge, dict) else None
        if title and _CHECK_BADGES.search(title) and title not in checks:
            checks.append(title)
    return checks[:_MAX_CHECKS]


def card_details(html: str, item_id: int | None, description: str | None) -> dict[str, Any]:
    """Deal terms, seller checks and location fields; missing data stays None/empty.

    ``address`` and ``coords`` are returned as the page gives them; the caller
    exposes them only for a company seller (server._apply_details).
    """
    bi = buyer_item(load_hydration(html), item_id)
    out: dict[str, Any] = {
        "condition": None,
        "params": [],
        "delivery_available": None,
        "delivery_services": [],
        "payment_on_delivery": None,
        "safe_deal": None,
        "price_offer_enabled": None,
        "bargain": bargain(description, []),
        "item_badges": [],
        "location": None,
        "metro": [],
        "address": None,
        "coords": None,
        "posted_at": None,
        "views": None,
        "today_views": None,
        "is_company": is_company(bi),
        "seller": {},
    }
    if not isinstance(bi, dict):
        return out
    item = _dict(bi, "item")
    params = _params(bi)
    out["params"] = params
    out["condition"] = next((p["value"] for p in params if p["name"].lower() == "состояние"), None)
    out["bargain"] = bargain(description, params)
    delivery = _dict(item, "deliveryInfo")
    out["delivery_available"] = _bool(delivery.get("isDelivery"))
    out["payment_on_delivery"] = _bool(delivery.get("isCod"))
    safe_deal = bi.get("safeDealDesktop")
    if isinstance(safe_deal, dict):
        out["delivery_services"] = _delivery_services(safe_deal)
        enabled = safe_deal.get("deliveryButtonEnabled")
        if enabled is False:
            out["safe_deal"] = False
        elif enabled is True and _text(_dict(safe_deal, "deliveryButton").get("title")) is not None:
            # An enabled button without a recognisable title is not proof either way.
            out["safe_deal"] = True
    # The form object was a dict on every captured page; any other shape is unknown, not «no form».
    if isinstance(bi.get("bargainOffer"), dict):
        out["price_offer_enabled"] = True
    for badge in (_dict(item, "badgeBar").get("badges") or [])[:10]:
        title = _text(badge.get("title"), 80) if isinstance(badge, dict) else None
        if title:
            detail = _text(badge.get("description"), 120)
            out["item_badges"].append(f"{title}: {detail}" if detail else title)
    out["location"] = _text(_dict(item, "location").get("name"), 80)
    for ref in (_dict(item, "sellerAddressInfo").get("geoReferences") or [])[:5]:
        name = _text(ref.get("content"), 80) if isinstance(ref, dict) else None
        if name:
            walk = _text(_dict(ref, "afterWithIcon").get("text"), 40)
            out["metro"].append({"name": name, "distance": _text(ref.get("after"), 40), "walk": walk})
    geo = _dict(item, "geo")
    out["address"] = _text(geo.get("address")) or _text(item.get("address"))
    coords = _dict(geo, "coords")
    lat, lng = _number(coords.get("lat")), _number(coords.get("lng"))
    if lat is not None and lng is not None:
        out["coords"] = {"lat": lat, "lng": lng}
    out["posted_at"] = _text(item.get("sortFormatedDate"), 60)
    stats = _dict(bi, "viewStat")
    out["views"], out["today_views"] = _count(stats.get("totalViews")), _count(stats.get("todayViews"))
    seller_state = _dict(bi, "seller")
    since = _text(seller_state.get("tenureSince"), 60)
    out["seller"] = {
        "checks": _seller_checks(item),
        "tenure": _text(seller_state.get("tenureMedal"), 40) or _text(_dict(item, "seller").get("tenureMedal"), 40),
        # tenureSince carried a medal word, not a date, on 2026-11-05: keep only text with a year.
        "seller_since": since if since and re.search(r"\d{4}", since) else None,
        "reply_time": _text(seller_state.get("replyTimeText"), 80),
    }
    return out
