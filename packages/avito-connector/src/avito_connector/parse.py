"""Pure Avito parsing helpers: item pages, seller blocks and profiles, search envelopes.

Moved verbatim out of server.py (move-only); server.py re-imports every name, so
``server.parse_item_page`` and the other helpers keep working for callers and tests.
"""
# START_MODULE_CONTRACT
# PURPOSE: Pure parsing for the Avito tools (no network): item pages, seller blocks/profiles, search envelopes.
# SCOPE: HTML/JSON-LD item cards with identity-checked DOM fallback, seller reputation and profile links, search items and errors.
# DEPENDS: stdlib HTMLParser,json,re,urllib,datetime; mcp_core resilience/errors/redact/kit/sources; item_state; privacy.
# LINKS: M-AVITO; R-NO-PERSONAL-DATA; R-NAV-HOSTS.invariant; packages/avito-connector/tests.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _ItemPageHTML: Collect JSON-LD scripts and non-script page text.
# parse_item_page: Normalize schema.org Product first; use item-view DOM only when absent; reject empty cards and removed ads;
#                  a Product without a sku is checked by the item id in its canonical URL.
# _offer_item_id: Read the item id from the canonical URL the page records for a Product, or None.
# _parse_item_dom: Require matching page item id; read marked title, explicit rouble price and description.
# _SellerPageHTML: Collect seller markers, title, rating metadata and profile links.
# parse_item_seller: Read nullable seller reputation, explicit company label and canonical profile link.
# parse_seller_profile: Read profile title/score/reviews; reject 404 and empty profiles.
# _seller_profile_url: Validate Avito-only profile URLs or expand bare seller ids.
# _raise_for_search_error: Classify HTTP200 JSON challenge/rate/upstream errors from envelope metadata only.
# _parse_search_items: Require a known items list (including []); normalize listings, dates, places and nullable prices;
#                      a sale discount gives the price paid plus old_price_rub/discount_percent (item_state).
# END_MODULE_MAP

from __future__ import annotations

import datetime
import json
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any

from mcp_core import resilience as R
from mcp_core.errors import (
    BadRequestError,
    ChallengeRequiredError,
    NotFoundError,
    ParserDriftError,
    RateLimitedError,
    TransportDownError,
)
from mcp_core.kit import url_on_hosts
from mcp_core.redact import redact_error_text as _redact
from mcp_core.sources import BY_NAME

from avito_connector import item_state as _state
from avito_connector.privacy import public_seller_name

ALLOWED_HOSTS = BY_NAME["avito"].page_hosts


SITE_BASE = "https://www.avito.ru"


# The canonical item URL carries the id in the tail of its last segment, however
# many segments the slug in front of it has (see _offer_item_id).
_URL_TAIL_ID_RE = re.compile(r"(?:.*_)?(\d{6,})")


