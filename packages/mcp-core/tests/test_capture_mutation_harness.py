"""Requirement-derived controls for the stdlib capture-mutation harness."""

# START_MODULE_CONTRACT
# PURPOSE: Prove deterministic mutations, input preservation and fail-capable price/id oracles.
# SCOPE: Unit objects are oracle controls, not fake marketplace responses; real captures drive mutators.
# DEPENDS: pytest; stdlib json, random, pathlib; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_equivalent_json/test_equivalent_html: m1 preserves meaningful committed input.
# test_price_mutations: m2/m4 change exactly one actual amount, not its id.
# test_oracle_*: Known-item/field, fallback, truncation and decoy positive/negative controls.
# test_raw_amounts_grouped_and_decimal_tokens: Complete raw numeric tokens across the four space kinds.
# test_truncation_coverage/test_unexpected_errors: Skip accounting and exception boundary.
# END_MODULE_MAP

import json
import random
from pathlib import Path

import pytest
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import (
    PYTHON_SEEDS,
    assert_oracle,
    check_case,
    decoy_price,
    equivalent,
    raw_amounts,
    remove_price,
    run_mutations,
)

ROOT = Path(__file__).resolve().parents[3]
JSON_FIXTURE = ROOT / "packages/avito-connector/tests/fixtures/js_items_live.json"
HTML_FIXTURE = ROOT / "packages/dns-connector/tests/fixtures/search_grid.html"
PATHS = (r"priceDetailed\.value$",)

# START_BLOCK_MUTATOR_CONTROLS


@pytest.mark.req("R-CAPTURE-MUTATION.equivalent")
def test_equivalent_json() -> None:
    raw = JSON_FIXTURE.read_text(encoding="utf-8")
    clean = json.loads(raw)
    mutated = equivalent(raw, random.Random(0))
    assert mutated == equivalent(raw, random.Random(0))
    value = json.loads(mutated)
    assert value.pop("_p21_unrelated")
    assert value == clean
    assert raw == JSON_FIXTURE.read_text(encoding="utf-8")


