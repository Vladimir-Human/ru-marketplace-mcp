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

Closed and gone listings (live 2026-11-06) are detected by ``closed_state``
from the page's own ``buyerItem`` closure flags, ``loaderData`` ``notFound``
entries, and ``item-view/closed-warning`` banner so ``avito_card`` raises
``NotFoundError`` with Avito's text without inferring ``sold`` (the buyer page
looks identical for sold and withdrawn ads).

Search hits carry ``isReserved`` on the js/items entry itself (live 2026-11-05:
15 of 50 hits on one page were reserved).

A sale discount (live 2026-11-06, Avito's «Чёрная пятница»: 12 of 50 hits on
one page, shown to a signed-out buyer) leaves the base price where the parser
used to read it — ``priceDetailed.value`` on a search hit, ``item.price`` in
the card state — and puts the price the buyer pays next to it:
``iva.PriceStep`` components ``price`` and ``discount`` (``old_string``,
``percent``) on the hit, ``item.formattedPrice`` (``value``, ``oldString``,
``saleDiscount``) on the card. ``search_sale_price`` / ``card_sale_price``
read them and accept a discount only when the struck price equals the base,
the sale price is below it and Avito's percent agrees with the two.
"""
# START_MODULE_CONTRACT
# PURPOSE: Classify availability from explicit service signals; read deal/seller/location fields, sale prices and closed state from the page.
# SCOPE: Pure parsing of the already-fetched item HTML and js/items entries; no I/O; seller-written text never sets a status or flag.
# DEPENDS: stdlib json/re/HTMLParser.
# LINKS: M-AVITO; R-NO-FABRICATION,R-ERRORS; packages/avito-connector/tests/test_item_state.py,packages/avito-connector/tests/test_item_closed.py,packages/avito-connector/tests/test_sale_price.py.
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
# photo_urls: Listing photo URLs (640x480 first) from the page's own gallery state, videos skipped.
# photo_url_allowed: https on *.img.avito.st only, no credentials or odd ports.
# closed_state: Whether the page's own state is known, and Avito's closure text when it says the ad is gone.
# search_sale_price: A js/items hit's sale price, struck price and percent, or why they do not add up.
# card_sale_price: The same from the card's own item.price and item.formattedPrice.
# END_MODULE_MAP

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Final, Literal
from urllib.parse import urlsplit

Availability = Literal["active", "reserved", "unknown"]
ACTIVE: Final = "active"
RESERVED: Final = "reserved"
UNKNOWN: Final = "unknown"
INACTIVE = frozenset({RESERVED})

_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
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
    """Decode window.__staticRouterHydrationData from a single real <script> element only."""
    found: list[dict[str, Any]] = []
    for script_match in _SCRIPT_RE.finditer(html):
        match = _HYDRATION_RE.search(script_match.group(1))
        if not match or len(match.group(1)) > _MAX_STATE_CHARS:
            continue
        try:
            data = json.loads(json.loads('"' + match.group(1) + '"'))
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            found.append(data)
    return found[0] if len(found) == 1 else None


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
    closed = _closed_flags(bi)
    banner = texts.get(_CLOSED_BANNER)
    if banner:
        closed.append(f"{_CLOSED_BANNER}: «{banner[:80]}»")
    if closed:
        # closed_state() lets a card through with a closure signal only when the
        # page also says it is open: report both sides, decide nothing.
        opened = _open_flags(bi)
        if opened or free:
            return UNKNOWN, ["contradicting status signals", *closed, *opened, *reserved, *free]
        return UNKNOWN, ["closed/removed signals on the page", *closed, *reserved]
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
    return classify(buyer_item(load_hydration(html), item_id), marker_texts(html, (*_SERVICE_MARKERS, _CLOSED_BANNER)))


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
_DOUBLE_NEG_BARGAIN = re.compile(rf"{_TORG}\s+не\s+(?:запрещ|исключ)", re.I)
_NO_BARGAIN = re.compile(
    r"без\s+торга"
    r"|торга\s+нет"
    r"|не\s+торгуюсь|не\s+торгуемся|торговаться\s+не\b"
    rf"|{_TORG}\s*[?:\-–—]?\s*(?:нет|не\b|неуместен|невозмож|исключ|отсутств)",
    re.I,
)
_BARGAIN = re.compile(_TORG, re.I)
_SAFE_DEAL_BUTTON = re.compile(r"доставк|купить", re.I)
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


def _list(container: Any, key: str) -> list[Any]:
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text_bargain(text: str) -> tuple[bool | None, bool]:
    """(verdict, conflicted) from the words alone; both a negation and a plain «торг» -> (None, True)."""
    if _DOUBLE_NEG_BARGAIN.search(text):
        return None, True
    negated = bool(_NO_BARGAIN.search(text))
    plain = bool(_BARGAIN.search(_NO_BARGAIN.sub(" ", text)))
    if negated and plain:
        return None, True
    if negated:
        return False, False
    return (True, False) if plain else (None, False)


def bargain(description: str | None, params: list[dict[str, str]]) -> bool | None:
    """True/False only from explicit statements; the «Предложить цену» form is not «торг».

    The explicit «Торг» parameter decides first (да/нет); otherwise the listing
    text, where negations («Торг: нет», «торг не …», «без торга») are read
    before a plain «торг». A text that contradicts the parameter, or itself, is
    None: the seller's words are data, not a flag to trust over the page.
    """
    param_values: set[bool] = set()
    for entry in params:
        if entry["name"].strip().lower() != "торг":
            continue
        value = entry["value"].strip()
        if _PARAM_YES.match(value):
            param_values.add(True)
        elif _PARAM_NO.match(value):
            param_values.add(False)
        else:
            return None
    if len(param_values) > 1:
        return None
    param = next(iter(param_values), None)
    words, conflicted = _text_bargain(description or "")
    if conflicted:
        return None
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
    for entry in _list(_dict(bi, "paramsBlock"), "items")[:40]:
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
        button_title = _text(_dict(safe_deal, "deliveryButton").get("title"))
        if enabled is False:
            out["safe_deal"] = False
        elif enabled is True and button_title is not None and _SAFE_DEAL_BUTTON.search(button_title):
            # An enabled button without a recognisable delivery/buy title is not proof either way.
            out["safe_deal"] = True
    # The form object was a dict on every captured page; any other shape is unknown, not «no form».
    if isinstance(bi.get("bargainOffer"), dict):
        out["price_offer_enabled"] = True
    for badge in _list(_dict(item, "badgeBar"), "badges")[:10]:
        title = _text(badge.get("title"), 80) if isinstance(badge, dict) else None
        if title:
            detail = _text(badge.get("description"), 120)
            out["item_badges"].append(f"{title}: {detail}" if detail else title)
    out["location"] = _text(_dict(item, "location").get("name"), 80)
    for ref in _list(_dict(item, "sellerAddressInfo"), "geoReferences")[:5]:
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


# --------------------------------------------------------------------------
# Listing photos (avito_card photos=N, opt-in).
# --------------------------------------------------------------------------

_PHOTO_SIZES = ("640x480", "1280x960")
_PHOTO_HOST = re.compile(r"(?:[a-z0-9-]+\.)?img\.avito\.st")


def photo_urls(html: str, item_id: int | None, limit: int = 20) -> list[str]:
    """Photo URLs of the requested listing from its own gallery state; videos are skipped.

    Read from ``buyerItem.galleryInfo.media[].urls`` (640x480, else 1280x960),
    the shape of every captured item page (2026-11-06); an entry marked
    ``isVideo`` or a URL off ``*.img.avito.st`` is skipped.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return []
    bi = buyer_item(load_hydration(html), item_id)
    media = _list(_dict(bi, "galleryInfo"), "media")
    urls: list[str] = []
    for entry in media:
        if not isinstance(entry, dict) or entry.get("isVideo") is not False:
            continue
        sizes = entry.get("urls")
        if not isinstance(sizes, dict):
            continue
        pick = next((sizes[size] for size in _PHOTO_SIZES if isinstance(sizes.get(size), str)), None)
        if pick and photo_url_allowed(pick) and pick not in urls:
            urls.append(pick)
            if len(urls) >= limit:
                break
    return urls


