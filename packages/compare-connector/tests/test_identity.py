from compare_connector.identity import (
    ProductIdentity,
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
