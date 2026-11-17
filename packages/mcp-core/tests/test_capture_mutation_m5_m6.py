"""Fail-capable controls for adjacent prices and seller-text capture mutations."""

# START_MODULE_CONTRACT
# PURPOSE: Prove P2-1 m5/m6 mutators preserve task facts and their oracles can fail.
# SCOPE: Committed captures and explicitly planted broken outputs; offline only.
# DEPENDS: pytest; stdlib html,pathlib,re; mcp_core.resilience.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.split-payment.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _captured_regular_text: Read the first marked regular price from the committed DNS grid.
# test_split_payment_candidates_never_replace_captured_regular_price: Minimal m6e red cases.
# END_MODULE_MAP

import html
import importlib
import json
import random
import re
from pathlib import Path

import pytest
from mcp_core.resilience import coerce_price, price_from_texts
from mcp_core.testing.capture_mutation import (
    PYTHON_SEEDS,
    adjacent_price,
    assert_facts_unchanged,
    preserving_case,
    run_preserving_mutations,
    seller_attack,
)

ROOT = Path(__file__).resolve().parents[3]
HTML_FIXTURE = ROOT / "packages/dns-connector/tests/fixtures/search_grid.html"
SPLIT_DISPLAYS = (
    "4 × 1 250 ₽ Долями",
    "1 250 ₽ Долями",
    "1 250 ₽ Сплит",
    "1 250 ₽ частями",
    "от 2 500 ₽/мес",
)

# START_BLOCK_SPLIT_PAYMENT_REGRESSIONS


def _captured_regular_text() -> str:
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    match = re.search(r'<div class="product-buy__price">([^<]+)', raw)
    assert match, "the committed grid must carry its marked regular price"
    return html.unescape(match[1])


@pytest.mark.req("R-CAPTURE-MUTATION.split-payment")
@pytest.mark.parametrize("display", SPLIT_DISPLAYS)
def test_split_payment_candidates_never_replace_captured_regular_price(display: str) -> None:
    regular = _captured_regular_text()
    assert coerce_price(regular) is not None
    # The split display precedes the unchanged captured regular candidate.
    assert price_from_texts(display, regular) == coerce_price(regular)


# END_BLOCK_SPLIT_PAYMENT_REGRESSIONS
# START_BLOCK_MUTATOR_CONTROLS

JSON_FIXTURE = ROOT / "packages/avito-connector/tests/fixtures/js_items_live.json"
PRICE_PATHS = (r"^catalog\.items\[\]\.priceDetailed\.value$",)
TEXT_PATHS = (r"^catalog\.items\[\]\.title$",)


def _options() -> dict:
    return {"kind": "json", "price_paths": PRICE_PATHS, "text_paths": TEXT_PATHS}


@pytest.mark.req(
    "R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.seller-text", "R-CAPTURE-MUTATION.split-payment"
)
@pytest.mark.parametrize("mutation", ("m5", "m6a", "m6e"))
def test_new_mutations_are_deterministic_and_keep_captured_task_facts(mutation: str) -> None:
    raw = JSON_FIXTURE.read_text(encoding="utf-8")
    clean = json.loads(raw)
    for seed in range(PYTHON_SEEDS):
        mutated = preserving_case(raw, mutation, seed, **_options())
        assert mutated != raw
        assert mutated == preserving_case(raw, mutation, seed, **_options())
        after = json.loads(mutated)
        changed = 0
        for before_item, after_item in zip(clean["catalog"]["items"], after["catalog"]["items"], strict=True):
            if before_item == after_item:
                continue
            changed += 1
            if mutation == "m6a":
                assert after_item["title"] == before_item["title"] + "\n" + seller_attack(seed)
                after_item["title"] = before_item["title"]
            else:
                key = "cardPrice" if mutation == "m5" else "installmentPrice"
                extra = after_item["priceDetailed"].pop(key)
                assert 0 < extra["amount"] < before_item["priceDetailed"]["value"]
                assert extra["price_kind"] == ("card" if mutation == "m5" else "split")
            assert after_item == before_item
        assert changed == 1
        assert after == clean
    assert JSON_FIXTURE.read_text(encoding="utf-8") == raw


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.split-payment")
@pytest.mark.parametrize("mutation", ("m5", "m6e"))
def test_html_price_sibling_never_rewrites_the_regular_node(mutation: str) -> None:
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    nodes = (r'class="product-buy__price"',)
    for seed in range(PYTHON_SEEDS):
        mutated = preserving_case(raw, mutation, seed, kind="html", price_nodes=nodes)
        assert mutated != raw
        # Removing only the labelled addition recovers the entire original capture.
        restored, count = re.subn(
            r'<div class="product-buy__price" data-price-kind="(?:card|split)">.*?</div>', "", mutated
        )
        assert count == 1
        assert restored == raw


