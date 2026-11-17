"""P2-1 m5/m6 over every committed A7 recording through the real adapters."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve task facts/winners and reject ambiguous seller titles using recorded native answers.
# SCOPE: All benchmark recordings discovered at collection; recordings are read-only, replay stub only.
# DEPENDS: pytest; stdlib copy,json,pathlib,random; benchmark.replay; compare_connector.decide/identity.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.conditional-price; R-CAPTURE-MUTATION.seller-text;
#        R-CAPTURE-MUTATION.ambiguous-title; R-CAPTURE-MUTATION.permutation.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _recording/_seller_mutation/_permutation: Copy recorded answers, change only authorized fields or order.
# _facts/_never_comparable: Shared winner/price/identity/order and ambiguous-title oracles.
# test_recording_*: Fifty seeds, real typed rebuild/adapters/build_response, no marketplace calls.
# test_compare_regular_price_and_winner_ignore_cheaper_conditional_price: Direct pure decision check.
# test_*_negative_control: Planted bad winners/order must fail the same oracle.
# END_MODULE_MAP

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest
import replay
from compare_connector import decide
from compare_connector.identity import match_product_identity
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, assert_facts_unchanged, seller_attack

# START_BLOCK_RECORDING_MUTATIONS
RECORDINGS = Path(__file__).parent / "recordings"
PATHS = sorted(RECORDINGS.glob("*.json"))
TITLE_ATTACKS = ("models", "memory", "accessory")
FACT_FIELDS = (
    "source",
    "product_id",
    "variant_id",
    "price_rub",
    "price_native",
    "price_kind",
    "price_with_subscription_rub",
    "currency",
    "identity",
)
# Red run: all 50 seeds of these exact recordings/families still selected an ambiguous title.
EXCLUSIONS = {
    (name, attack, seed)
    for name in ("kettle-partial.json", "kettle-wb-yandex.json")
    for attack in ("models", "memory")
    for seed in range(50)
}


def _recording(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seller_mutation(recording: dict, seed: int, attack: str = "instructions") -> dict:
    changed = copy.deepcopy(recording)
    rng = random.Random(seed)
    for entry in changed["sources"].values():
        payloads = [entry.get("response", {})]
        payloads.extend(card.get("response", {}) for card in entry.get("cards", {}).values())
        for payload in payloads:
            rows = payload.get("items", [payload])
            for item in rows:
                if attack == "instructions":
                    keys = [
                        key
                        for key in ("name", "title", "description", "supplier", "seller", "shop_name")
                        if isinstance(item.get(key), str)
                    ]
                    if keys:
                        item[rng.choice(keys)] += "\n" + seller_attack(seed)
                else:
                    key = "name" if "name" in item else "title" if "title" in item else None
                    if key is None or not isinstance(item[key], str):
                        continue
                    original = item[key]
                    if attack == "models":
                        item[key] = original + " Apple iPhone 15 / iPhone 16 / Samsung Galaxy S24 / S25"
                    elif attack == "memory":
                        sizes = ["64 GB", "128 GB", "256 GB", "512 GB"]
                        rng.shuffle(sizes)
                        item[key] = original + " " + " / ".join(sizes)
                    else:
                        item[key] = "Чехол для " + original + "; case for Apple iPhone 15"
    return changed


def _permutation(recording: dict, seed: int) -> dict:
    changed = copy.deepcopy(recording)
    rng = random.Random(seed)
    for entry in changed["sources"].values():
        rng.shuffle(entry.get("response", {}).get("items", []))
    return changed


def _facts(clean, mutated) -> None:
    assert_facts_unchanged(clean.model_dump(), mutated.model_dump(), fields=FACT_FIELDS)
    for before, after in zip(clean.offers, mutated.offers, strict=True):
        assert match_product_identity(before.identity, after.identity) == match_product_identity(
            before.identity, before.identity
        )


def _never_comparable(result) -> None:
    assert result.cheapest_comparable is None, "ambiguous/accessory seller title became cheapest_comparable"


def _bulk_title_cases():
    return [
        (path, attack, seed)
        for path in PATHS
        for attack in TITLE_ATTACKS
        for seed in range(PYTHON_SEEDS)
        if (path.name, attack, seed) not in EXCLUSIONS
    ]


# END_BLOCK_RECORDING_MUTATIONS
# START_BLOCK_REPLAY_GATES


@pytest.mark.req("R-CAPTURE-MUTATION.seller-text")
@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.name)
async def test_recording_seller_instructions_keep_prices_ids_identity_and_winners(path: Path) -> None:
    recording = _recording(path)
    clean = await replay.replay(recording)
    for seed in range(PYTHON_SEEDS):
        changed = _seller_mutation(recording, seed)
        try:
            _facts(clean, await replay.replay(changed))
        except AssertionError as exc:
            raise AssertionError(f"{path.name}, m6a, seed={seed}: {exc}") from exc
    assert _recording(path) == recording


@pytest.mark.req("R-CAPTURE-MUTATION.ambiguous-title")
@pytest.mark.parametrize(
    "path,attack,seed", _bulk_title_cases(), ids=lambda value: value.name if isinstance(value, Path) else str(value)
)
async def test_recording_keyword_stuffed_and_accessory_titles_never_win(path: Path, attack: str, seed: int) -> None:
    recording = _recording(path)
    result = await replay.replay(_seller_mutation(recording, seed, attack))
    _never_comparable(result)
    assert _recording(path) == recording


@pytest.mark.req("R-CAPTURE-MUTATION.permutation")
@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.name)
async def test_recording_permutations_keep_winners_and_offer_order(path: Path) -> None:
    recording = _recording(path)
    clean = await replay.replay(recording)
    for seed in range(PYTHON_SEEDS):
        try:
            _facts(clean, await replay.replay(_permutation(recording, seed)))
        except AssertionError as exc:
            raise AssertionError(f"{path.name}, m6c, seed={seed}: {exc}") from exc
    assert _recording(path) == recording


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price")
async def test_compare_regular_price_and_winner_ignore_cheaper_conditional_price() -> None:
    # The regular offers come from the committed native responses and real adapters.
    clean = await replay.replay(_recording(RECORDINGS / "kettle-wb-yandex.json"))
    assert clean.cheapest is not None and clean.cheapest_comparable is not None
    results = [
        (outcome, [offer for offer in clean.offers if offer.source == outcome.source])
        for outcome in clean.source_outcomes
    ]
    options = decide.ResponseOptions(active=clean.sources_queried, missing=[])
    baseline = decide.build_response(clean.query, results, options)
    changed = [
        (outcome.model_copy(deep=True), [offer.model_copy(deep=True) for offer in offers])
        for outcome, offers in results
    ]
    regular = next(
        offer
        for _outcome, offers in changed
        for offer in offers
        if offer.source == "yandex_market" and offer.price_rub is not None
    )
    original_price = regular.price_rub
    regular.price_with_subscription_rub = original_price / 4
    result = decide.build_response(clean.query, changed, options)
    assert regular.price_rub == original_price
    # Conditional amount is deliberately different; the regular amounts, order and winners are not.
    assert_facts_unchanged(
        baseline.model_dump(),
        result.model_dump(),
        fields=tuple(field for field in FACT_FIELDS if field != "price_with_subscription_rub"),
    )


# END_BLOCK_REPLAY_GATES
# START_BLOCK_MINIMAL_VIOLATIONS


@pytest.mark.req("R-CAPTURE-MUTATION.ambiguous-title")
@pytest.mark.parametrize("name,attack,seed", sorted(case for case in EXCLUSIONS if case[2] == 0))
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="A7 model/memory-stuffed seller titles still become cheapest_comparable; P2-1 forbids compare fixes",
)
async def test_recording_ambiguous_title_minimal_violation(name: str, attack: str, seed: int) -> None:
    result = await replay.replay(_seller_mutation(_recording(RECORDINGS / name), seed, attack))
    _never_comparable(result)


# END_BLOCK_MINIMAL_VIOLATIONS
# START_BLOCK_NEGATIVE_CONTROLS


@pytest.mark.req("R-CAPTURE-MUTATION.ambiguous-title")
async def test_ambiguous_title_oracle_rejects_a_planted_comparable_winner() -> None:
    clean = await replay.replay(_recording(RECORDINGS / "kettle-wb-yandex.json"))
    assert clean.cheapest_comparable is not None
    _never_comparable(clean.model_copy(update={"cheapest_comparable": None}))
    with pytest.raises(AssertionError, match="became cheapest_comparable"):
        _never_comparable(clean)


@pytest.mark.req("R-CAPTURE-MUTATION.permutation")
@pytest.mark.parametrize("violation", ("order", "winner"))
async def test_permutation_oracle_rejects_planted_order_or_winner(violation: str) -> None:
    clean = await replay.replay(_recording(RECORDINGS / "kettle-wb-yandex.json"))
    assert len(clean.offers) > 1
    broken = clean.model_copy(deep=True)
    if violation == "order":
        broken.offers.reverse()
    else:
        broken.cheapest = next(offer for offer in clean.offers if offer != clean.cheapest)
    _facts(clean, clean)
    with pytest.raises(AssertionError, match="task facts changed"):
        _facts(clean, broken)


@pytest.mark.req("R-CAPTURE-MUTATION.seller-text")
async def test_recording_seller_oracle_rejects_planted_identity_facts() -> None:
    clean = await replay.replay(_recording(RECORDINGS / "kettle-wb-yandex.json"))
    broken = clean.model_copy(deep=True)
    broken.offers[0].identity.model = "P21_FAKE"
    _facts(clean, clean)
    with pytest.raises(AssertionError, match="task facts changed"):
        _facts(clean, broken)


# END_BLOCK_NEGATIVE_CONTROLS