def photo_url_allowed(url: str) -> bool:
    """https on ``*.img.avito.st`` only, without credentials or a port other than 443."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = parts.hostname or ""
    return (
        parts.scheme == "https"
        and bool(_PHOTO_HOST.fullmatch(host))
        and parts.username is None
        and parts.password is None
        and port in (None, 443)
    )


# --------------------------------------------------------------------------
# Closed / removed ads (avito_card answers not_found for them).
# --------------------------------------------------------------------------

_CLOSED_BANNER = "item-view/closed-warning"


def _closed_flags(bi: dict[str, Any] | None) -> list[str]:
    """The buyerItem's own closure flags (real booleans only)."""
    if not isinstance(bi, dict):
        return []
    flags = ["buyerItem.closedItem=true"] if bi.get("closedItem") is True else []
    if _bool_at(bi.get("item"), "isClosed") is True:
        flags.append("item.isClosed=true")
    return flags


def _open_flags(bi: dict[str, Any] | None) -> list[str]:
    """The buyerItem's own open flags (real booleans only)."""
    if not isinstance(bi, dict):
        return []
    item = bi.get("item")
    flags: list[str] = []
    if _bool_at(item, "isActive") is True:
        flags.append("item.isActive=true")
    if bi.get("closedItem") is False:
        flags.append("buyerItem.closedItem=false")
    if _bool_at(item, "isClosed") is False:
        flags.append("item.isClosed=false")
    return flags


