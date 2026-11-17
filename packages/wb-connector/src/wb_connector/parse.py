"""Pure Wildberries parsing helpers: product flattening, prices, ids, menu nodes and basket hosts.

Moved verbatim out of server.py (move-only); server.py re-imports every name, so
``server._card_item_dict`` and the other helpers keep working for callers and tests.
"""
# START_MODULE_CONTRACT
# PURPOSE: Pure parsing and routing helpers for the Wildberries tools (no network).
# SCOPE: Basket host for an nm id, product flattening and prices, response shape checks, review dates, search ids, menu nodes.
# DEPENDS: mcp_core.resilience, mcp_core.errors, mcp_core.logging; models_output; settings.
# LINKS: M-WB; packages/wb-connector/tests.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _basket_for_sku: Basket CDN host for an nm id.
# _card_item_dict: Flatten one WB product into the shared card-item shape.
# _extract_price_rub: Separate current and basic prices, converting kopecks to rubles.
# _recover_search_ids: Numeric product ids from a search-goods response.
# _menu_node/_find_menu_subtree: Bounded category nodes and breadth-first lookup.
# END_MODULE_MAP

from __future__ import annotations

import datetime
import math
import string
from typing import Any

from mcp_core import resilience as R
from mcp_core.errors import ParserDriftError, raise_tool_error
from mcp_core.logging import log_event

from wb_connector.models_output import WbCategoryNode
from wb_connector.settings import get_settings


def _basket_for_sku(nm_id: int) -> str:
    """Compute basket CDN host. Probe range up to 28 for new SKUs."""
    vol = nm_id // 100000
    table = [
        (143, "01"),
        (287, "02"),
        (431, "03"),
        (719, "04"),
        (1007, "05"),
        (1061, "06"),
        (1115, "07"),
        (1169, "08"),
        (1313, "09"),
        (1601, "10"),
        (1655, "11"),
        (1919, "12"),
        (2045, "13"),
        (2189, "14"),
        (2405, "15"),
        (2621, "16"),
        (2837, "17"),
        (3053, "18"),
        (3269, "19"),
        (3485, "20"),
        (3701, "21"),
        (3917, "22"),
        (4133, "23"),
        (4349, "24"),
        (4565, "25"),
        (4781, "26"),
        (4997, "27"),
    ]
    for upper, nn in table:
        if vol <= upper:
            return f"basket-{nn}.wbbasket.ru"
    log_event("wb.basket_fallback_used", vol=vol, fallback=get_settings().basket_fallback)
    return f"{get_settings().basket_fallback}.wbbasket.ru"


def _decode_mojibake(s: object) -> str:
    """Fix WB double-encoded UTF-8. Best-effort: keeps correct strings alone.

    Accepts ANY type and returns a str: a drifted/null field (text=None, or a
    rich-text dict) must not crash the downstream [:1500] slice — this helper is
    the single chokepoint every WB text field flows through (audit wave-2 DRIFT:
    text/pros/cons=None raised 'NoneType not subscriptable')."""
    if not isinstance(s, str):
        return ""
    if not s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        cyr_orig = sum("\u0400" <= c <= "\u04ff" for c in s)
        cyr_fixed = sum("\u0400" <= c <= "\u04ff" for c in fixed)
        return fixed if cyr_fixed > cyr_orig else s
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _single_product_color(value: Any) -> str:
    """Keep only unambiguous typed color evidence, never infer it from a title."""
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return ""
    name = value[0].get("name")
    return _decode_mojibake(name).strip() if isinstance(name, str) else ""


def _card_item_dict(p: dict[str, Any]) -> dict[str, Any]:
    """Flatten one WB product object into the shared card-item shape.

    Used by wb_card, wb_search and wb_category_products so a product looks the
    same however it was found — a category walk and a text search stay directly
    comparable. Price extraction and the in_stock rule live in one place here,
    which matters because "in stock" means *both* a positive quantity and a real
    price: a quantity with no price is an unsellable listing, and calling it
    available would rank a dead item as the cheapest option.
    """
    cur_rub, orig_rub = _extract_price_rub(p)
    qty = R.coerce_int(p.get("totalQuantity"))
    return {
        "nm_id": p.get("id"),
        "name": _decode_mojibake(p.get("name", "")),
        "brand": _decode_mojibake(p.get("brand", "")),
        "color": _single_product_color(p.get("colors")),
        "supplier": _decode_mojibake(p.get("supplier", "")),
        "supplier_id": p.get("supplierId"),
        "supplier_rating": p.get("supplierRating"),
        "review_rating": p.get("reviewRating"),
        "feedbacks": p.get("feedbacks"),
        "total_quantity": qty,
        "in_stock": qty is not None and qty > 0 and cur_rub is not None,
        "price_rub": cur_rub,
        "price_original_rub": orig_rub,
    }