@pytest.mark.req("R-CAPTURE-MUTATION.equivalent")
def test_equivalent_html() -> None:
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    mutated = equivalent(raw, random.Random(0), kind="html")
    assert mutated != raw
    assert mutated == equivalent(raw, random.Random(0), kind="html")
    assert '<meta data-p21="unrelated">' in mutated
    assert raw == HTML_FIXTURE.read_text(encoding="utf-8")


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("marker", DECOY_MARKERS)
def test_price_mutations(marker: str) -> None:
    raw = JSON_FIXTURE.read_text(encoding="utf-8")
    clean = json.loads(raw)
    removed = json.loads(remove_price(raw, random.Random(0), price_paths=PATHS))
    decoy = json.loads(decoy_price(raw, random.Random(0), marker, price_paths=PATHS))
    before = clean["catalog"]["items"]
    after = removed["catalog"]["items"]
    changed = [index for index, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert len(changed) == 1
    index = changed[0]
    assert "value" not in after[index]["priceDetailed"]
    assert decoy["catalog"]["items"][index]["priceDetailed"]["value"] == marker
    assert [item["id"] for item in before] == [item["id"] for item in after]
    assert raw == JSON_FIXTURE.read_text(encoding="utf-8")


# END_BLOCK_MUTATOR_CONTROLS
# START_BLOCK_ORACLE_CONTROLS


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("field", ("price", "original", "card"))
def test_oracle_rejects_broken_output(field: str) -> None:
    # Deliberately broken output is the red control; no parser is replaced.
    clean = {"id": 1, "price": 100.0, "original": 120.0, "card": 90.0}
    broken = {**clean, field: 999.0}
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle(clean, broken, clean_raw="999", price_fields=("price", "original", "card"), id_fields=("id",))
    assert_oracle(clean, clean, clean_raw="", price_fields=("price", "original", "card"), id_fields=("id",))
    assert_oracle(
        clean, {"id": 1, "price": None}, clean_raw="", price_fields=("price", "original", "card"), id_fields=("id",)
    )
    assert_oracle(clean, [], clean_raw="", price_fields=("price", "original", "card"), id_fields=("id",))


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
def test_oracle_rejects_neighbours_price() -> None:
    clean = [{"id": 1, "price": 100.0}, {"id": 2, "price": 200.0}]
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle(
            clean, [{"id": 1, "price": 200.0}], clean_raw="100 200", price_fields=("price",), id_fields=("id",)
        )


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
def test_oracle_rejects_wrong_price_field() -> None:
    clean = {"id": 1, "price": 100.0, "original": 120.0}
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle(
            clean, {"id": 1, "price": 120.0}, clean_raw="120", price_fields=("price", "original"), id_fields=("id",)
        )


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("unknown", (None, ""))
def test_oracle_uses_any_known_id_field(unknown: object) -> None:
    clean = {"id": 1, "sku_id": 10, "price": 100.0}
    fields = {"clean_raw": "200", "price_fields": ("price",), "id_fields": ("id", "sku_id")}
    assert_oracle(clean, {"id": unknown, "sku_id": 10, "price": 100.0}, **fields)
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle(clean, {"id": unknown, "sku_id": 10, "price": 200.0}, **fields)


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
def test_oracle_matches_nested_item_representations() -> None:
    clean = {"extracted": [{"id": 1}], "monetary": [{"id": 1, "price": 100.0}]}
    assert_oracle(clean, clean, clean_raw="", price_fields=("price",), id_fields=("id",))
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle(
            clean,
            {"monetary": [{"id": 1, "price": 200.0}]},
            clean_raw="200",
            price_fields=("price",),
            id_fields=("id",),
        )


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("identity", (2, None, ""))
def test_oracle_accepts_real_fallback_amounts(identity: object) -> None:
    clean = {"id": 1, "price": 100.0}
    # New ids and unknown ids may expose a different real amount on the same page.
    assert_oracle(
        clean, {"id": identity, "price": 1063.0}, clean_raw="1063 ₽", price_fields=("price",), id_fields=("id",)
    )
    # Prices without an identified item also include the clean output's parsed amounts.
    assert_oracle(clean, {"price": 100.0}, clean_raw="", price_fields=("price",), id_fields=("id",))
    assert_oracle(clean, {"price": 1063.0}, clean_raw="1063 ₽", price_fields=("price",), id_fields=("id",))


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("identity", (2, None, ""))
def test_oracle_rejects_truncated_amount(identity: object) -> None:
    clean_raw = "64532 ₽"
    assert 645 not in raw_amounts(clean_raw)
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle(
            {}, {"id": identity, "price": 645}, clean_raw=clean_raw, price_fields=("price",), id_fields=("id",)
        )
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle({}, {"price": 645}, clean_raw=clean_raw, price_fields=("price",), id_fields=("id",))


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("marker", DECOY_MARKERS)
def test_oracle_rejects_decoy_made_amount(marker: str) -> None:
    clean_raw = "64532 ₽"
    decoy = f"999 {marker}"
    assert 999 in raw_amounts(decoy)
    assert 999 not in raw_amounts(clean_raw)
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle({}, {"id": 2, "price": 999}, clean_raw=clean_raw, price_fields=("price",), id_fields=("id",))
    with pytest.raises(AssertionError, match="invented price"):
        assert_oracle({}, {"price": 999}, clean_raw=clean_raw, price_fields=("price",), id_fields=("id",))


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("separator", (" ", "\u00a0", "\u2009", "\u202f"))
def test_raw_amounts_grouped_and_decimal_tokens(separator: str) -> None:
    raw = f"64{separator}532 ₽; 1{separator}063.50 ₽; 2{separator}099,75 ₽; 8.4; 9,8; 17"
    assert raw_amounts(raw) == {64532, 1063.5, 2099.75, 8.4, 9.8, 17}


@pytest.mark.req("R-CAPTURE-MUTATION.equivalent")
def test_equivalent_oracle_rejects_lost_output() -> None:
    with pytest.raises(AssertionError, match="m1 changed"):
        assert_oracle({"id": 1}, [], clean_raw="", price_fields=(), id_fields=("id",), equivalent_output=True)


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
def test_truncation_coverage() -> None:
    # json.loads IS the decoder under test here, not a fake connector parser.
    raw = JSON_FIXTURE.read_text(encoding="utf-8")
    counts = run_mutations(
        raw,
        json.loads,
        fixture=JSON_FIXTURE.name,
        mutation="m3",
        kind="json",
        price_paths=PATHS,
        price_fields=("value",),
        id_fields=("id",),
        markers=DECOY_MARKERS,
    )
    assert counts["reached"] >= (PYTHON_SEEDS + 1) // 2
    assert counts["reached"] + counts["json_skips"] == PYTHON_SEEDS
    assert counts["json_skips"] > 0


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
def test_unexpected_errors_and_preparser_decode_skips() -> None:
    # The real stdlib decoder is the parser in this unit check, not a fake connector.
    raw = JSON_FIXTURE.read_text(encoding="utf-8")[:1]
    assert not check_case(
        raw,
        {},
        json.loads,
        clean_raw=JSON_FIXTURE.read_text(encoding="utf-8"),
        mutation="m3",
        kind="json",
        price_fields=(),
        id_fields=(),
        allowed_errors=(ConnectorError,),
    )
    # With no pre-parser JSON boundary the decoder's own JSONDecodeError must escape:
    # it is not a documented ConnectorError and must never become an allowed failure.
    with pytest.raises(json.JSONDecodeError):
        check_case(
            raw,
            {},
            json.loads,
            clean_raw=JSON_FIXTURE.read_text(encoding="utf-8"),
            mutation="m3",
            kind="html",
            price_fields=(),
            id_fields=(),
            allowed_errors=(ConnectorError,),
        )


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
def test_driver_rejects_missing_price_target() -> None:
    raw = JSON_FIXTURE.read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="priced capture has no mutation target"):
        run_mutations(
            raw,
            json.loads,
            fixture=JSON_FIXTURE.name,
            mutation="m2",
            kind="json",
            price_paths=(),
            price_fields=("value",),
            id_fields=("id",),
            markers=DECOY_MARKERS,
        )


# END_BLOCK_ORACLE_CONTROLS