def _closure_text(value: Any, fallback: str) -> str:
    return " ".join(value.split())[:120] if isinstance(value, str) and value.strip() else fallback


def closed_state(html: str, item_id: int | None) -> tuple[bool, str | None]:
    """(own state known, Avito's closure text or None) from service data only.

    Captured 2026-11-06: a withdrawn or sold ad («Объявление снято с
    публикации.», the same text whether the seller sold it or not) still renders
    its buyerItem with ``closedItem: true``, ``closedItemStatus``,
    ``item.isClosed: true`` and ``item.isActive: false``, plus the
    ``item-view/closed-warning`` banner; a gone ad («Объявление закрыто.»)
    renders a loader entry ``type: "notFound"`` with that message and no
    buyerItem. Avito's buyer page does not say whether a closed ad was sold, so
    nothing here claims it.

    The requested id's own buyerItem decides first, ahead of any loader
    ``notFound`` entry (an auxiliary sub-route may carry one on an open page).
    Closure text is returned only for that id, and only when ``item.isActive``
    is not ``true``: closure flags against an active flag are a contradiction
    the card reports as ``unknown`` (classify), not a not_found. The page is
    known to be open when its own flags say so (``isActive``, ``closedItem`` or
    ``isClosed``); ``isActive: false`` alone says neither. A buyerItem of
    another id is not this ad's page: nothing is attributed to the requested
    id, not even the banner, and the card's identity check answers. A known
    state means the caller must not look for closure words in the visible text,
    where the title and description are the seller's. Only a page without its
    own decisive state falls back to the banner.
    """
    state = load_hydration(html)
    loader = state.get("loaderData") if isinstance(state, dict) else None
    if not isinstance(loader, dict):
        loader = {}
    bi = buyer_item(state, item_id)
    if isinstance(bi, dict):
        closed = _closed_flags(bi)
        opened = _open_flags(bi)
        if closed and not opened:
            return True, _closure_text(bi.get("closedItemStatus"), "page state: closed")
        if closed or opened:
            return True, None
    for value in loader.values():
        if isinstance(value, dict) and value.get("type") == "notFound":
            return True, _closure_text(value.get("message"), "page state: notFound")
    other = buyer_item(state, None)
    other_id = _dict(other, "item").get("id") if isinstance(other, dict) else None
    if bi is None and isinstance(other_id, int) and not isinstance(other_id, bool):
        return True, None
    banner = marker_texts(html, (_CLOSED_BANNER,)).get(_CLOSED_BANNER)
    return False, banner[:120] if banner else None


