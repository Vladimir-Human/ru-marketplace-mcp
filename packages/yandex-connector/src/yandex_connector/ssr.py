"""Extract product data from Yandex Market's server-rendered state.

Yandex Market has no reachable JSON API: ``/api/resolve`` answers 403, the
internal product endpoint speaks gRPC, and the old public Content API is dead.
What it does serve — to ordinary clients, without a captcha — is a fully
server-rendered page carrying its own widget state as JSON. This module reads
that state.

The state arrives as a series of ``<noframes data-apiary="patch">`` blocks
holding normalised entity *collections* (``offer``, ``product``, ``shop``,
``vendor``, ``productSnippet``, ``reviews``) keyed by id. Two shapes exist, and
the difference matters:

**Search pages** nest one big collection bundle inside a single lazy-loader
widget, under a generated path key. That key is garbage
(``/content/page/fancyPage/.../lazyGenerator``) and must never be hardcoded — it
is located by scanning for the bundle that actually holds products.

**Product pages** spread collections across dozens of small top-level patches,
which are merged. The merge must not let a later empty value clobber an earlier
populated one; that single rule is the difference between reading 13 reviews and
reading none.

Pure standard library on purpose — an HTML parser dependency would buy nothing
here, since the payload is JSON once located.

Verified against live pages Jul 2026 (search, cards across categories, empty
results, pagination, A/B duplicates) and re-verified live Sep 2026 (search
price semantics against the product card for the same offer).

**Hollow frames.** Live-verified 2026-09-13 (residential RU IP): Yandex can
serve a product page as an empty frame — ``pageId: market:product`` with every
product collection empty, no schema.org Product and no captcha marker — while
the product is alive in search, and the same URL shows SmartCaptcha to a real
browser. ``parse_card`` reports that page as ``EMPTY_PRODUCT_SHELL``: degraded
serving, never parser drift (the field families did not change shape; they are
absent). The same day, search pages arrived without the widget-state bundle at
all, readable only through the schema.org fallback.

**Zone blobs (first screen).** Since 2026-09-12/13 the anonymous search page
no longer embeds the product *collections* — Yandex moved them to client-side
lazy loading — but the first screen of snippets is still server-rendered as
DOM, and every ``<div data-zone-name="productSnippet">`` carries its own
analytics payload in an HTML-escaped ``data-zone-data`` attribute: marketSku,
oskuId, title, the base price, ``additionalPrices`` (``withDiscount`` =
everyday, ``yaBank`` = Plus), vendor/shop ids, rating and availability. That
is the same ``baobabPayload`` the collections used to carry, minus the joins.
``parse_zone_items`` reads it (live capture 2026-09-13, 8 snippets on the
anonymous page, 19 in a hydrated browser DOM), and ``parse_search`` uses it
whenever the collections are missing — before the thinner schema.org fallback.
"""

from __future__ import annotations

import html as _html
import json
import math
import re
from typing import Any

# One state fragment. Yandex emits ~150 of these per search page, ~200 per card.
_PATCH_RE = re.compile(r'<noframes data-apiary="patch">(.*?)</noframes>', re.S)

_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)

# Opening tag of a server-rendered SERP snippet. Attribute order is not fixed
# (class/id/data-daemon come and go), so the tag is matched on the zone name
# and the payload is pulled out of it separately.
_ZONE_SNIPPET_TAG_RE = re.compile(r'<[a-zA-Z][^>]*\bdata-zone-name="productSnippet"[^>]*>')
_ZONE_DATA_ATTR_RE = re.compile(r'\bdata-zone-data="([^"]*)"')

# Visible copy shown when a query genuinely matched nothing. Distinguishing "no
# results" from "our parser broke" is the whole point of tracking it.
EMPTY_RESULT_MARKER = "Попробуйте сформулировать запрос"

# Real captcha markers. Plain "captcha" is useless as a signal: every healthy
# page ships an empty <div id=/content/captchaService> placeholder.
_CAPTCHA_MARKERS = ("SmartCaptcha", "/showcaptcha", "checkbox_captcha")

_ORIG_SUFFIX = "/orig"