class _ItemPageHTML(HTMLParser):
    """Collect JSON-LD scripts and visible text without executing page content."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.text: list[str] = []
        self._hidden: str | None = None
        self._json_ld = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._hidden = tag
            self._json_ld = tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._json_ld:
            self._parts.append(data)
        elif self._hidden is None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._hidden:
            if self._json_ld:
                self.scripts.append("".join(self._parts))
            self._hidden = None
            self._json_ld = False


def _offer_item_id(product: dict[str, Any]) -> int | None:
    """The item id in the canonical URL the page records for this Product.

    Avito redirects /<id> to the canonical item page, whose schema.org offer
    carries that URL; its last segment ends in the id (…/noutbuk_4612345678).
    No offer URL, or no id in it, answers None: the page is then unproven.
    """
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), {})
    if not isinstance(offers, dict):
        return None
    url = R.flatten_text(offers.get("url"))
    if not url:
        return None
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return None
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    match = _URL_TAIL_ID_RE.fullmatch(segment)
    return int(match.group(1)) if match else None


def parse_item_page(html: str, item_id: int | None = None) -> dict[str, Any]:
    """Extract the first schema.org Product, preferring a matching requested sku.

    Supports @graph and bare Product objects. Only RUB (or unspecified currency)
    offers become price_rub; absent/unknown prices are None, never invented zero.
    A Product without a sku carries no identity of its own, so the canonical URL
    the page records for it must name the requested item.
    If there is no Product, use identity-checked item-view DOM markers with an
    explicit rouble price. location, posted_at, views and seller stay None;
    seller parsing is separate. Availability is classified separately
    (item_state.card_availability) from the page state, never from JSON-LD.
    Raises raw ParserDriftError on empty Product or missing/invalid item DOM,
    NotFoundError on a removed/missing-ad page; the MCP adapter serializes these.
    """
    # Avito's own state first: a closed or gone ad is not_found with Avito's
    # closure text, and a page whose own state says it is open is never closed
    # by words in the visible text (the title and description are the seller's).
    state_known, closure = _state.closed_state(html, item_id)
    if closure:
        raise NotFoundError(f"Avito item page: the ad is closed or removed («{closure}»).")
    document = _ItemPageHTML()
    document.feed(html)
    document.close()
    products: list[dict[str, Any]] = []

    def collect(node: Any) -> None:
        if isinstance(node, list):
            for entry in node:
                collect(entry)
        elif isinstance(node, dict):
            types = node.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if isinstance(types, list) and any(
                kind in ("Product", "http://schema.org/Product", "https://schema.org/Product") for kind in types
            ):
                products.append(node)
            collect(node.get("@graph"))

    for script in document.scripts:
        try:
            collect(json.loads(script))
        except json.JSONDecodeError:
            # A malformed unrelated script must not hide a valid Product.
            continue
    if not products:
        visible = " ".join(document.text)
        if not state_known and re.search(
            r"Объявление\s+не\s+найдено|снято\s+с\s+публикации|Ошибка\s+404|Страница\s+не\s+найдена",
            visible,
            re.IGNORECASE,
        ):
            raise NotFoundError("Avito item page: announcement not found or removed.")
        return _parse_item_dom(html, item_id)
    product = next((p for p in products if item_id is not None and str(p.get("sku")) == str(item_id)), None)
    if product is None:
        # A Product for another sku (a recommendation block) must never stand in for
        # the requested ad; only a lone Product without any sku is taken as the page's.
        if item_id is not None and (len(products) != 1 or products[0].get("sku") not in (None, "")):
            raise ParserDriftError(f"avito_card: no schema.org Product for item {item_id} on its page")
        product = products[0]
        # A lone Product without a sku says nothing about WHICH ad it describes, so
        # the canonical URL the page itself records must name the requested item:
        # that URL is where the /<id> request redirects to. An id that is absent
        # or names another ad is the same mismatch as a wrong sku.
        if item_id is not None and _offer_item_id(product) != item_id:
            raise ParserDriftError(f"avito_card: no schema.org Product for item {item_id} on its page")
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), {})
    if not isinstance(offers, dict):
        offers = {}
    title = R.flatten_text(product.get("name"))
    price = R.coerce_price(offers.get("price")) if offers.get("priceCurrency") in (None, "RUB") else None
    if title is None and price is None:
        raise ParserDriftError("avito_card: schema.org Product carries neither name nor RUB price")
    images = product.get("image")
    condition = str(offers.get("itemCondition") or product.get("itemCondition") or "").rsplit("/", 1)[-1]
    return {
        "condition": _LD_CONDITIONS.get(condition),
        "item_id": item_id if item_id is not None else R.coerce_int(product.get("sku")),
        "title": title,
        "price_rub": price,
        "description": R.flatten_text(product.get("description")),
        # schema.org allows one image as a bare URL; no image key means unknown, not zero.
        "images": len(images) if isinstance(images, list) else 1 if isinstance(images, str) and images else None,
        "url": R.flatten_text(offers.get("url")),
        "location": None,
        "posted_at": None,
        "views": None,
        "seller": None,
    }


class _SellerPageHTML(HTMLParser):
    """Collect marker text, title and profile links, ignoring scripts/styles."""

    _VOID = frozenset(
        {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    )

    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[dict[str, Any]] = []
        self.stack: list[int] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        node = {"tag": tag, "attrs": attributes, "parts": [], "ancestors": self.stack.copy(), "rating": None}
        self.nodes.append(node)
        if attributes.get("itemprop") == "ratingValue":
            for index in self.stack:
                self.nodes[index]["rating"] = attributes.get("content")
        if tag not in self._VOID:
            self.stack.append(len(self.nodes) - 1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for position in range(len(self.stack) - 1, -1, -1):
            if self.nodes[self.stack[position]]["tag"] == tag:
                del self.stack[position:]
                break

    def handle_data(self, data: str) -> None:
        if any(self.nodes[index]["tag"] in ("script", "style") for index in self.stack):
            return
        self.text.append(data)
        for index in self.stack:
            self.nodes[index]["parts"].append(data)

    def marked(self, marker: str) -> dict[str, Any] | None:
        return next((node for node in self.nodes if node["attrs"].get("data-marker") == marker), None)


def _seller_text(node: dict[str, Any] | None) -> str | None:
    return R.flatten_text(" ".join(" ".join(node["parts"]).split())) if node else None


def _parse_item_dom(html: str, item_id: int | None) -> dict[str, Any]:
    """Read item-view markers only when there is no Product JSON-LD.

    Reuse the seller DOM collector; never infer identity from the requested URL,
    the generic page title, recommendations or the operator's account header.
    """
    document = _SellerPageHTML()
    document.feed(html)
    document.close()
    id_nodes = [node for node in document.nodes if node["attrs"].get("data-marker") == "item-view/item-id"]
    id_matches = [re.fullmatch(r"(?:№\s*)?(\d+)", _seller_text(node) or "") for node in id_nodes]
    page_id = int(id_matches[0].group(1)) if id_matches and id_matches[0] else None
    if (
        page_id is None
        or any(match is None or int(match.group(1)) != page_id for match in id_matches)
        or (item_id is not None and page_id != item_id)
    ):
        raise ParserDriftError(f"avito_card: DOM item id is missing or does not match item {item_id}")
    title_node = document.marked("item-view/title-info")
    title = _seller_text(title_node) if title_node and title_node["tag"] == "h1" else None
    price_node = document.marked("item-view/item-price")
    if price_node is None:
        price_node = document.marked("item-view/item-price-container")
    price_text = _seller_text(price_node) or ""
    # Unlike JSON-LD, DOM text has no implicit currency. Require the rouble sign
    # and a single ungrouped or correctly space-grouped amount (optional kopecks).
    # Never concatenate two amounts sharing a currency sign or malformed groups.
    amount = re.fullmatch(r"(?:\d+|\d{1,3}(?: \d{3})+)(?:[.,]\d{1,2})?\s*₽", price_text)
    price = R.coerce_price(price_text) if amount else None
    if title is None and price is None:
        raise ParserDriftError("avito_card: item page carries neither DOM title nor RUB price (no schema.org Product)")
    return {
        "item_id": page_id,
        "title": title,
        "price_rub": price,
        "description": _seller_text(document.marked("item-view/item-description")),
        "images": None,  # No reliable gallery total in the captured item-view blocks.
        "url": None,
        "location": None,
        "posted_at": None,
        "views": None,
        "seller": None,
    }


def _seller_score(text: str | None) -> float | None:
    match = re.search(r"(?<!\d)([0-5](?:[.,]\d+)?)(?!\d)", text or "")
    if not match:
        return None
    score = float(match.group(1).replace(",", "."))
    return score if 0 <= score <= 5 else None


def _seller_review_count(text: str | None) -> int | None:
    match = re.search(r"(?<![\d.,])(\d+(?:[ \t\xa0\u202f]+\d{3})*)\s+отзыв", text or "", re.IGNORECASE)
    return int(re.sub(r"\s", "", match.group(1))) if match else None


def _seller_profile_url(raw: str) -> str:
    """Validate an Avito profile URL/path or expand a bare seller handle."""
    raw = raw.strip()
    try:
        parts = urllib.parse.urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise BadRequestError("invalid Avito seller URL") from exc
    if parts.scheme or parts.netloc:
        if (
            not url_on_hosts(raw, ALLOWED_HOSTS)
            or parts.username is not None
            or parts.password is not None
            or port not in (None, 80, 443)
        ):
            raise BadRequestError("pass an http(s) avito.ru seller profile URL without credentials")
    elif not raw.startswith("/"):
        if not re.fullmatch(r"[0-9A-Za-z_-]+", raw):
            raise BadRequestError("pass a seller id or an Avito /brands/<id> or /user/<id>/profile URL")
        path = f"/brands/{raw}" if re.fullmatch(r"[0-9a-fA-F]{32}", raw) else f"/user/{raw}/profile"
        return f"{SITE_BASE}{path}"
    if not re.fullmatch(r"/(?:brands/[0-9A-Za-z_-]+|user/[0-9A-Za-z_-]+/profile)/?", parts.path):
        raise BadRequestError("expected an Avito /brands/<id> or /user/<id>/profile URL")
    return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc or "www.avito.ru", parts.path, "", ""))


def _score_after_name(document: _SellerPageHTML, name_node: dict[str, Any] | None) -> str | None:
    """The first «d,d» text node after the seller name, inside the seller block.

    The scan stops at the seller's review caption (rating-caption/rating), which
    follows the score in the block, so a product-model rating elsewhere on the
    page can never be read as the seller's.
    """
    if name_node is None:
        return None
    start = next(index for index, node in enumerate(document.nodes) if node is name_node)
    caption = document.marked("rating-caption/rating")
    stop = next((index for index, node in enumerate(document.nodes) if node is caption), None)
    end = stop if stop is not None and stop > start else start + 60
    for node in document.nodes[start + 1 : end]:
        text = _seller_text(node)
        if text and re.fullmatch(r"[0-5][.,]\d", text):
            return text
    return None


def parse_item_seller(html: str, *, company_hint: bool | None = None) -> dict[str, Any]:
    """Seller block: unknown -> None, never 0; company = label, else page flag; private name -> None."""
    document = _SellerPageHTML()
    document.feed(html)
    document.close()
    name_node = document.marked("seller-info/name")
    score_node = document.marked("rating-caption/rating") or document.marked("sellerRate")
    labels = [_seller_text(node) for node in document.nodes if node["attrs"].get("data-marker") == "seller-info/label"]
    label = next((lbl for lbl in labels if lbl), None)
    label_types = {lbl != "Частное лицо" for lbl in labels if lbl in ("Частное лицо", "Компания", "Магазин")}
    is_company = False if False in label_types else (True if True in label_types else company_hint)
    rating_container = next(
        (node for node in document.nodes if "seller-info-rating" in (node["attrs"].get("class") or "").split()),
        None,
    )
    # Live 2026-10-12: rating-caption/rating holds the review count («563 отзыва»),
    # the visible «5,0» is a bare span right after the seller name, and sellerRate
    # only carries an integer ratingValue. Read the score next to the name first —
    # never anywhere on the page, which also shows the product model's own rating.
    score_text = _score_after_name(document, name_node)
    if not score_text:
        rate_node = document.marked("sellerRate")
        score_text = rate_node["rating"] if rate_node else None
    if not score_text:
        visible_score = re.search(r"(?<!\d)[0-5][.,]\d+(?!\d)", _seller_text(rating_container) or "")
        score_text = visible_score.group(0) if visible_score else _seller_text(score_node)
    profile_url = None
    if name_node is not None:
        name_index = next(index for index, node in enumerate(document.nodes) if node is name_node)
        links = [node for node in document.nodes if node["tag"] == "a" and node["attrs"].get("href")]

        # A full item page also links to the operator's own account in its
        # header. Prefer the actual seller-name link, then the nearest enclosing
        # seller block; never blindly take the first /user/ link in the page.
        def link_priority(node: dict[str, Any]) -> int:
            if name_index in node["ancestors"] or node["attrs"].get("data-marker") == "seller-link/link":
                return -2
            if _seller_text(node) == _seller_text(name_node):
                return -1
            for distance, ancestor in enumerate(reversed(name_node["ancestors"])):
                if ancestor in node["ancestors"] and document.nodes[ancestor]["tag"] not in ("html", "body"):
                    return distance
            return len(document.nodes)

        for node in sorted(links, key=link_priority):
            if link_priority(node) == len(document.nodes):
                continue
            href = node["attrs"]["href"]
            if "/brands/" in href or "/user/" in href:
                try:
                    profile_url = _seller_profile_url(href)
                    break
                except BadRequestError:
                    continue
    block_present = name_node is not None or score_node is not None or label is not None
    # The count comes from the seller block only: the page also shows the product
    # model's own «N отзывов о модели», which must never become the seller's.
    review_text = _seller_text(rating_container) or _seller_text(document.marked("rating-caption/rating"))
    return {
        "name": public_seller_name(_seller_text(name_node), is_company, profile_url),
        "rating_score": _seller_score(score_text) if block_present else None,
        "rating_count": _seller_review_count(review_text) if block_present else None,
        "is_company": is_company,
        "profile_url": profile_url,
    }


def parse_seller_profile(html: str, profile_url: str | None = None) -> dict[str, Any]:
    """Profile name/score/reviews; 404 -> NotFoundError, neither name nor score -> drift; private name -> None."""
    document = _SellerPageHTML()
    document.feed(html)
    document.close()
    visible = " ".join(document.text)
    if re.search(r"Ошибка\s+404|Страница\s+не\s+найдена|Пользователь\s+не\s+найден", visible, re.IGNORECASE):
        raise NotFoundError("Avito seller profile not found.")
    title = _seller_text(next((node for node in document.nodes if node["tag"] == "title"), None))
    # The separator distinguishes a seller title from generic Avito pages.
    name = R.flatten_text(title.split(" - ", 1)[0]) if title and " - " in title else None
    score = _seller_score(_seller_text(document.marked("profile/score")))
    if name is None and score is None:
        raise ParserDriftError("avito_seller: profile page carries neither seller name nor rating score")
    return {
        "name": public_seller_name(name, parse_item_seller(html)["is_company"], profile_url),
        "rating_score": score,
        "rating_count": _seller_review_count(_seller_text(document.marked("profile/summary"))),
    }


def _posted_at(item: dict[str, Any]) -> str | None:
    """Publication time as an ISO-8601 string, or an honest None.

    The live payload carries no `date`/`time` string — it carries
    `sortTimeStamp`/`allowTimeStamp` in epoch milliseconds (verified 2026-07-28
    against a real response). Reading only the string aliases left `posted_at`
    null on every row, so the epoch form is handled here and rendered as ISO-8601
    UTC rather than passed through as a bare 13-digit number, which no caller
    could tell from an id.
    """
    text = R.flatten_text(R.first_present(item, "date", "publishedAt", "postedAt", "time"), "text", "value", "name")
    if text:
        return text
    stamp = R.first_present(item, "sortTimeStamp", "allowTimeStamp")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return None
    try:
        # Milliseconds since epoch; guard against a seconds-based drift
        # upstream. The division stays inside the try: json.loads admits
        # arbitrary-precision ints, and a huge stamp overflows the float
        # arithmetic before fromtimestamp is ever reached.
        seconds = stamp / 1000.0 if stamp > 1e11 else float(stamp)
        moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.isoformat().replace("+00:00", "Z")


def _raise_for_search_error(payload: dict) -> None:
    """Reject explicit API errors even when the transport returned HTTP 200.

    Inspect error metadata only, never listing titles/descriptions: ads may
    legitimately mention captcha or firewall software. A captcha link takes
    precedence over the firewall's misleading too-many-requests envelope key.
    """
    errors = {
        key: payload[key] for key in ("error", "errors", "too-many-requests", "firewall", "captcha") if payload.get(key)
    }
    failed = payload.get("success") is False or payload.get("status") in ("error", "fail", "failed")
    if not errors and not failed:
        return
    detail = json.dumps(
        {**errors, **{key: payload[key] for key in ("code", "status", "message") if key in payload}},
        ensure_ascii=False,
    )
    markers = detail.lower()
    if any(marker in markers for marker in ("captcha", "challenge", "firewall", "капча", "не робот")):
        raise ChallengeRequiredError(
            "Avito search requires a browser challenge. Open avito.ru in the Chrome CDP profile, "
            "solve the captcha, then retry.",
            provider="avito",
        )
    if re.search(r"too[-_ ]many[-_ ]requests|rate[-_ ]?limit|\b429\b", markers):
        raise RateLimitedError("avito")
    raise TransportDownError(_redact(f"Avito search API returned an error: {detail}"), provider="avito")


def _parse_search_items(payload: dict) -> tuple[list[dict[str, Any]], int | None]:
    """Best-effort extraction of items + total from a js/items payload.

    The endpoint has shipped several envelope shapes; tolerate the known ones
    and record drift loudly rather than guessing. Explicit API errors raise
    ConnectorError; a missing list raises ParserDriftError, unlike a real [].
    """
    _raise_for_search_error(payload)
    candidates = (
        payload.get("items"),
        payload.get("catalog", {}).get("items") if isinstance(payload.get("catalog"), dict) else None,
        payload.get("data", {}).get("items") if isinstance(payload.get("data"), dict) else None,
    )
    items_raw = next((c for c in candidates if isinstance(c, list)), None)
    if items_raw is None:
        raise ParserDriftError(
            "Avito search payload has no items list at items, catalog.items or data.items", provider="avito"
        )
    total = R.first_present(payload, "totalCount", "total", "count")
    if not isinstance(total, int):
        total = R.coerce_int(total)
    out: list[dict[str, Any]] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        item_id = R.first_present(it, "id", "itemId", "item_id")
        item_id = R.coerce_int(item_id)
        title = R.flatten_text(R.first_present(it, "title", "name"))
        price = R.coerce_price(R.first_present(it, "price", "priceRub", "price_rub"))
        if price is None and isinstance(it.get("priceDetailed"), dict):
            price = R.coerce_price(R.first_present(it["priceDetailed"], "value", "price"))
        # During an Avito sale priceDetailed.value stays the listing price; the
        # price the buyer pays is in the hit's own PriceStep (live 2026-11-06).
        sale, price_conflict = _state.search_sale_price(it)
        if sale is not None and price not in (None, sale["price_rub"], sale["old_price_rub"]):
            price_conflict = f"price {price} is neither {sale['price_rub']} nor {sale['old_price_rub']}"
        if price_conflict:
            price, sale = None, None
        elif sale is not None:
            price = float(sale["price_rub"])
        # The live js/items key is `urlPath`. The alias list used to read `uriPath`
        # — one letter out — so every search hit came back with url=None while the
        # rest of the row parsed fine. Verified 2026-07-28 against a real response
        # captured from a residential session: `urlPath` is the ONLY url-bearing
        # key on the item (there is no `uri`, `url` or `itemUrl`).
        uri = R.first_present(it, "uri", "url", "urlPath", "uriPath", "itemUrl") or ""
        if uri and uri.startswith("/"):
            uri = SITE_BASE + uri
        # location arrives as a nested object on the live endpoint, not a string:
        # {"id": 637640, "name": "Москва", ...}. flatten_text pulls the name out
        # (or yields None) so one extra level of nesting upstream can no longer
        # fail validation for the entire page of listings.
        location = R.flatten_text(
            R.first_present(it, "location", "locationName", "address"),
            "name",
            "title",
            "formattedAddress",
        )
        if location is None:
            # Real fallbacks present in the live payload, in order of specificity.
            # `addressDetailed.locationName` can be "" for an item Avito itself has
            # no place name for; flatten_text turns that into None rather than an
            # empty string masquerading as an answer. A bare `locationId` is NOT
            # resolved to a city — there is no lookup table here, and guessing one
            # would be inventing data.
            location = R.flatten_text(it.get("addressDetailed"), "locationName", "name") or R.flatten_text(
                it.get("geo"), "formattedAddress", "name", "title"
            )
        seller_raw = it.get("seller")
        seller = seller_raw if isinstance(seller_raw, dict) else {}
        images = it.get("images")
        images_count = (
            len(images)
            if isinstance(images, list)
            else R.coerce_int(R.first_present(it, "imagesCount", "images_count")) or 0
        )
        out.append(
            {
                "item_id": item_id,
                "title": title,
                "price_rub": price,
                "old_price_rub": float(sale["old_price_rub"]) if sale else None,
                "discount_percent": sale["discount_percent"] if sale else None,
                "price_conflict": bool(price_conflict),
                "url": uri or None,
                "location": location,
                "seller_name": public_seller_name(
                    R.first_present(seller, "name", "title") if seller else it.get("sellerName"),
                    R.first_present(seller, "isCompany", "is_company"),
                    R.flatten_text(R.first_present(seller, "profileUrl", "profile_url", "url", "uri")),
                ),
                "seller_id": str(R.first_present(seller if seller else {}, "id", "userId", "user_id", default="") or "")
                or None,
                "is_company": R.first_present(seller, "isCompany", "is_company") if seller else None,
                "posted_at": _posted_at(it),
                "images": images_count,
                "availability": _state.search_availability(it),
            }
        )
    return out, total


_LD_CONDITIONS = {
    "NewCondition": "Новое",
    "UsedCondition": "Б/у",
    "RefurbishedCondition": "Восстановленное",
    "DamagedCondition": "Повреждённое",
}