def _normalise_ws(value: object) -> str:
    """Collapse runs of whitespace into single spaces.

    Seller answers to buyer questions arrive with literal newlines and tabs in
    them (verified live Jul 2026). Left alone, a multi-line answer breaks any
    single-line rendering an agent produces, and a naive slice can end mid-escape.
    Accepts any type and always returns a str, matching ``_decode_mojibake``.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _extract_price_rub(p: dict) -> tuple[float | None, float | None]:
    """Return (current_rub, original_rub), or (None, None) if no live offer.

    WB v4 (pre-v1.0.0 (July 2026)): top-level priceU/salePriceU are null. Prices live in
    sizes[0].price.{product, basic} as integer kopecks.

    None (not 0.0) is returned when the SKU has no sellable price — verified
    pre-v1.0.0 (July 2026) that this coincides with totalQuantity==0 (out of stock). A 0.0
    sentinel would falsely rank a dead listing as the cheapest item, corrupting
    any "find the best/cheapest" comparison.
    """
    if not isinstance(p, dict):
        return (None, None)  # defensive: a non-dict product never has a price
    sizes = p.get("sizes") or []
    # A null / non-dict size entry must NOT crash the whole tool (audit
    # 2026-06-01 CONFIRMED: sizes:[None] raised AttributeError before attach_meta
    # could emit a drift warning). Find the first dict size with a dict price.
    if isinstance(sizes, list):
        for sz in sizes:
            if not isinstance(sz, dict):
                continue
            price = sz.get("price")
            if not isinstance(price, dict):
                continue
            # coerce so a number->string price drift ('12300') doesn't crash the
            # `/100` math (audit wave-2 round-2 DRIFT). coerce_price already
            # rejects <=0/junk and returns None.
            product = _kopeck_to_rub(price.get("product") or price.get("total"))
            basic = _kopeck_to_rub(price.get("basic")) or product
            if product:
                return (product, basic or product)
    sale = _kopeck_to_rub(p.get("salePriceU"))
    base = _kopeck_to_rub(p.get("priceU"))
    if sale or base:
        return ((sale or base), (base or sale))
    return (None, None)


def _kopeck_to_rub(v: object) -> float | None:
    """WB integer kopecks -> float rubles, tolerant of a number->string drift.
    Returns None for null/<=0/non-finite/unparseable (never crashes the /100
    math). json.loads admits Infinity by default and a huge int makes the
    division raise OverflowError, so a poisoned priceU cell degrades to
    no-price instead of corrupting a ranking or aborting the tool."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            rubles = v / 100
        except OverflowError:
            return None
        return rubles if rubles > 0 and math.isfinite(rubles) else None
    if isinstance(v, str):
        stripped = v.strip()
        if not stripped or any(c not in string.digits for c in stripped):
            return None
        try:
            amount = int(stripped)
        except ValueError:
            return None
        try:
            rubles = amount / 100
        except OverflowError:
            return None
        return rubles if rubles > 0 and math.isfinite(rubles) else None
    return None