class ParseStatus:
    """Outcomes a parse can have, kept explicit so callers can branch on them."""

    OK = "ok"
    # The collections were missing but the first screen of snippets carried its
    # ``data-zone-data`` payloads: both prices, title, sku, rating per row.
    OK_ZONE = "ok_zone"
    OK_LDJSON_ONLY = "ok_ldjson_only"
    EMPTY = "empty"
    NO_PRODUCTS_FOUND = "no_products_found"
    CAPTCHA = "captcha"
    # The product page frame arrived with zero product state inside. That is a
    # serving/session condition (degraded or challenge-gated serving, or a
    # delisted product), not a parser verdict — see _empty_product_shell.
    EMPTY_PRODUCT_SHELL = "empty_product_shell"


def looks_like_captcha(html: str) -> bool:
    """True when the page is an actual captcha challenge, not a normal page."""
    return any(marker in html for marker in _CAPTCHA_MARKERS)


def _iter_patches(html: str):
    for match in _PATCH_RE.finditer(html):
        try:
            yield json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a dict, else an empty dict.

    SSR fields drift between object, null and scalar between renders. Coercing to
    an empty dict lets a missing node read as absent instead of raising.
    """
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return ``value`` when it is a list, else an empty list."""
    return value if isinstance(value, list) else []


def _is_empty(value: Any) -> bool:
    return value is None or value == {} or value == [] or value == ""


def find_search_collections(html: str) -> dict[str, Any] | None:
    """Locate the collection bundle on a search page.

    Found by scoring candidates on how many products they hold rather than by
    path, because the widget's path key is generated per render.
    """
    best: dict[str, Any] | None = None
    best_score = 0

    for patch in _iter_patches(html):
        widgets = patch.get("widgets")
        if not isinstance(widgets, dict):
            continue
        for widget_value in widgets.values():
            if not isinstance(widget_value, dict):
                continue
            for path_value in widget_value.values():
                if not isinstance(path_value, dict):
                    continue
                options = path_value.get("options")
                if not isinstance(options, dict):
                    continue
                collections = options.get("collections")
                if not isinstance(collections, dict):
                    continue
                score = (
                    len(_as_dict(collections.get("productSnippet")))
                    + len(_as_dict(collections.get("offer")))
                    + len(_as_dict(collections.get("product")))
                )
                if score > best_score:
                    best_score = score
                    best = collections
    return best


def merge_card_collections(html: str) -> dict[str, Any]:
    """Merge every top-level collection patch on a product page.

    A populated entry is never overwritten by an empty one: later patches
    routinely re-declare keys as empty, and honouring them blindly silently
    discards the reviews.
    """
    merged: dict[str, Any] = {}
    for patch in _iter_patches(html):
        collections = patch.get("collections")
        if not isinstance(collections, dict):
            continue
        for name, value in collections.items():
            if not isinstance(value, dict):
                merged.setdefault(name, value)
                continue
            slot = merged.setdefault(name, {})
            if not isinstance(slot, dict):
                continue
            for entity_id, entity in value.items():
                if _is_empty(entity) and not _is_empty(slot.get(entity_id)):
                    continue
                slot[entity_id] = entity
    return merged


def _iter_ldjson(html: str):
    for match in _LDJSON_RE.finditer(html):
        try:
            yield json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue


def ldjson_product(html: str) -> dict[str, Any]:
    """schema.org ``Product`` from a card page, used as a degraded fallback.

    Carries title/brand/description/image reliably but no rating and no rating
    distribution, and its price is the discounted one.
    """
    for obj in _iter_ldjson(html):
        if obj.get("@type") != "Product":
            continue
        offers = _as_dict(obj.get("offers"))
        return {
            "title": obj.get("name"),
            "brand": obj.get("brand"),
            "image": obj.get("image"),
            "description": obj.get("description"),
            "sku": obj.get("sku"),
            "price": offers.get("price"),
            "currency": offers.get("priceCurrency"),
            "url": obj.get("url"),
        }
    return {}


def ldjson_item_list(html: str) -> list[dict[str, Any]]:
    """schema.org ``ItemList`` from a search page (first screen only)."""
    items: list[dict[str, Any]] = []
    for obj in _iter_ldjson(html):
        if obj.get("@type") != "ItemList":
            continue
        for element in obj.get("itemListElement") or []:
            item = element.get("item") if isinstance(element, dict) else None
            if not isinstance(item, dict):
                continue
            offers = _as_dict(item.get("offers"))
            rating = _as_dict(item.get("aggregateRating"))
            url = str(item.get("url") or "")
            items.append(
                {
                    "product_id": url.rstrip("/").split("/")[-1] if url else None,
                    "title": item.get("name"),
                    "url": url,
                    "image": item.get("image"),
                    "sku_id": item.get("sku"),
                    "price_with_plus": _to_number(offers.get("price")),
                    "rating": _to_number(rating.get("ratingValue")),
                    "rating_count": _to_int(rating.get("ratingCount")),
                    "source": "ld+json",
                }
            )
    return items


