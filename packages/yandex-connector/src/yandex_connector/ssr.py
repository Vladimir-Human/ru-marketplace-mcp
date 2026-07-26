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
results, pagination, A/B duplicates).
"""

from __future__ import annotations

import json
import re
from typing import Any

# One state fragment. Yandex emits ~150 of these per search page, ~200 per card.
_PATCH_RE = re.compile(r'<noframes data-apiary="patch">(.*?)</noframes>', re.S)

_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)

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
    OK_LDJSON_ONLY = "ok_ldjson_only"
    EMPTY = "empty"
    NO_PRODUCTS_FOUND = "no_products_found"
    CAPTCHA = "captcha"


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
    """Coerce to a positive float, or None. Never 0 as a stand-in for missing.

    Rounded to two decimals because Yandex serialises some values as float32,
    which surfaces as ``4.900000095367432`` for a rating of 4.9 — accurate but
    absurd to hand to a reader.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number > 0 else None


def _to_int(value: Any) -> int | None:
    number = _to_number(value)
    return int(number) if number is not None else None


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
        price_with_plus = _amount_int(price_node.get("actualPrice"))
        price_snippet_base = _amount_int(price_node.get("initialPrice"))
        price_old = _amount_int(price_node.get("oldPrice"))
        offer_price = _to_number((_as_dict(offer.get("price"))).get("value"))

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
                # Base price first: offer.price is structural, the snippet's is display text.
                "price_rub": offer_price if offer_price is not None else price_snippet_base,
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

    return {
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
