"""Avito listing availability: active, reserved, sold, closed or unknown.

The rendered item page that ``avito_card`` already reads carries the page's own
state as ``window.__staticRouterHydrationData = JSON.parse("...")``; its
``loaderData`` holds ``buyerItem`` with the item and its safe-deal block. The
status is read from that same document and from the visible status banners:
no extra request to Avito.

Verified live 2026-11-05 on two reserved private listings: the schema.org
offer said ``availability: InStock`` and ``item.isActive`` was true, while the
page showed «Товар зарезервирован» and ``item.deliveryInfo.isReserved`` /
``safeDealDesktop.reserved`` were true. So neither JSON-LD availability nor
``isActive`` decides the status: explicit reservation, sale or closure signals
do, ``active`` needs an explicit ``isReserved: false`` next to ``isActive``,
and anything else is ``unknown`` rather than a guess.

Search hits carry ``isReserved`` on the js/items entry itself (live 2026-11-05:
15 of 50 hits on one page were reserved).
"""
# START_MODULE_CONTRACT
# PURPOSE: Classify an Avito listing's availability from explicit page/search signals only.
# SCOPE: Pure parsing of the already-fetched item HTML and js/items entries; no I/O.
# DEPENDS: stdlib json/re/HTMLParser.
# LINKS: M-AVITO; R-NO-FABRICATION; packages/avito-connector/tests/test_item_state.py.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# load_hydration: Decode window.__staticRouterHydrationData from the page, bounded in size.
# buyer_item: Return the page's buyerItem only when its item id matches the requested one.
# marker_texts: Visible text of the status-bearing data-marker elements (scripts ignored).
# classify: availability + evidence from explicit flags/banners; contradictions or silence -> unknown.
# card_availability: classify() over one item page.
# closed_page_evidence: Explicit closure/sale wording on a page without a Product.
# search_availability: active/reserved/unknown from a js/items entry's own isReserved.
# END_MODULE_MAP

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Final, Literal

Availability = Literal["active", "reserved", "sold", "closed", "unknown"]
SearchAvailability = Literal["active", "reserved", "unknown"]
ACTIVE: Final = "active"
RESERVED: Final = "reserved"
SOLD: Final = "sold"
CLOSED: Final = "closed"
UNKNOWN: Final = "unknown"
INACTIVE = frozenset({RESERVED, SOLD, CLOSED})

_HYDRATION_RE = re.compile(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:[^"\\]|\\.)*)"\)', re.S)
_MAX_STATE_CHARS = 4 * 1024 * 1024

_RESERVED_TEXT = re.compile(r"Товар\s+зарезервирован|Зарезервирован[оа]?\b|Забронирован[оа]?\b", re.I)
_SOLD_TEXT = re.compile(r"\bПродан[оа]?\b|Товар\s+продан", re.I)
_CLOSED_TEXT = re.compile(
    r"Объявление\s+снято\s+с\s+публикации|Срок\s+публикации\s+(?:объявления\s+)?ист[её]к|Объявление\s+закрыто",
    re.I,
)
# Elements whose visible text states the listing's status on the item page.
_STATUS_MARKERS = (
    "item-view/title-info",
    "safedeal-item-card",
    "item-view/item-view-contacts",
    "item-closed-warning",
    "item-view/closed-warning",
)
_CLOSED_FLAGS = ("isClosed", "isExpired", "isArchived", "isDeleted", "isFinished", "isBlocked", "isRejected")


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


def marker_texts(html: str, markers: tuple[str, ...] = _STATUS_MARKERS) -> dict[str, str]:
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


def _flag(container: Any, key: str) -> bool:
    return isinstance(container, dict) and container.get(key) is True


def _flag_false(container: Any, key: str) -> bool:
    return isinstance(container, dict) and container.get(key) is False


def classify(bi: dict[str, Any] | None, texts: dict[str, str]) -> tuple[Availability, list[str]]:
    """Availability from explicit signals only; silence is ``unknown``.

    Precedence: sold, then closed, then reserved; ``active`` only when the page
    state says ``isActive: true`` and ``deliveryInfo.isReserved: false`` and
    nothing else contradicts it.
    """
    item = bi.get("item") if isinstance(bi, dict) else None
    closed: list[str] = []
    sold: list[str] = []
    reserved: list[str] = []
    if isinstance(bi, dict) and isinstance(item, dict):
        if bi.get("closedItem") is True:
            closed.append("buyerItem.closedItem=true")
        status = bi.get("closedItemStatus")
        if isinstance(status, str) and status.strip():
            bucket = sold if re.search(r"sold|продан", status, re.I) else closed
            bucket.append(f"closedItemStatus={status.strip()[:60]}")
        for key in _CLOSED_FLAGS:
            if item.get(key) is True:
                closed.append(f"item.{key}=true")
        if _flag(item.get("deliveryInfo"), "isReserved"):
            reserved.append("item.deliveryInfo.isReserved=true")
        if _flag(bi.get("safeDealDesktop"), "reserved"):
            reserved.append("safeDealDesktop.reserved=true")
        if bi.get("reservationInfo"):
            reserved.append("buyerItem.reservationInfo present")
    for marker, text in texts.items():
        for pattern, bucket in ((_CLOSED_TEXT, closed), (_SOLD_TEXT, sold), (_RESERVED_TEXT, reserved)):
            hit = pattern.search(text)
            if hit:
                bucket.append(f"{marker}: «{hit.group(0)}»")
    if sold:
        return SOLD, sold + closed
    if closed:
        return CLOSED, closed
    if reserved:
        return RESERVED, reserved
    delivery = item.get("deliveryInfo") if isinstance(item, dict) else None
    if isinstance(item, dict) and item.get("isActive") is True and _flag_false(delivery, "isReserved"):
        return ACTIVE, ["item.isActive=true", "item.deliveryInfo.isReserved=false"]
    return UNKNOWN, ["no explicit status signal on the page"]


def card_availability(html: str, item_id: int | None) -> tuple[Availability, list[str]]:
    return classify(buyer_item(load_hydration(html), item_id), marker_texts(html))


def closed_page_evidence(html: str) -> list[str]:
    """Explicit closure/sale wording in the visible markup (scripts and styles stripped)."""
    visible = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    visible = " ".join(re.sub(r"<[^>]+>", " ", visible).split())
    found: list[str] = []
    for pattern in (_CLOSED_TEXT, _SOLD_TEXT):
        hit = pattern.search(visible)
        if hit:
            found.append(f"page text: «{hit.group(0)}»")
    return found


def search_availability(item: dict[str, Any]) -> SearchAvailability:
    """A search hit is a published listing; only its own isReserved flag tells reserved from active."""
    value = item.get("isReserved")
    if value is True:
        return RESERVED
    if value is False:
        return ACTIVE
    return UNKNOWN