def _to_number(value: Any) -> float | None:
    """Coerce to a positive finite float, or None. Never 0 as a stand-in for
    missing, never a non-finite value.

    Rounded to two decimals because Yandex serialises some values as float32,
    which surfaces as ``4.900000095367432`` for a rating of 4.9 — accurate but
    absurd to hand to a reader. The isfinite guard matches the coerce_price
    doctrine: json.loads admits NaN/Infinity and float() inflates huge
    magnitudes to inf; either would corrupt a price or let ``_to_int`` crash
    on int(inf), so a poisoned cell degrades to no-data instead.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number <= 0 or not math.isfinite(number):
        return None
    return round(number, 2)


def _to_int(value: Any) -> int | None:
    number = _to_number(value)
    return int(number) if number is not None else None


def zone_snippets(html: str) -> list[dict[str, Any]]:
    """Raw ``data-zone-data`` payloads of the SERP's productSnippet zones.

    Document order, which is the on-screen order (each payload also carries a
    ``pos``). The attribute is HTML-escaped JSON (``&quot;``), unescaped here
    and nowhere else; a snippet without a parseable payload is skipped, never
    guessed at. Only ``type: offer`` payloads count — the same zone name is
    reused for non-product tiles on some pages.
    """
    out: list[dict[str, Any]] = []
    for tag_match in _ZONE_SNIPPET_TAG_RE.finditer(html):
        attr = _ZONE_DATA_ATTR_RE.search(tag_match.group(0))
        if attr is None:
            continue
        try:
            payload = json.loads(_html.unescape(attr.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("type") == "offer":
            out.append(payload)
    return out


def _zone_shop_name(payload: dict[str, Any]) -> str:
    """Shop title out of the payload's ``signals`` — shipped on some rows only."""
    for signal in _as_list(payload.get("signals")):
        if isinstance(signal, dict) and signal.get("type") == "shop" and signal.get("title"):
            return str(signal["title"])
    return ""


def parse_zone_items(html: str) -> list[dict[str, Any]]:
    """Search rows from the first-screen snippet payloads (no collections needed).

    Price semantics are the ones verified live 2026-09-11 for the identical
    ``baobabPayload`` fields and re-read on the 2026-09-13 anonymous capture:

    * ``additionalPrices[withDiscount]`` is the everyday price anyone pays
      (it equalled the cart price and the card's ``prices.price`` row by row),
      so it is ``price_rub``; on undiscounted rows the entry is absent and the
      base ``price`` IS the everyday price.
    * ``additionalPrices[yaBank]`` is the green Yandex Plus price — the figure
      the SERP prints big and the only ₽ amount visible in the snippet DOM.
      It is ``price_with_plus`` and is never allowed into ``price_rub``.
    * ``price`` is the base/initial price; on discounted rows it is the
      struck-through figure (Realme Note 60x: 12450 struck, 11329 everyday,
      11102 Plus, badge «Скидка 11%») and rides in ``price_old_rub``.

    ``product_id`` is ``oskuId`` — the id the row's own ``/card/…/{id}`` link
    and schema.org ``url`` end in and that ``/product/{id}`` accepts;
    ``sku_id`` is ``marketSku``. Brand cannot be resolved (only ``vendorId``
    ships); seller comes from the ``shop`` signal when present. URL and image
    are filled from the page's schema.org ``ItemList`` by product id, else the
    canonical ``/product/{id}`` URL is built.
    """
    payloads = zone_snippets(html)
    if not payloads:
        return []

    ldjson_by_id = {item["product_id"]: item for item in ldjson_item_list(html) if item.get("product_id")}

    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for payload in payloads:
        product_id = str(payload.get("oskuId") or payload.get("modelId") or "")
        key = (product_id, str(payload.get("wareId") or ""))
        if key in seen:
            continue
        seen.add(key)

        base_price = _to_number(payload.get("price"))
        with_discount = _additional_price(payload, "withDiscount")
        price_rub = with_discount if with_discount is not None else base_price
        price_old = (
            base_price if (base_price is not None and price_rub is not None and base_price > price_rub) else None
        )

        rating_node = _as_dict(payload.get("rating"))
        is_available = payload.get("isAvailable")
        ld = ldjson_by_id.get(product_id, {})

        items.append(
            {
                "product_id": product_id or None,
                "sku_id": str(payload.get("marketSku") or ""),
                "title": str(payload.get("title") or ld.get("title") or ""),
                "brand": "",
                "seller": _zone_shop_name(payload),
                "price_rub": price_rub,
                "price_with_plus": _additional_price(payload, "yaBank"),
                "price_old_rub": price_old,
                "currency": "RUR",
                "rating": _to_number(rating_node.get("rating")) or ld.get("rating"),
                "rating_count": _to_int(rating_node.get("gradesCount")) or ld.get("rating_count"),
                "in_stock": is_available if isinstance(is_available, bool) else None,
                "is_express": bool(payload.get("isExpress")),
                "url": str(ld.get("url") or "") or _absolute_url(None, product_id or None),
                "image": str(ld.get("image") or ""),
                "source": "zone",
            }
        )
    return items