@pytest.mark.req("R-CAPTURE-MUTATION.seller-text")
def test_html_seller_text_keeps_attributes_specs_and_price_nodes() -> None:
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    for seed in range(PYTHON_SEEDS):
        mutated = preserving_case(raw, "m6a", seed, kind="html", text_nodes=(r'class="catalog-product__name(?:\s|\")',))
        assert mutated != raw
        assert mutated.replace("\n" + html.escape(seller_attack(seed)), "", 1) == raw


@pytest.mark.req("R-CAPTURE-MUTATION.seller-text")
@pytest.mark.parametrize("package", ("aliexpress", "citilink", "dns", "lamoda", "taobao"))
def test_dom_seller_mutation_targets_cover_the_committed_capture_fields(package: str) -> None:
    # This checks mutation targeting only, not extractor execution; no Node or transport fake.
    adapter = importlib.import_module(f"packages.{package}-connector.tests.test_capture_mutation_m5_m6")
    textless = {"refusal_page_live.html", "search_login_wall_live.html"}
    for name in adapter.capture.CASES:
        raw = (adapter.capture.FIXTURES / name).read_text(encoding="utf-8")
        for seed in range(adapter.capture.TAOBAO_SEEDS if package == "taobao" else adapter.JSDOM_SEEDS):
            mutated = preserving_case(raw, "m6a", seed, kind="html", text_nodes=adapter.TEXT_NODES)
            if name in textless:
                assert mutated == raw
            else:
                assert mutated != raw, f"{package}/{name}: no seller-editable mutation target"
                assert mutated.replace("\n" + html.escape(seller_attack(seed)), "", 1) == raw


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.split-payment")
def test_adjacent_prices_skip_zero_amount_fields_in_priced_captures() -> None:
    path = ROOT / "packages/cian-connector/tests/fixtures/search_rent_live.json"
    raw = path.read_text(encoding="utf-8")
    paths = (r"^data\.offersSerialized\[\]\.bargainTerms\.(?:priceRur|price|deposit)$",)
    for mutation in ("m5", "m6e"):
        for seed in range(PYTHON_SEEDS):
            assert preserving_case(raw, mutation, seed, kind="json", price_paths=paths) != raw


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price")
def test_conditional_display_is_cheaper_in_native_rubles_not_kopecks() -> None:
    path = ROOT / "packages/wb-connector/tests/fixtures/card_v4_live.json"
    raw = path.read_text(encoding="utf-8")
    paths = (r"^products\[\]\.sizes\[\]\.price\.product$",)
    clean = json.loads(raw)

    def check(scale: int) -> None:
        for seed in range(PYTHON_SEEDS):
            changed = json.loads(preserving_case(raw, "m5", seed, kind="json", price_paths=paths, amount_scale=scale))
            for before, after in zip(clean["products"], changed["products"], strict=True):
                for old_size, new_size in zip(before["sizes"], after["sizes"], strict=True):
                    if "cardPrice" in new_size["price"]:
                        display = coerce_price(new_size["price"]["cardPrice"]["text"])
                        assert display is not None and 0 < display < old_size["price"]["product"] / 100

    check(100)
    # Planted wrong-unit display: raw kopecks presented as rubles must fail.
    with pytest.raises(AssertionError):
        check(1)


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.split-payment")
def test_adjacent_display_uses_the_price_not_a_quantity_in_the_node() -> None:
    # Synthetic node is a mutator unit input, not a marketplace/parser fixture.
    raw = '<span class="price">1 шт · 59 990 ₽</span>'
    for seed in range(PYTHON_SEEDS):
        changed = adjacent_price(raw, random.Random(seed), kind="html", price_nodes=(r'class="price"',))
        match = re.search(r'data-price-kind="card">([^<]+)', changed)
        assert match, "the price node must acquire a labelled sibling"
        amounts = re.findall(r"[\d ]+(?:\.\d+)?", match[1])
        amount = float(amounts[0].replace(" ", ""))
        assert 7_498.75 <= amount <= 14_997.5
        assert raw in changed


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.split-payment")
def test_adjacent_cny_displays_keep_the_captured_currency() -> None:
    path = ROOT / "packages/taobao-connector/tests/fixtures/search_grid_iphone15_live.html"
    raw = path.read_text(encoding="utf-8")
    nodes = (r'class="priceWrapper[^\"]*"',)
    assert "₽" not in raw
    for mutation in ("m5", "m6e"):
        changed = preserving_case(raw, mutation, 0, kind="html", price_nodes=nodes, currency="¥")
        assert changed != raw and "₽" not in changed
        assert "¥" in changed


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.split-payment")
@pytest.mark.parametrize("package", ("aliexpress", "citilink", "dns", "lamoda", "taobao"))
def test_dom_adjacent_mutations_have_targets_even_for_minimal_xfail_cases(package: str) -> None:
    # Targeting is checked independently of jsdom; no extractor output is substituted.
    adapter = importlib.import_module(f"packages.{package}-connector.tests.test_capture_mutation_m5_m6")
    unpriced = {"refusal_page_live.html", "search_login_wall_live.html"}
    if package == "aliexpress":
        unpriced.add("card.html")  # Captured SSR shell has no price module.
    no_target = set(getattr(adapter, "NO_PRICE_TARGETS", ()))
    nodes = getattr(adapter, "PRICE_NODES", adapter.capture.PRICE_NODES)
    for name in adapter.capture.CASES:
        if name in no_target:
            continue  # Explicit family configuration exclusion from brief2; some seeds have no usable target.
        raw = (adapter.capture.FIXTURES / name).read_text(encoding="utf-8")
        for mutation in ("m5", "m6e"):
            seeds = adapter.capture.TAOBAO_SEEDS if package == "taobao" else adapter.JSDOM_SEEDS
            for seed in range(seeds):
                changed = preserving_case(
                    raw, mutation, seed, kind="html", price_nodes=nodes, currency="¥" if package == "taobao" else "₽"
                )
                if name in unpriced:
                    assert changed == raw, f"{package}/{name}/{mutation}/{seed}: no-price control changed"
                else:
                    assert changed != raw, f"{package}/{name}/{mutation}/{seed}: no price mutation target"
                    assert 'data-price-kind="' in changed


