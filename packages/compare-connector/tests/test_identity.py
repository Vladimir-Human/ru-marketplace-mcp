import pytest
from compare_connector.identity import (
    ProductIdentity,
    identity_from_mapping,
    match_product_identity,
    normalize_gtin,
)


def test_gtin_is_normalized_and_check_digit_validated():
    assert normalize_gtin("4006 3813 3393 1") == "4006381333931"
    assert normalize_gtin("4006381333932") == ""


def test_gtin_mismatch_cannot_be_overruled_by_same_title():
    result = match_product_identity(
        ProductIdentity(model="Phone 15", gtin="4006381333931"),
        ProductIdentity(model="Phone 15", gtin="1234567890128"),
    )
    assert result.status == "mismatch"


def test_matching_gtin_is_exact_but_variant_conflict_is_mismatch():
    result = match_product_identity(
        ProductIdentity(gtin="4006381333931", variant_attributes={"color": "black"}),
        ProductIdentity(gtin="4006381333931", variant_attributes={"color": "white"}),
    )
    assert result.status == "mismatch"
    assert "variant_mismatch" in result.reasons


def test_mpn_and_brand_match_is_exact_without_gtin():
    result = match_product_identity(
        ProductIdentity(brand="ACME", mpn="AB-12"),
        ProductIdentity(brand="acme", mpn="AB12"),
    )
    assert result.status == "exact"


def test_same_model_without_manufacturer_identifier_is_only_likely():
    result = match_product_identity(ProductIdentity(model="X 100 128GB"), ProductIdentity(model="x-100 128gb"))
    assert result.status == "likely"


def test_no_identity_evidence_explicitly_abstains():
    assert match_product_identity(ProductIdentity(), ProductIdentity()).status == "unknown"


def test_mapping_uses_typed_identifiers_and_variants_only():
    identity = identity_from_mapping(
        {
            "brand_name": "ACME",
            "manufacturer_part_number": "AB-12",
            "barcode": "4006381333931",
            "color": "Black",
            "size": "128GB",
            "title": "ACME AB-12 128GB",
            "id": 42,
        },
        source="fixture",
    )

    assert identity.brand == "ACME"
    assert identity.mpn == "AB12"
    assert identity.gtin == "4006381333931"
    assert identity.variant_attributes == {"color": "Black", "size": "128GB"}
    assert identity.native_product_id == "42"
    assert identity.source == "fixture"


def test_mapping_discards_invalid_gtin_instead_of_guessing():
    identity = identity_from_mapping({"gtin": "4006381333932", "title": "AB-12"})

    assert identity.gtin == ""
    assert identity.mpn == ""


@pytest.mark.parametrize("value", ["00000000", "-4006381333931", "400638133393.1", [4006381333931], True])
def test_gtin_rejects_malformed_values(value):
    assert normalize_gtin(value) == ""


def test_zero_padded_gtin_represents_the_same_trade_item():
    match = match_product_identity(ProductIdentity(gtin="4006381333931"), ProductIdentity(gtin="04006381333931"))
    assert match.status == "exact"


def test_mapping_does_not_promote_seller_article_to_manufacturer_id():
    result = identity_from_mapping({"vendor_code": "AB12", "article": "AB12", "sku": "AB12"})
    assert result.mpn == ""
    assert result.native_product_id == "AB12"


def test_mapping_discards_structures_instead_of_stringifying_them():
    result = identity_from_mapping({"mpn": {"value": "AB12"}, "brand": ["ACME"], "size": True})
    assert result.mpn == result.brand == ""
    assert result.variant_attributes == {}


def test_mpn_without_manufacturer_brand_does_not_prove_identity():
    result = match_product_identity(ProductIdentity(mpn="AB12"), ProductIdentity(mpn="AB12"))
    assert result.status == "unknown"
    assert result.reasons == ["mpn_requires_brand"]


@pytest.mark.parametrize("left,right", [("чёрный", "белый"), ("красный", "синий"), ("蓝色", "红色")])
def test_non_latin_variant_conflicts_are_not_erased(left, right):
    result = match_product_identity(
        ProductIdentity(gtin="4006381333931", variant_attributes={"color": left}),
        ProductIdentity(gtin="4006381333931", variant_attributes={"color": right}),
    )
    assert result.status == "mismatch"
    assert result.reasons == ["variant_mismatch"]


def test_matching_one_variant_attribute_does_not_prove_the_missing_other():
    result = match_product_identity(
        ProductIdentity(brand="ACME", mpn="AB12", variant_attributes={"color": "black", "storage": "256GB"}),
        ProductIdentity(brand="ACME", mpn="AB12", variant_attributes={"color": "black"}),
    )
    assert result.status == "unknown"
    assert result.reasons == ["incomplete_variant_evidence"]


def test_colour_alias_and_case_keep_variant_identity():
    result = match_product_identity(
        ProductIdentity(gtin="4006381333931", variant_attributes={"colour": "Чёрный"}),
        ProductIdentity(gtin="4006381333931", variant_attributes={"color": "чёрный"}),
    )
    assert result.status == "exact"


def test_model_name_does_not_override_brand_conflict():
    result = match_product_identity(
        ProductIdentity(model="X100", brand="Альфа"), ProductIdentity(model="X100", brand="Бета")
    )
    assert result.status == "mismatch"


def test_matching_gtin_does_not_override_conflicting_mpn():
    result = match_product_identity(
        ProductIdentity(gtin="4006381333931", mpn="A12"),
        ProductIdentity(gtin="4006381333931", mpn="B12"),
    )
    assert result.status == "mismatch"