def _amount_int(node: Any) -> float | None:
    """Read a price out of the presentational ``amount.intPart`` shape.

    This is the most fragile path in the file — prices arrive as display strings
    with non-breaking spaces — which is why the structural ``offer.price.value``
    is always preferred when available.
    """
    if not isinstance(node, dict):
        return None
    amount = node.get("amount")
    if not isinstance(amount, dict):
        return None
    return _to_number(amount.get("intPart"))


def _cart_price(cart_button: Any) -> float | None:
    """The price the snippet's cart button would actually charge.

    ``productPayload.cartButton.price`` carries the everyday price the SERP
    displays big: ``valueFmt`` in roubles, ``value`` in 1e-7 units. Verified live
    2026-09-11 row by row against the product card for the same offer (Tuvio
    TKP2117S: cart 2293 == card ``prices.price``) and against the baobab's
    ``withDiscount`` additional price, which it equalled on every row. This is
    the number any buyer pays, subscription or not.
    """
    if not isinstance(cart_button, dict):
        return None
    price = cart_button.get("price")
    if not isinstance(price, dict):
        return None
    return _to_number(price.get("valueFmt"))


def _additional_price(baobab: Any, price_type: str) -> float | None:
    """One named price out of ``baobabPayload.additionalPrices``.

    The SERP snippet ships its price breakdown there: ``withDiscount`` is the
    discounted everyday price any buyer pays, ``yaBank`` is the green Yandex
    Plus price. Both verified live 2026-09-11 to equal the product card's
    ``prices.price`` / ``greenPrice`` for the same offer.
    """
    if not isinstance(baobab, dict):
        return None
    for entry in _as_list(baobab.get("additionalPrices")):
        if isinstance(entry, dict) and entry.get("priceType") == price_type:
            return _to_number(entry.get("priceValue"))
    return None


def _first_dict(collection: Any) -> dict[str, Any]:
    """First dict value in a collection, or an empty dict.

    Card collections are keyed by ids the caller has no way to know, and each
    holds exactly one meaningful entry per page.
    """
    if isinstance(collection, dict):
        for value in collection.values():
            if isinstance(value, dict):
                return value
    return {}


# The pageParams.pageId Yandex sets when it believes it is rendering a product
# page. Present even on a hollow frame, which is what makes the shell verdict
# more than "we found nothing": Yandex itself claims this is a product render.
_PRODUCT_PAGE_ID = "market:product"

# Collections a real product card populates at least one of. Every one of them
# empty while the page claims to be a product render is the hollow-frame
# signature (live capture 2026-09-13, /product/4315891968).
_CARD_PRODUCT_COLLECTIONS = (
    "allPrices",
    "businessRatingStats",
    "mediaItem",
    "offer",
    "price",
    "productServiceSnippets",
    "reviews",
    "shopInfo",
    "titleV2",
)


def _empty_product_shell(merged: dict[str, Any], fallback: dict[str, Any]) -> bool:
    """True when the page is a product frame carrying no product at all.

    Live-verified 2026-09-13 from a residential RU IP: ``/product/4315891968``
    — a product alive enough to be the first hit of its own search — answered
    HTTP 200 with ``pageParams {pageId: "market:product", productId:
    "4315891968"}`` while every product-bearing collection was empty and no
    schema.org Product was embedded. The same URL in a real browser hit
    SmartCaptcha. The known field families did not change shape; they are
    absent. Under the tri-state doctrine that is degraded serving
    (inconclusive), never parser drift — and without a 404 or a not-found
    marker it is never not_found either, because the product may well exist.

    Captcha pages never reach this check — ``parse_card`` short-circuits them.
    ``fallback`` is the ld+json Product dict; a page that still embeds one is
    not hollow (the degraded card path fills title and price from it).
    """
    params = _first_dict(merged.get("pageParams"))
    if params.get("pageId") != _PRODUCT_PAGE_ID:
        return False
    if fallback:
        return False
    return all(not _as_dict(merged.get(name)) for name in _CARD_PRODUCT_COLLECTIONS)


