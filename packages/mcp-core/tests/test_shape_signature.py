"""Tests for structural drift fingerprinting.

The point of shape_signature is to separate "the marketplace sent different
data" from "the marketplace changed its response shape". The first happens
every request and must be ignored; the second silently breaks a parser and must
be caught. Each test below pins one half of that distinction.
"""

from __future__ import annotations

from mcp_core.resilience import diff_keys, shape_signature

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