# END_BLOCK_MUTATOR_CONTROLS
# START_BLOCK_NEGATIVE_CONTROLS


def _control_parser(raw: str, *, broken: str = "") -> list[dict]:
    """Planted price selection only, not a replacement marketplace response."""
    rows = []
    for item in json.loads(raw)["catalog"]["items"]:
        price = item["priceDetailed"]["value"]
        if broken in {"m5", "m6e"}:
            key = "cardPrice" if broken == "m5" else "installmentPrice"
            price = item["priceDetailed"].get(key, {}).get("amount", price)
        elif broken == "m6a" and "P21_FAKE" in item["title"]:
            price = 1
        rows.append({"id": item["id"], "price": price})
    return rows


@pytest.mark.req(
    "R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.seller-text", "R-CAPTURE-MUTATION.split-payment"
)
@pytest.mark.parametrize("mutation", ("m5", "m6a", "m6e"))
def test_gate_rejects_a_planted_parser_that_uses_the_attack_price(mutation: str) -> None:
    raw = JSON_FIXTURE.read_text(encoding="utf-8")
    options = {
        **_options(),
        "fixture": JSON_FIXTURE.name,
        "mutation": mutation,
        "price_fields": ("price",),
        "id_fields": ("id",),
    }
    # The same gate passes an attack-ignoring parser and rejects the planted violation.
    assert run_preserving_mutations(raw, _control_parser, **options)["reached"] == PYTHON_SEEDS
    with pytest.raises(AssertionError, match="task facts changed"):
        run_preserving_mutations(raw, lambda text: _control_parser(text, broken=mutation), **options)


@pytest.mark.req("R-CAPTURE-MUTATION.seller-text")
@pytest.mark.parametrize(
    "broken",
    (
        [],
        [{"id": 1, "price": None}, {"id": 2, "price": 200}],
        [{"price": 100}, {"id": 2, "price": 200}],
        [{"id": 2, "price": 200}, {"id": 1, "price": 100}],
        [{"id": 1, "price": 100, "identity": {"verdict": "exact"}}, {"id": 2, "price": 200}],
    ),
    ids=("lost-all", "lost-price", "lost-id", "reordered", "fake-verdict"),
)
def test_fact_oracle_rejects_missing_changed_or_reordered_facts(broken: list) -> None:
    clean = [{"id": 1, "price": 100}, {"id": 2, "price": 200}]
    fields = ("id", "price", "identity")
    assert_facts_unchanged(clean, clean, fields=fields)
    with pytest.raises(AssertionError, match="task facts changed"):
        assert_facts_unchanged(clean, broken, fields=fields)


@pytest.mark.req("R-CAPTURE-MUTATION.seller-text")
def test_preserving_gate_rejects_a_vacuous_seller_mutation() -> None:
    with pytest.raises(AssertionError, match="no mutation target"):
        run_preserving_mutations(
            JSON_FIXTURE.read_text(encoding="utf-8"),
            _control_parser,
            fixture=JSON_FIXTURE.name,
            mutation="m6a",
            kind="json",
            text_paths=(),
            price_fields=("price",),
            id_fields=("id",),
        )


@pytest.mark.req("R-CAPTURE-MUTATION.seller-text")
def test_seller_gate_tracks_every_emitted_id_not_just_the_product_id() -> None:
    def parser(raw: str) -> list[dict]:
        rows = _control_parser(raw)
        rows[0]["seller_id"] = 999 if "P21_FAKE" in raw else 123
        return rows

    with pytest.raises(AssertionError, match="task facts changed"):
        run_preserving_mutations(
            JSON_FIXTURE.read_text(encoding="utf-8"),
            parser,
            **_options(),
            fixture=JSON_FIXTURE.name,
            mutation="m6a",
            price_fields=("price",),
            id_fields=("id",),
        )


# END_BLOCK_NEGATIVE_CONTROLS