def _shell_product_id(merged: dict[str, Any]) -> str:
    """The productId echoed by pageParams on a product frame, if present."""
    params = _as_dict(_first_dict(merged.get("pageParams")).get("params"))
    product_id = params.get("productId")
    return str(product_id) if product_id else ""


def _picture_url(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    base = node.get("baseUrl")
    if isinstance(base, str) and base:
        return base.rstrip("/") + _ORIG_SUFFIX
    namespace, group_id, name = node.get("namespace"), node.get("groupId"), node.get("imageName")
    if namespace and group_id and name:
        return f"https://avatars.mds.yandex.net/get-{namespace}/{group_id}/{name}{_ORIG_SUFFIX}"
    return ""


def _absolute_url(raw: Any, product_id: Any = None, slug: Any = None) -> str:
    if isinstance(raw, str) and raw:
        if raw.startswith("http"):
            return raw
        if raw.startswith("//"):
            return f"https:{raw}"
        return f"https://market.yandex.ru{raw}"
    if slug and product_id:
        return f"https://market.yandex.ru/product--{slug}/{product_id}"
    if product_id:
        return f"https://market.yandex.ru/product/{product_id}"
    return ""


def parse_search(html: str) -> dict[str, Any]:
    """Parse a search results page into products plus result metadata.

    Two prices are reported per item and the distinction is not cosmetic:
    ``price_rub`` is what anyone pays, while ``price_with_plus`` requires a
    Yandex Plus subscription and runs 25–30% lower. Reporting only the latter
    would quote a price most callers cannot get.

    ``price_rub`` comes from the snippet's cart price
    (``productPayload.cartButton.price``, mirrored by the baobab's
    ``withDiscount`` additional price) — never from ``offer.price.value`` or
    ``baobabPayload.price`` alone: on discounted rows both of those carry the
    STRUCK-THROUGH base price. Verified live 2026-09-11 against the product
    card for the same offer (Tuvio TKP2117S: SERP cart price 2293 == card
    ``prices.price``, while ``offer.price.value`` and the baobab base price
    both carry the struck-through ``initialPrice`` 3698). The struck-through
    figure is surfaced separately as ``price_old_rub``.

    When the page carries no collection bundle at all (anonymous serving since
    2026-09-12), the rows come from the first-screen snippet payloads instead
    (``parse_zone_items``, status ``ok_zone``) with the same price semantics;
    only when those are missing too does the Plus-only schema.org list stand
    in (``ok_ldjson_only``).

    A search row describes the SERP snippet's offer, which is not necessarily
    the offer a card for the same product id defaults to: one id covers a
    product family, and Yandex may show one member in search (REDMOND KM243,
    sku 4668084807) while the card defaults to another (KM245). Rows are
    SERP-faithful; reconcile them with cards by ``sku_id``, not by product
    URL.
    """
    if looks_like_captcha(html):
        return {"status": ParseStatus.CAPTCHA, "items": [], "total": None}

    collections = find_search_collections(html)
    source = collections if collections is not None else merge_card_collections(html)
    visible = _first_dict(source.get("visibleSearchResult"))

    result: dict[str, Any] = {
        "status": ParseStatus.OK,
        "query": visible.get("text") or "",
        "total": _to_int(visible.get("total")),
        "page": _to_int(visible.get("page")),
        "page_count": _to_int(visible.get("pageCount")),
        "has_next_page": bool(visible.get("hasNextPage")),
        "items": [],
    }

    if not collections:
        # No collection bundle (the post-2026-09-12 anonymous page): the first
        # screen of snippets still ships its data-zone-data payloads, which
        # carry both prices — read those before the Plus-only schema.org list.
        zone_items = parse_zone_items(html)
        if zone_items:
            result["items"] = zone_items
            result["status"] = ParseStatus.OK_ZONE
            return result
        fallback = ldjson_item_list(html)
        if fallback:
            result["items"] = fallback
            result["status"] = ParseStatus.OK_LDJSON_ONLY
            return result
        result["status"] = ParseStatus.EMPTY if EMPTY_RESULT_MARKER in html else ParseStatus.NO_PRODUCTS_FOUND
        return result

    offers = _as_dict(collections.get("offer"))
    products = _as_dict(collections.get("product"))
    snippets = _as_dict(collections.get("productSnippet"))
    shops = _as_dict(collections.get("shop"))
    vendors = _as_dict(collections.get("vendor"))
    show_places = _as_dict(collections.get("productShowPlace"))
    offer_places = _as_dict(collections.get("offerShowPlace"))
    visible_entities = _as_dict(collections.get("visibleEntity"))
    search_results = _as_dict(collections.get("searchResult"))

    # Follow the page's own ordering when it is available, so results come back
    # in the order a human would see them.
    ordered_ids = [
        entity_id
        for search_result in search_results.values()
        if isinstance(search_result, dict)
        for entity_id in (search_result.get("visibleEntityIds") or [])
        if isinstance(entity_id, str) and entity_id.startswith("showPlace_")
    ]
    if ordered_ids:
        places = [
            show_places[visible_entities[entity_id]["productShowPlaceId"]]
            for entity_id in ordered_ids
            if isinstance(visible_entities.get(entity_id), dict)
            and visible_entities[entity_id].get("productShowPlaceId") in show_places
        ]
    else:
        places = [place for place in show_places.values() if isinstance(place, dict)]

    seen: set[Any] = set()
    for place in places:
        if not isinstance(place, dict):
            continue
        product_id = place.get("productId")
        snippet_id = place.get("productSnippetId")
        # Dedupe by snippet: one product can appear several times as different
        # offers, and the snippet is the on-screen position.
        key = snippet_id or (product_id, place.get("id"))
        if key in seen:
            continue
        seen.add(key)

        snippet = snippets.get(snippet_id) if snippet_id else None
        payload = snippet.get("productPayload") if isinstance(snippet, dict) else None
        payload = payload if isinstance(payload, dict) else {}
        baobab = snippet.get("baobabPayload") if isinstance(snippet, dict) else None
        baobab = baobab if isinstance(baobab, dict) else {}

        offer: dict[str, Any] = {}
        offer_place_id = place.get("defaultOfferShowPlaceId")
        if offer_place_id and isinstance(offer_places.get(offer_place_id), dict):
            offer_candidate = offers.get(offer_places[offer_place_id].get("offerId"))
            offer = offer_candidate if isinstance(offer_candidate, dict) else {}
        if not offer and isinstance(snippet_id, str) and snippet_id.startswith("prime-"):
            offer_candidate = offers.get(snippet_id[len("prime-") :])
            offer = offer_candidate if isinstance(offer_candidate, dict) else {}

        product = products.get(str(product_id)) if product_id is not None else None
        product = product if isinstance(product, dict) else {}

        price_node = _as_dict(payload.get("price"))
        # The everyday price, in decreasing order of trust: what the snippet's
        # cart button charges (== the figure displayed big on the page), the
        # baobab's withDiscount price, and only then offer.price.value. The
        # latter two structural fields both carry the STRUCK-THROUGH base price
        # on discounted rows — quoting offer.price.value as the everyday price
        # was the pre-fix bug (Tuvio TKP2117S: 3698 instead of 2293, live
        # check 2026-09-11).
        cart_price = _cart_price(payload.get("cartButton"))
        with_discount = _additional_price(baobab, "withDiscount")
        offer_price = _to_number((_as_dict(offer.get("price"))).get("value"))
        price_rub = next((price for price in (cart_price, with_discount, offer_price) if price is not None), None)

        price_with_plus = _amount_int(price_node.get("actualPrice"))
        if price_with_plus is None:
            price_with_plus = _additional_price(baobab, "yaBank")

        # Struck-through reference: the display node's own old/initial price
        # when present, else the baobab base price — but only while it actually
        # sits above the everyday price. On undiscounted rows the baobab price
        # IS the everyday price and nothing is struck through.
        price_old = _amount_int(price_node.get("oldPrice"))
        if price_old is None:
            price_old = _amount_int(price_node.get("initialPrice"))
        if price_old is None:
            baobab_price = _to_number(baobab.get("price"))
            if baobab_price is not None and price_rub is not None and baobab_price > price_rub:
                price_old = baobab_price

        rating_node = _as_dict(payload.get("rating"))
        delivery = _as_dict(offer.get("delivery"))

        vendor_id = offer.get("vendorId")
        supplier_id = offer.get("supplierId")
        vendor = vendors.get(str(vendor_id)) if vendor_id is not None else None
        shop = shops.get(str(supplier_id)) if supplier_id is not None else None

        titles = _as_dict(product.get("titles"))
        offer_titles = _as_dict(offer.get("titles"))
        title_node = _as_dict(payload.get("title"))
        gallery = _as_dict(payload.get("gallery"))
        media = _as_list(gallery.get("mediaItems"))

        result["items"].append(
            {
                "product_id": str(product_id) if product_id is not None else None,
                "sku_id": offer.get("skuId"),
                "title": title_node.get("value") or titles.get("raw") or offer_titles.get("raw") or "",
                "brand": (vendor or {}).get("name") or "",
                "seller": (shop or {}).get("name") or "",
                # Cart price first: it is what the page displays and charges;
                # offer.price.value is structural but carries the strike-through
                # on discounted rows, so it is only a last-resort fallback.
                "price_rub": price_rub,
                "price_with_plus": price_with_plus,
                "price_old_rub": price_old,
                "currency": (_as_dict(offer.get("price"))).get("currency") or "RUR",
                "rating": _to_number(rating_node.get("ratingValue")),
                "rating_count": _to_int(rating_node.get("ratingCount")),
                "in_stock": delivery.get("inStock") if isinstance(delivery.get("inStock"), bool) else None,
                "is_express": bool(delivery.get("isExpress")),
                "url": _absolute_url((_as_dict(place.get("urls"))).get("direct"), product_id, product.get("slug")),
                "image": _picture_url((media[0] or {}).get("picture") if media else None),
                "source": "ssr",
            }
        )

    if not result["items"]:
        result["status"] = ParseStatus.EMPTY if EMPTY_RESULT_MARKER in html else ParseStatus.NO_PRODUCTS_FOUND
    return result


def _rating_from_snippets(merged: dict[str, Any]) -> dict[str, Any]:
    snippet = _first_dict(merged.get("productServiceSnippets"))
    product_snippet = _as_dict(snippet.get("productSnippet"))
    payload = _as_dict(product_snippet.get("productPayload"))
    rating = _as_dict(payload.get("rating"))
    return rating


def _stars_from_stats(stats: Any) -> dict[int, int]:
    if not isinstance(stats, dict):
        return {}
    out: dict[int, int] = {}
    for star in range(1, 6):
        value = _to_int(stats.get(f"cnt{star}"))
        out[star] = value if value is not None else 0
    return out


def _stars_from_distribution(distribution: Any) -> dict[int, int]:
    if not isinstance(distribution, dict):
        return {}
    out: dict[int, int] = {}
    for part in distribution.get("parts") or []:
        if not isinstance(part, dict):
            continue
        star = _to_int(part.get("value"))
        count = _to_int(part.get("number"))
        if star is not None and 1 <= star <= 5:
            out[star] = count if count is not None else 0
    return out


def parse_reviews(merged: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the reviews embedded in a product page's state.

    Only the first ~13 reviews are server-rendered; the dedicated ``/reviews``
    URL renders none at all (they load over XHR), so the card is the only usable
    source without the closed API.
    """
    reviews_collection = _as_dict(merged.get("reviews"))
    raw_reviews = reviews_collection.get("reviews")
    if not isinstance(raw_reviews, list):
        return []

    out: list[dict[str, Any]] = []
    for raw in raw_reviews:
        if not isinstance(raw, dict):
            continue
        date = ""
        for descriptor in raw.get("descriptor") or []:
            if isinstance(descriptor, dict) and descriptor.get("type") == "text" and descriptor.get("content"):
                date = str(descriptor["content"])
                break
        votes = _as_dict(raw.get("votes"))
        author = _as_dict(raw.get("author"))
        photos = [
            url
            for item in (raw.get("media") or [])
            if isinstance(item, dict) and (url := _picture_url(item.get("picture")))
        ]
        out.append(
            {
                "author": str(author.get("nickname") or ""),
                "rating": _to_int(raw.get("rating")),
                "date": date,
                "pros": str(raw.get("pro") or ""),
                "cons": str(raw.get("contra") or ""),
                "comment": str(raw.get("comment") or ""),
                "votes_up": _to_int(votes.get("votesAgree")) or 0,
                "votes_down": _to_int(votes.get("votesReject")) or 0,
                "photos": photos,
            }
        )
    return out


def parse_card(html: str) -> dict[str, Any]:
    """Parse a product page into a card, its rating breakdown and its reviews.

    Three prices are surfaced because Yandex shows three: the subscriber price
    (``price_with_plus``), the current everyday price (``price_rub``), and the
    pre-discount reference (``price_before_discount_rub``).
    """
    if looks_like_captcha(html):
        return {"status": ParseStatus.CAPTCHA, "reviews": []}

    merged = merge_card_collections(html)
    fallback = ldjson_product(html)

    all_prices = _first_dict(merged.get("allPrices"))
    baobab = _as_dict(all_prices.get("baobab"))
    title_node = _first_dict(merged.get("titleV2"))
    vendor_node = _as_dict(title_node.get("vendor"))

    product_id = baobab.get("productId")
    if not product_id and fallback.get("url"):
        match = re.search(r"/(\d+)(?:\?|$)", str(fallback["url"]))
        if match:
            product_id = match.group(1)

    price_node = _first_dict(merged.get("price"))
    main_price = _as_dict(price_node.get("mainPrice"))
    price_with_plus = _to_number((_as_dict(main_price.get("price"))).get("value")) or _to_number(fallback.get("price"))

    price_regular = price_before_discount = None
    for old_price in price_node.get("oldPrices") or []:
        if not isinstance(old_price, dict):
            continue
        value = _to_number((_as_dict(old_price.get("price"))).get("value"))
        if old_price.get("type") == "regular":
            price_regular = value
        elif old_price.get("type") == "withoutDiscount":
            price_before_discount = value

    rating_node = _rating_from_snippets(merged)
    reviews_collection = _as_dict(merged.get("reviews"))
    rating_stats = _as_dict(reviews_collection.get("ratingStats"))
    review_stats = _as_dict(reviews_collection.get("reviewStats"))
    business_stats = _first_dict(merged.get("businessRatingStats"))
    shop_info = _first_dict(merged.get("shopInfo"))

    rating = _to_number(rating_node.get("ratingValue"))
    if rating is None:
        rating = _to_number(rating_stats.get("ratingValue")) or _to_number(rating_stats.get("roundedRating"))
    if rating is None:
        rating = _to_number(business_stats.get("ratingValue"))

    rating_count = _to_int(rating_node.get("ratingCount")) or _to_int(rating_stats.get("ratingCount"))
    if rating_count is None:
        rating_count = _to_int(business_stats.get("ratingCount"))

    stars = _stars_from_stats(rating_node.get("ratingCountStats"))
    if not stars:
        stars = _stars_from_distribution(reviews_collection.get("distribution"))

    result = {
        "status": ParseStatus.OK,
        "product_id": str(product_id) if product_id else "",
        "sku_id": str(baobab.get("skuId") or fallback.get("sku") or ""),
        "title": str(title_node.get("title") or fallback.get("title") or ""),
        "brand": str(vendor_node.get("name") or fallback.get("brand") or ""),
        "description": str(fallback.get("description") or ""),
        "image": str(fallback.get("image") or ""),
        "seller": str(shop_info.get("shopName") or shop_info.get("name") or business_stats.get("shopName") or ""),
        "price_rub": price_regular if price_regular is not None else price_with_plus,
        "price_with_plus": price_with_plus,
        "price_before_discount_rub": price_before_discount,
        "discount_percent": _to_int(price_node.get("discountPercent")),
        "currency": (_as_dict(main_price.get("price"))).get("currency") or fallback.get("currency") or "RUR",
        "offers_count": _to_int(baobab.get("numOffers")),
        "rating": rating,
        "rating_count": rating_count,
        "review_count": _to_int(rating_node.get("reviewsCount")) or _to_int(review_stats.get("reviewsCount")),
        "rating_stars": stars,
        "reviews": parse_reviews(merged),
    }
    if (
        not result["title"]
        and result["price_rub"] is None
        and result["price_with_plus"] is None
        and _empty_product_shell(merged, fallback)
    ):
        # A hollow frame, not a card: report it as itself so the caller can
        # classify it as degraded serving (inconclusive) instead of blaming
        # the parsers (drift). pageParams still echoes the requested id.
        result["status"] = ParseStatus.EMPTY_PRODUCT_SHELL
        result["product_id"] = result["product_id"] or _shell_product_id(merged)
    return result
