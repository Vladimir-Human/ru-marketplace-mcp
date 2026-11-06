"""Avito listing availability: active, reserved or unknown.

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
# PURPOSE: Classify an Avito listing as active/reserved/unknown from explicit service signals only.
# SCOPE: Pure parsing of the already-fetched item HTML and js/items entries; no I/O; seller-written text never read.
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