def _expect_json_object(value: Any, label: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(obj, None)`` for a JSON object, else ``(None, error_dict)``.

    Exactly one element is ever non-None. Callers that raise on the error branch
    should pass the result through ``_require_object`` afterwards so the
    invariant is visible to type checkers instead of merely implied.
    """
    if isinstance(value, dict):
        return value, None
    return None, {
        "status": "error",
        "type": "parse",
        "message": f"{label} expected object, got {type(value).__name__}",
    }


def _require_object(obj: dict[str, Any] | None, label: str) -> dict[str, Any]:
    """Assert the non-error branch of ``_expect_json_object``.

    Reached only when the caller already handled the error tuple, so a None here
    means the two branches drifted apart in code — a bug, surfaced loudly as
    parser drift rather than an AttributeError three frames later.
    """
    if obj is None:
        raise_tool_error(ParserDriftError(f"{label}: expected an object", provider="wb"))
    return obj


def _card_products_checked(data: dict[str, Any]) -> tuple[list[Any], str | None]:
    if "products" in data:
        products = data.get("products")
        return (products, None) if isinstance(products, list) else ([], "products_not_list")
    nested = data.get("data")
    if isinstance(nested, dict):
        if "products" not in nested:
            return [], "products_missing"
        products = nested.get("products")
        return (products, None) if isinstance(products, list) else ([], "products_not_list")
    if "data" in data:
        return [], "data_not_object"
    return [], "products_missing"


def _card_products(data: dict[str, Any]) -> list[Any]:
    products, _error = _card_products_checked(data)
    return products


def _aggregate_offer_warnings(items: list[dict]) -> list[str]:
    """Roll per-item validation into connector-level warnings (systemic drift only)."""
    if not items:
        return ["no_items: zero parseable products returned"]
    from collections import Counter

    tally: Counter = Counter()
    for it in items:
        for w in R.validate_offer(it, require_title=True):
            tally[w.split(":")[0]] += 1
        # in_stock is a plain bool for wire-compatibility with 1.1.0, so a
        # missing totalQuantity collapses to False — indistinguishable from a
        # genuine sell-out. Count those separately: the caller can then read
        # total_quantity is None instead of trusting in_stock=False.
        if it.get("total_quantity") is None:
            tally["stock_unknown"] += 1
    n = len(items)
    # Threshold: half the items (systemic drift), but ALWAYS warn when a single
    # item is affected in a 1-item result — wb_card([sku]) / a 1-hit search must
    # not silently drop a real price_zero/title_missing warning (audit 2026-06-01
    # CONFIRMED: max(2, n//2) needed 2 hits so n==1 drift was suppressed).
    threshold = max(1, n // 2)
    return [
        f"{code}: {hits}/{n} items affected (likely schema drift)" for code, hits in tally.items() if hits >= threshold
    ]


def _wb_review_date_ts(created: object) -> float:
    """Parse WB ISO createdDate to a unix ts for tiebreak sorting (0 if absent
    or non-string — a drifted numeric/None date must not crash the sort key,
    audit wave-2)."""
    if not isinstance(created, str) or not created:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _recover_search_ids(ids: Any) -> list[int]:
    """PURE: recover numeric product ids from a search-goods response (no network).

    Factored from wb_search STEP 2 (audit 2026-06-01) so wb_selfcheck can assert
    the id-list shape offline. Handles the live shapes the wb_search drift-guard
    already covers: bare ints, numeric strings, and id-objects ({id|nmId|nm_id}).
    """
    out: list[int] = []
    if not isinstance(ids, list):
        return out

    def _as_int(s: str) -> int | None:
        # str.isdigit() is True for non-decimal Unicode digits (e.g. '²') that
        # int() rejects — guard with int() in a try so a hostile id string can
        # never crash the canary (audit 2026-06-01 CRASH_HANG).
        s = s.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    for i in ids:
        if isinstance(i, bool):
            continue
        if isinstance(i, int):
            if i > 0:
                out.append(i)
        elif isinstance(i, str):
            v = _as_int(i)
            if v is not None and v > 0:
                out.append(v)
        elif isinstance(i, dict):
            cand = R.first_present(i, "id", "nmId", "nm_id")
            if isinstance(cand, bool):
                continue
            if isinstance(cand, int):
                if cand > 0:
                    out.append(cand)
            elif isinstance(cand, str):
                v = _as_int(cand)
                if v is not None and v > 0:
                    out.append(v)
    return out


def _menu_node(raw: Any, depth: int, budget: list[int], max_depth: int) -> WbCategoryNode | None:
    """Convert one raw menu entry into a bounded WbCategoryNode.

    ``budget`` is a single-element list used as a mutable counter: the whole
    traversal shares one node allowance so a wide subtree cannot blow the
    response size. Returns None once the budget is exhausted.
    """
    if budget[0] <= 0 or not isinstance(raw, dict):
        return None
    budget[0] -= 1

    raw_children = raw.get("childs")
    children_list = raw_children if isinstance(raw_children, list) else []

    children: list[WbCategoryNode] = []
    if depth < max_depth:
        for child in children_list:
            node = _menu_node(child, depth + 1, budget, max_depth)
            if node is None:
                break
            children.append(node)

    return WbCategoryNode(
        id=R.coerce_int(R.first_present(raw, "id", default=None)),
        name=_decode_mojibake(R.first_present(raw, "name", default="")),
        url=str(R.first_present(raw, "url", default="") or ""),
        shard=str(R.first_present(raw, "shard", default="") or ""),
        query=str(R.first_present(raw, "query", default="") or ""),
        depth=depth,
        children_count=len(children_list),
        children=children,
    )


def _find_menu_subtree(nodes: list[Any], needle: str) -> dict | None:
    """Breadth-first search for a category by name, url or id.

    Breadth-first on purpose: a short query like "обувь" should resolve to the
    top-level section, not to whichever deep subcategory happens to match first.
    """
    needle_low = needle.strip().lower()
    queue: list[Any] = list(nodes)
    while queue:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        name = _decode_mojibake(node.get("name", "")).strip().lower()
        url = str(node.get("url") or "").strip().lower()
        node_id = str(node.get("id") or "")
        if needle_low in (name, url, node_id) or url.endswith(f"/{needle_low}"):
            return node
        children = node.get("childs")
        if isinstance(children, list):
            queue.extend(children)
    return None
