"""Tests for structural drift fingerprinting.

The point of shape_signature is to separate "the marketplace sent different
data" from "the marketplace changed its response shape". The first happens
every request and must be ignored; the second silently breaks a parser and must
be caught. Each test below pins one half of that distinction.
"""

from __future__ import annotations

from mcp_core.resilience import diff_keys, shape_drift_warning, shape_signature

# A trimmed stand-in for a search payload: the parts a parser binds to.
BASELINE = {
    "total": 57,
    "items": [
        {"id": 1, "title": "Ноутбук", "price_rub": 52999.0, "in_stock": True, "seller": {"name": "ООО Ромашка"}},
        {"id": 2, "title": "Ноутбук 2", "price_rub": 61000.0, "in_stock": False, "seller": {"name": "ИП Петров"}},
    ],
}


def test_the_same_shape_with_different_values_fingerprints_identically():
    changed_values = {
        "total": 3,
        "items": [
            {"id": 99, "title": "Другой товар", "price_rub": 100.0, "in_stock": False, "seller": {"name": "X"}},
        ],
    }

    assert shape_signature(BASELINE) == shape_signature(changed_values)


def test_a_different_item_count_does_not_register_as_drift():
    """List indices collapse, so page size must not move the fingerprint."""
    one_item = {"total": 57, "items": [BASELINE["items"][0]]}

    assert shape_signature(BASELINE) == shape_signature(one_item)


def test_a_renamed_field_shows_up_as_missing():
    renamed = {
        "total": 57,
        "items": [{"id": 1, "title": "x", "cost": 52999.0, "in_stock": True, "seller": {"name": "y"}}],
    }

    delta = diff_keys(shape_signature(BASELINE), shape_signature(renamed))

    assert "items[].price_rub:float" in delta["missing"]
    assert "items[].cost:float" in delta["added"]


def test_a_retyped_field_shows_up_even_when_the_name_survives():
    """price arriving as "52 999 ₽" instead of a number is exactly how a
    tolerant reader starts returning None without anyone noticing."""
    retyped = {
        "total": 57,
        "items": [{"id": 1, "title": "x", "price_rub": "52 999 ₽", "in_stock": True, "seller": {"name": "y"}}],
    }

    delta = diff_keys(shape_signature(BASELINE), shape_signature(retyped))

    assert "items[].price_rub:float" in delta["missing"]
    assert "items[].price_rub:str" in delta["added"]


def test_a_nested_field_disappearing_is_caught():
    flattened = {
        "total": 57,
        "items": [{"id": 1, "title": "x", "price_rub": 1.0, "in_stock": True, "seller": "ООО Ромашка"}],
    }

    delta = diff_keys(shape_signature(BASELINE), shape_signature(flattened))

    assert "items[].seller.name:str" in delta["missing"]


def test_an_emptied_container_is_distinguishable_from_a_populated_one():
    emptied = {"total": 0, "items": []}

    signature = shape_signature(emptied)

    assert "items:empty_array" in signature
    assert not any(entry.startswith("items[]") for entry in signature)


def test_bool_is_not_reported_as_int():
    """bool subclasses int in Python; conflating them would hide a real retype."""
    assert shape_signature({"flag": True}) == ["flag:bool"]
    assert shape_signature({"flag": 1}) == ["flag:int"]


def test_null_is_its_own_type_rather_than_an_absent_key():
    assert shape_signature({"rating": None}) == ["rating:null"]


def test_recursion_is_bounded():
    """WB category trees nest arbitrarily; the walk must terminate."""
    deep: dict = {"name": "root"}
    node = deep
    for _ in range(50):
        node["childs"] = {"name": "child"}
        node = node["childs"]

    signature = shape_signature(deep, max_depth=4)

    assert any(entry.endswith(":<truncated>") for entry in signature)


def test_a_scalar_payload_is_handled():
    assert shape_signature("just a string") == [".:str"]
    assert shape_signature([]) == [".:empty_array"]


def test_a_lost_family_yields_the_compact_drift_warning():
    """The runtime half of the family check: one warning string naming the
    lost family's aliases, not a raise — the tool's answer stays usable and
    the agent sees the drift in meta.warnings."""
    lost = {"total": 57, "items": [{"id": 1, "title": "x", "in_stock": True}]}

    warning = shape_drift_warning(lost, (("items[].price", "items[].price_rub"),), source="test_search")

    assert warning == "shape_drift: test_search: missing items[].price/items[].price_rub"


def test_a_complete_payload_yields_no_drift_warning():
    families = (("items[].id",), ("items[].title",), ("items[].price_rub",))

    assert shape_drift_warning(BASELINE, families, source="test_search") is None


def test_a_null_value_still_counts_as_a_present_family_member():
    """Presence, not type: a null price key is a page without a price, not a
    page that lost its price field."""
    nulled = {"total": 0, "items": [{"id": 1, "title": "x", "price_rub": None}]}

    assert shape_drift_warning(nulled, (("items[].price_rub",),), source="test_search") is None


def test_an_empty_container_is_not_a_lost_element_family():
    """Vacuity, not drift: a delisted WB page arrives with "sizes": [] on every
    product, and a key inside an array that holds no elements cannot have been
    lost — the same rule that keeps a zero-item payload out of the check. The
    boundary stays sharp: an array that still HAS elements which lost the key
    is a rename, and must warn."""
    delisted = {"products": [{"id": 1, "name": "x", "sizes": [], "totalQuantity": 0}]}
    renamed = {"products": [{"id": 1, "name": "x", "sizes": [{"optionId": 7}], "totalQuantity": 0}]}

    assert shape_drift_warning(delisted, (("products[].sizes[].price",),), source="test_search") is None
    assert (
        shape_drift_warning(renamed, (("products[].sizes[].price",),), source="test_search")
        == "shape_drift: test_search: missing products[].sizes[].price"
    )
    # A page mixing delisted products with sizes that lost the key still warns:
    # the merged signature shows filled sizes next to the empty ones.
    mixed = {"products": [delisted["products"][0], renamed["products"][0]]}
    assert shape_drift_warning(mixed, (("products[].sizes[].price",),), source="test_search") is not None
    # An empty sibling envelope is not vacuity: the filled one lost the key.
    sibling = {"items": [], "goods": [{"name": "x"}]}
    assert shape_drift_warning(sibling, (("items[].id", "goods[].id"),), source="test_search") is not None