# --------------------------------------------------------------------------
# Sale prices (Avito's own discount on the listing; live 2026-11-06).
# --------------------------------------------------------------------------

_PERCENT = re.compile(r"^[\s\-\u2013\u2212]*(\d{1,2})\s*%\s*$")
SalePrice = dict[str, int]


def _rub(value: Any) -> int | None:
    """A whole positive ruble amount from an int or a «4 600»-style string; anything else is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        digits = re.sub(r"[\s\u00a0\u2009\u202f]", "", value)
        if digits.isdigit() and len(digits) <= 12:
            return int(digits) or None
    return None


def _percent(value: Any) -> int | None:
    """A sale percent 1-99 from an int or a «–5%»-style string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value < 100 else None
    match = _PERCENT.match(value) if isinstance(value, str) else None
    return _percent(int(match.group(1))) if match else None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _resolve_sale(
    base: int | None, now: int | None, old: int | None, percent: int | None
) -> tuple[SalePrice | None, str | None]:
    """A shown discount as (price, old price, percent), or the reason it does not add up."""
    if base is None or now is None or old is None:
        return None, f"incomplete discount (price {base}, sale price {now}, struck price {old})"
    if base == now and old > now and percent is None:
        return None, None
    if old != base and now != base:
        return None, f"struck price {old} is not the listing price {base}"
    if now >= old:
        return None, f"sale price {now} is not below the listing price {old}"
    computed = round(100 * (old - now) / old)
    if percent is None:
        percent = max(1, min(99, computed))
    elif abs(computed - percent) > 1:
        return None, f"{percent}% does not match {old} -> {now}"
    return {"price_rub": now, "old_price_rub": old, "discount_percent": percent}, None


def search_sale_price(item: dict[str, Any]) -> tuple[SalePrice | None, str | None]:
    """(sale, conflict) for one js/items hit; (None, None) when no discount is shown.

    ``priceDetailed.value`` is the listing price; the hit's ``iva.PriceStep``
    renders the price the buyer sees (component ``price``) and, during a sale,
    a ``discount`` component with the struck ``old_string`` and ``percent``.
    """
    steps: dict[str, dict[str, Any]] = {}
    for step in _list(_dict(item, "iva"), "PriceStep"):
        name = _dict(step, "componentData").get("component")
        if isinstance(name, str) and name not in steps:
            steps[name] = _dict(step, "payload")
    base = _rub(_dict(item, "priceDetailed").get("value"))
    now = _rub(_dict(steps.get("price"), "priceDetailed").get("value"))
    discount = steps.get("discount")
    if discount is None or (_blank(discount.get("old_string")) and _blank(discount.get("percent"))):
        if now is not None and base is not None and now != base:
            return None, f"shown price {now} differs from the listing price {base} without a discount"
        return None, None
    return _resolve_sale(base, now, _rub(discount.get("old_string")), _percent(discount.get("percent")))


def card_sale_price(html: str, item_id: int | None) -> tuple[SalePrice | None, str | None]:
    """(sale, conflict) from the card's own ``item.price`` and ``item.formattedPrice``.

    ``item.price`` is the listing price; ``formattedPrice.value`` is the price
    the page shows, and during a sale ``oldString`` is the struck price and
    ``saleDiscount`` Avito's percent («–5%»).
    """
    item = _dict(buyer_item(load_hydration(html), item_id), "item")
    shown = _dict(item, "formattedPrice")
    base, now = _rub(item.get("price")), _rub(shown.get("value"))
    old, percent = _rub(shown.get("oldString")), _percent(shown.get("saleDiscount"))
    if _blank(shown.get("oldString")) and _blank(shown.get("saleDiscount")):
        if now is not None and base is not None and now != base:
            return None, f"shown price {now} differs from the listing price {base} without a discount"
        return None, None
    return _resolve_sale(base, now, old, percent)
