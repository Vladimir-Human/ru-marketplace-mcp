"""Deciding what a comparison says: pure ranking over gathered results.

The comparison itself is I/O: every selected marketplace is queried at once and
each one may fail on its own. What comes after the gather is not. The offers
are deduplicated, ranked on everyday roubles, checked for accessories, used
condition, price outliers and size conflicts, and turned into one ranked answer
with warnings. That part lives here, as pure functions over the results the
tool gathered, so it can be read and tested without a marketplace in the room.

Nothing in this module fetches, waits, sleeps or writes to stdout: it is on the
architecture gate's pure list.
"""
# START_MODULE_CONTRACT
# PURPOSE: R-COMPARE-RELEVANCE-BOUNDARIES; R-COMPARE-BUNDLE-NOUN-PHRASES; R-COMPARE-CONDITIONAL-PRICE; R-COMPARE-VARIANT-IDENTITY; R-COMPARE-MODEL-TOKENS; R-NO-FABRICATION: rank everyday-ruble offers, warn about accessories, conditions, outliers, other models and size conflicts, and build the comparison answer.
# SCOPE: Pure decisions over the results a comparison gathered; no source access, no browser, no model, no stdout.
# REQUIREMENTS: R-COMPARE-RELEVANCE-BOUNDARIES; R-COMPARE-BUNDLE-NOUN-PHRASES; R-COMPARE-CONDITIONAL-PRICE; R-COMPARE-VARIANT-IDENTITY; R-COMPARE-MODEL-TOKENS; R-NO-FABRICATION; R-COMPARE-PRICE-CONTEXT; R-INTERFACE-GUARDS.output-size.
# DEPENDS: compare_connector.identity (size tokens); compare_connector.models_output (CompareResponse, MarketOffer, SourceOutcome); mcp_core.logging (stderr events); stdlib json, re, collections.abc, dataclasses.
# SCHEMA: CompareResponse keeps its fields, types and meanings; this module only fills them.
# LINKS: M-COMPARE; M-COMPARE-DECIDE; M-LOGGING; R-STDOUT; R-NO-MCP-LOGGING.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# ResponseOptions: Carry what the tool knew before the gather into the decision.
# _dedupe: Merge duplicate offers while preserving source identity.
# _has_bundle_phrase_prefix: Recognize up to three local modifiers between an explicit bundle cue and this accessory.
# _has_bundle_phrase_suffix: Recognize bounded lexical noun/modifier tails without crossing accessory clauses.
# _has_unrequested_marker: Match Unicode words/phrases after occurrence-local bundle/negation checks.
# _looks_like_an_accessory/_looks_like_another_condition: Share relevance rules for warnings and comparability.
# _size_mismatch_warning: Name the excluded offers and the asked sizes once per call.
# _relevance_warnings: Flag a cheapest offer that probably answers a different question.
# _ranks_in_rubles: Whether an offer may enter the cheapest-in-rubles ranking.
# ConditionalPriceDecision: What one offer's everyday and conditional prices say about its rank.
# decide_conditional_price: The one rule for an everyday price beside a conditional one.
# _conditional_price_note: Name the excluded amount, not the offer whose everyday price may still rank.
# pick_card_candidate: The cheapest tile worth one card read (numeric id, an amount, not an accessory).
# _mixed_regions_notes: Name the regions when answered sources priced in different ones.
# _omitted_note: Name how many offers the size budget left out and how to get them.
# _fit_answer: Leave whole offers out from the end until the answer fits ANSWER_CHAR_LIMIT characters.
# build_response: Rank eligible RUB offers with per-source completeness and warnings.
# END_MODULE_MAP

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from mcp_core.logging import log_event

from compare_connector.identity import extract_size_tokens, format_size_tokens, model_mismatch, sizes_conflict
from compare_connector.models_output import CompareResponse, MarketOffer, SourceOutcome


@dataclass(frozen=True)
class ResponseOptions:
    """What the tool knew before the gather, handed to the decision.

    `active` names the marketplaces that were queried, `missing` the ones that
    were asked for but are not installed, `in_stock_only` says whether only
    offers with confirmed stock may rank, and `server_version` is the version
    the answer reports.
    """

    active: list[str]
    missing: list[str]
    in_stock_only: bool = False
    server_version: str = ""


def _dedupe(offers: Iterable[MarketOffer]) -> list[MarketOffer]:
    """Drop repeats of the same listing, retaining explicitly distinct variants.

    A marketplace can return the same product twice — colour variants sharing
    an id, a pagination overlap — and every copy would otherwise rank
    separately. That inflates total_offers and, worse, lets one listing occupy
    both the cheapest slot and the runner-up, making a single offer look like
    two independent confirmations of a price.

    Order is preserved so the per-source ordering the marketplaces chose
    survives. Offers with no product_id cannot be compared this way and are all
    kept: dropping them on a blank key would silently merge distinct listings.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[MarketOffer] = []
    for offer in offers:
        if not offer.product_id:
            out.append(offer)
            continue
        key = (offer.source, offer.product_id, offer.variant_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


# START_BLOCK_RELEVANCE_POLICY
# Words that mark a listing as something you buy *for* a product rather than
# the product itself. A search for "iphone 15" matches a case, a screen
# protector and a cable, all of them cheaper than the phone, and whichever is
# cheapest becomes the headline answer. Observed live in July 2026: a query for
# iPhone 15 ranked a 34 224 ₽ listing above genuine 52 049 ₽ ones.
_ACCESSORY_MARKERS = (
    "чехол",
    "чехол-книжка",
    "бампер",
    "накладка",
    "защитное стекло",
    "стекло",
    "плёнка",
    "пленка",
    "кабель",
    "адаптер",
    "зарядка",
    "зарядное",
    "держатель",
    "ремешок",
    "подставка",
    "наклейка",
    "case",
    "cover",
    "screen protector",
    "cable",
    "charger",
    "strap",
)


# Words that mark a listing as a different *condition* of the product, not a
# different product. This is the case the accessory list above misses entirely
# and the one that actually bit: checked live in July 2026, the cheap iPhone 15
# rows on Wildberries were "Восстановленный" and "Витринный образец" — a
# refurbished phone and a display unit, ranked against new ones from other
# marketplaces. Nothing in the title looks like an accessory, and the price is
# not low enough to trip a median check. Only the wording gives it away.
_CONDITION_MARKERS = (
    "восстановленный",
    "восстановленная",
    "витринный образец",
    "витринный",
    "уценка",
    "уценённый",
    "уцененный",
    "б/у",
    "бывший в употреблении",
    "как новый",
    "refurbished",
    "renewed",
    "pre-owned",
    "used",
    "open box",
)


def _marker_pattern(markers: tuple[str, ...]) -> re.Pattern[str]:
    """Unicode word boundaries and flexible whitespace inside marker phrases."""
    phrases = [
        r"\s+".join(re.escape(word) for word in marker.split()) for marker in sorted(markers, key=len, reverse=True)
    ]
    return re.compile(r"(?<!\w)(?:" + "|".join(phrases) + r")(?!\w)", re.IGNORECASE)


_NEGATION_PREFIX = re.compile(r"(?<!\w)(?:не|not)\s+$", re.IGNORECASE)
_BUNDLE_PREFIX = re.compile(r"(?<!\w)(?:в\s+комплекте|with(?:\s+(?:a|an))?)\s*[:—-]?\s*$", re.IGNORECASE)
_BUNDLE_SUFFIX = re.compile(r"\s*(?:[:—-]\s*)?в\s+комплекте(?!\w)", re.IGNORECASE)

# Complete an adjective-only accessory head or a known functional noun phrase.
# These are lexical categories, not product-title or query exceptions.
_BUNDLE_NOUN_COMPLEMENTS = {
    "зарядное": ("устройство", "приспособление"),
    "кабель": ("питания",),
    "адаптер": ("питания",),
}
_BUNDLE_PHRASE_WORD = re.compile(r"[^\S\r\n]+(?P<word>\w+(?:-\w+)*)(?![\w-])")
_BUNDLE_PHRASE_PREFIX_WORD = re.compile(r"(?<![\w-])(?P<word>\w+(?:-\w+)*)[^\S\r\n]+\Z")
_BUNDLE_MODIFIER = re.compile(
    r"(?:(?:силиконов|кожан|пластиков|резинов|металлическ|нейлонов|тканев|"
    r"прозрачн|защитн|сетев|беспроводн|оригинальн|универсальн|компактн|"
    r"магнитн|черн|чёрн|бел|красн|син|зелён|зелен|сер)"
    r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие)|"
    r"usb(?:-[abc])?|type-[abc]|micro-usb|hdmi|lightning|displayport|thunderbolt|"
    r"silicone|leather|plastic|rubber|metal|nylon|transparent|protective|"
    r"wireless|original|universal|compact|magnetic|black|white|red|blue|green|grey)",
    re.IGNORECASE,
)


def _has_bundle_phrase_prefix(text: str, match: re.Match[str], pattern: re.Pattern[str]) -> bool:
    """R-COMPARE-BUNDLE-NOUN-PHRASES: admit at most three preceding modifiers.

    Walk backwards from this accessory only through adjacent recognized words,
    then require the original explicit bundle cue. Unknown words, punctuation,
    relation words and another accessory cannot connect it to an earlier cue.
    """
    position = match.start()
    for _ in range(3):
        word_match = _BUNDLE_PHRASE_PREFIX_WORD.search(text, 0, position)
        if word_match is None or pattern.match(text, word_match.start("word")):
            return False
        if not _BUNDLE_MODIFIER.fullmatch(word_match.group("word")):
            return False
        position = word_match.start("word")
        if _BUNDLE_PREFIX.search(text, 0, position):
            return True
    return False


def _has_bundle_phrase_suffix(text: str, match: re.Match[str], pattern: re.Pattern[str]) -> bool:
    """R-COMPARE-BUNDLE-NOUN-PHRASES: admit at most three known phrase words.

    A noun complement belongs to its accessory head and can occur only once.
    Unknown words, punctuation, relation words and another accessory stop the
    extension; they must not borrow a later clause's 'в комплекте'.
    """
    position = match.end()
    noun_complements = _BUNDLE_NOUN_COMPLEMENTS.get(match.group().casefold(), ())
    for _ in range(3):
        word_match = _BUNDLE_PHRASE_WORD.match(text, position)
        if word_match is None or pattern.match(text, word_match.start("word")):
            return False
        word = word_match.group("word").casefold()
        if word in noun_complements:
            noun_complements = ()
        elif not _BUNDLE_MODIFIER.fullmatch(word):
            return False
        position = word_match.end()
        if _BUNDLE_SUFFIX.match(text, position):
            return True
    return False


def _positive_marker_text(text: str, pattern: re.Pattern[str], *, bundles: bool) -> str:
    """Mask only explicitly negated or locally bundled marker occurrences.

    Longer phrases match first, so a bundled 'защитное стекло' also masks its
    nested 'стекло'. Another positive marker in the same title stays evidence.
    """

    def replace(match: re.Match[str]) -> str:
        negated = _NEGATION_PREFIX.search(text, 0, match.start())
        bundled = bundles and (
            _BUNDLE_PREFIX.search(text, 0, match.start())
            or _has_bundle_phrase_prefix(text, match, pattern)
            or _BUNDLE_SUFFIX.match(text, match.end())
            or _has_bundle_phrase_suffix(text, match, pattern)
        )
        return " " * len(match.group()) if negated or bundled else match.group()

    return pattern.sub(replace, text)


# START_CONTRACT: _has_unrequested_marker
# PURPOSE: R-COMPARE-RELEVANCE-BOUNDARIES; R-COMPARE-BUNDLE-NOUN-PHRASES: detect positive unrequested markers.
# INPUTS: Untrusted title/query, fixed markers; accessory bundles admit up to three known prefix modifiers or suffix noun/modifier words.
# OUTPUTS: True only when an unmasked title marker is absent from the equally checked query.
# SIDE_EFFECTS: None; no source I/O, title exceptions or ranking mutations.
def _has_unrequested_marker(title: str, query: str, markers: tuple[str, ...], *, bundles: bool = False) -> bool:
    pattern = _marker_pattern(markers)
    title = _positive_marker_text(title, pattern, bundles=bundles)
    query = _positive_marker_text(query, pattern, bundles=bundles)
    for marker in markers:
        word_pattern = _marker_pattern((marker,))
        if word_pattern.search(title) and not word_pattern.search(query):
            return True
    return False


# END_CONTRACT: _has_unrequested_marker


def _looks_like_another_condition(title: str, query: str) -> bool:
    """Detect an unrequested used/refurbished/display condition on word boundaries.

    Direct 'не'/'not' negation is not condition evidence, including in a query:
    asking for 'не б/у' must not make a genuinely used listing comparable.
    """
    return _has_unrequested_marker(title, query, _CONDITION_MARKERS)


def _looks_like_an_accessory(title: str, query: str) -> bool:
    """Detect an unrequested accessory, not a substring or an explicit bundle.

    'with [a/an] <accessory>', 'в комплекте: <accessory>' and '<accessory> в
    комплекте' describe a bundled accessory, not the listing itself. Up to three
    known modifiers may follow a bundle prefix; up to three known noun complements
    and modifiers may precede the bundle suffix. The extensions stop at punctuation,
    unknown words and other accessories; direct cue separators remain allowed.
    These rules also apply to queries, without exempting whole titles or treating
    a bundled accessory as a standalone-accessory request.
    """
    return _has_unrequested_marker(title, query, _ACCESSORY_MARKERS, bundles=True)


def _size_mismatch_warning(count: int, stated: Iterable[frozenset[str]], asked: frozenset[str]) -> str:
    """One warning per call, not per offer: every warning costs the agent tokens."""
    verb = "offer states" if count == 1 else "offers state"
    sizes = format_size_tokens({size for group in stated for size in group})
    return f"identity_mismatch: {count} {verb} {sizes}, query asks {format_size_tokens(asked)}; not comparable"


def _relevance_warnings(query: str, priced: list[MarketOffer]) -> list[str]:
    """Flag a cheapest offer that probably answers a different question.

    Warnings never remove an offer or change raw cheapest. The same bounded
    accessory/condition checks steer cheapest_comparable; an explicit bundle or
    negation is not positive evidence. The median check only warns, because an
    uncalibrated price threshold must never hide a real bargain.
    """
    if not priced:
        return []
    warnings: list[str] = []
    cheapest = priced[0]

    if _looks_like_an_accessory(cheapest.title, query):
        warnings.append(
            f"relevance: the cheapest offer ({cheapest.source}, {cheapest.price_rub} ₽) is titled "
            f"{cheapest.title[:60]!r}, which reads as an accessory rather than {query!r} itself — verify before quoting it"
        )

    if _looks_like_another_condition(cheapest.title, query):
        warnings.append(
            f"condition: the cheapest offer ({cheapest.source}, {cheapest.price_rub} ₽) is titled "
            f"{cheapest.title[:60]!r} — that is a used, refurbished or display unit, so it is not "
            f"comparable with new goods from the other marketplaces"
        )

    # A price far under the middle of the pack is usually a different product,
    # a different configuration or a grey import. Half the median is
    # conservative on purpose: normal cross-marketplace spread does not reach it.
    if len(priced) >= 3:
        prices = sorted(offer.price_rub or 0.0 for offer in priced)
        median = prices[len(prices) // 2]
        low = cheapest.price_rub or 0.0
        if median > 0 and low < median * 0.5:
            warnings.append(
                f"price_outlier: the cheapest offer is {low} ₽ against a median of {median} ₽ across "
                f"{len(priced)} offers — check it is the same product and configuration"
            )
    return warnings


# END_BLOCK_RELEVANCE_POLICY


# START_BLOCK_PRICE_POLICY
def _ranks_in_rubles(offer: MarketOffer) -> bool:
    """Whether an offer may enter the cheapest-in-rubles ranking.

    R-COMPARE-CONDITIONAL-PRICE: an explicitly conditional price never ranks,
    even if an adapter fills price_rub with it. Unmarked source prices retain
    their existing treatment; no missing price or foreign currency may rank.
    """
    return offer.price_rub is not None and offer.currency == "rub" and offer.price_kind in (None, "", "regular")


def _conditional_price_note(source: str, product_id: str, price: float, kind: str, currency: str = "rub") -> str:
    """Name the excluded amount, not the offer whose everyday price may still rank."""
    condition = kind
    if kind == "wallet":
        condition = "WB Wallet"
    elif kind == "card":
        condition = "Ozon card" if source == "ozon" else "card"
    elif kind == "subscription":
        # Yandex's yaBank price: paying with Yandex Pay («Пэй»), earlier Yandex Plus (audit 2026-11-17).
        condition = "paying with Yandex Pay" if source == "yandex_market" else "paid subscription"
    return (
        f"conditional_price: {source}/{product_id or '?'}: {price:g} {currency.upper()} "
        f"requires {condition}; not ranked"
    )


@dataclass(frozen=True)
class ConditionalPriceDecision:
    """What one offer's everyday and conditional prices say about its rank.

    `rank_price_rub` is the price the offer may rank by — None when the offer has
    no everyday price — and `note` is the one sentence the agent needs about the
    conditional amount, None when there is nothing to say about it.
    """

    rank_price_rub: float | None
    note: str | None


# START_CONTRACT: decide_conditional_price
# PURPOSE: R-COMPARE-CONDITIONAL-PRICE: one rule for an everyday price beside a conditional one.
# INPUTS: Untrusted source and id, the everyday and the conditional amount, the condition's kind.
# OUTPUTS: The price the offer may rank by and at most one note about the conditional amount.
# SIDE_EFFECTS: None; no source I/O, no ranking mutation, no invented price.
def decide_conditional_price(
    source: str,
    product_id: str,
    everyday_price_rub: float | None,
    conditional_price_rub: float | None,
    kind: str,
    currency: str = "rub",
) -> ConditionalPriceDecision:
    """Decide what an everyday price beside a conditional one means.

    An everyday price plus a *lower* conditional one ranks by the everyday price
    and says that the cheaper amount exists (`info:conditional_price_available`):
    the buyer without the card, the wallet or the subscription still pays the
    everyday price, and the amount is context, not a doubt about the data.

    A conditional price *alone* is not an everyday price: the offer does not rank
    and the amount is named in a real warning (`conditional_price`), because a
    buyer who cannot use that condition must not be quoted it.

    A conditional amount that is not lower than the everyday price is no bargain,
    so it stays unmentioned; the everyday price is the whole answer then.
    """
    if conditional_price_rub is None:
        return ConditionalPriceDecision(everyday_price_rub, None)
    if everyday_price_rub is None:
        note = _conditional_price_note(source, product_id, conditional_price_rub, kind, currency)
        return ConditionalPriceDecision(None, note)
    if conditional_price_rub >= everyday_price_rub:
        return ConditionalPriceDecision(everyday_price_rub, None)
    label = "Yandex Pay" if (source, kind) == ("yandex_market", "subscription") else kind
    note = (
        f"info:conditional_price_available: {source}/{product_id or '?'}: "
        f"{conditional_price_rub:g} {currency.upper()} with {label}"
    )
    return ConditionalPriceDecision(everyday_price_rub, note)


# END_CONTRACT: decide_conditional_price


def pick_card_candidate(query: str, tiles: Iterable[tuple[str, str, float | None, bool | None]]) -> str | None:
    """The product id of the cheapest tile worth one card read, or None.

    R-COMPARE-CONDITIONAL-PRICE.ozon-card: ``tiles`` are (product id, title,
    conditional amount, in stock). A tile without a numeric id or an amount
    cannot be read or ranked; a tile marked out of stock, an unrequested accessory
    or another condition would spend the one read on an offer the comparison
    would not count as the product asked for.
    """
    best: tuple[float, str] | None = None
    for product_id, title, amount, in_stock in tiles:
        if amount is None or not product_id.isdigit() or in_stock is False:
            continue
        if _looks_like_an_accessory(title, query) or _looks_like_another_condition(title, query):
            continue
        if best is None or amount < best[0]:
            best = (amount, product_id)
    return best[1] if best else None


# END_BLOCK_PRICE_POLICY


# Source warnings that keep their own prefix in the comparison's warnings: an info: note
# stays a note, and conditional_price: keeps the documented top-level shape (v2.9.4)
# whether the adapter or build_response's safety net named the conditional price.
_UNWRAPPED_PREFIXES = ("info:", "conditional_price:")


def _mixed_regions_notes(outcomes: list[SourceOutcome]) -> list[str]:
    """R-COMPARE-PRICE-CONTEXT: one info note when answered sources name different regions.

    Context, not a doubt about the data: prices from two regions are still
    ranked together, so the agent is told which source priced where. A region
    that is only digits is one source's own id (Avito's location_id): two
    sources' ids live in different namespaces, so it is shown but never compared.
    """
    known = [
        outcome
        for outcome in outcomes
        if outcome.status in {"ok", "unhealthy"} and outcome.region and not outcome.region.lstrip("-").isdigit()
    ]
    if len({outcome.region for outcome in known}) < 2:
        return []
    return ["info:mixed_regions: " + ", ".join(f"{outcome.source}={outcome.region}" for outcome in known)]


# R-INTERFACE-GUARDS.output-size: the answer an agent reads stays within this many
# characters of JSON (Claude Code warns at about 10k tokens and cuts at 25k).
ANSWER_CHAR_LIMIT = 40_000


def _json_size(value: CompareResponse | MarketOffer) -> int:
    return len(json.dumps(value.model_dump(mode="json"), ensure_ascii=False))


def _omitted_note(omitted: int, total: int, limit: int) -> str:
    return (
        f"info:offers_omitted: {omitted} of {total} offers left out from the end of the ranked list "
        f"to keep the answer under {limit} characters; for more, ask fewer sources or a lower per_source_limit"
    )


def _fit_answer(response: CompareResponse, limit: int = ANSWER_CHAR_LIMIT) -> CompareResponse:
    """Leave whole offers out from the end of the ranked list until the answer fits.

    The end of the list holds the unpriced offers, then the dearest ones; nothing is
    cut inside an offer. total_offers keeps the full count, cheapest and
    cheapest_comparable keep their own copies, and one info note names how many offers
    were left out and how to get them.
    """
    size = _json_size(response)
    if size <= limit:
        return response
    offers = list(response.offers)
    # json.dumps joins list items with ", ": each offer costs its JSON plus 2, and so
    # does the note (sized at its longest count) in the warnings list.
    costs = [_json_size(offer) + 2 for offer in offers]
    size += len(json.dumps(_omitted_note(response.total_offers, response.total_offers, limit), ensure_ascii=False)) + 2
    while offers and size > limit:
        offers.pop()
        size -= costs.pop()
    while True:
        note = _omitted_note(response.total_offers - len(offers), response.total_offers, limit)
        fitted = response.model_copy(update={"offers": offers, "warnings": [*response.warnings, note]})
        if not offers or _json_size(fitted) <= limit:
            return fitted
        offers = offers[:-1]


def build_response(
    query: str,
    results: list[tuple[SourceOutcome, list[MarketOffer]]],
    options: ResponseOptions,
) -> CompareResponse:
    """Rank the gathered offers and build the comparison answer within its size budget.

    Pure: it reads what the sources answered and returns the ranked list, the winners
    and the warnings; no marketplace is contacted, nothing goes to stdout. `query` is
    the stripped query the tool validated, `results` the (outcome, offers) pairs the
    gather returned, `options` what the tool knew before the gather.
    """
    outcomes = [outcome for outcome, _ in results]
    offers: list[MarketOffer] = _dedupe(offer for _, source_offers in results for offer in source_offers)

    for name in options.missing:
        outcomes.append(
            SourceOutcome(
                source=name,
                status="not_installed",
                detail="connector package is not installed in this environment",
            )
        )

    # Rank on everyday ruble prices only. The currency check is explicit rather
    # than implied by price_rub being None: an adapter that starts populating
    # price_rub from a foreign-currency field should fail this filter, not
    # quietly win the comparison.
    priced_all = [offer for offer in offers if _ranks_in_rubles(offer)]
    priced = sorted(
        (offer for offer in priced_all if not options.in_stock_only or offer.in_stock is True),
        key=lambda offer: offer.price_rub or 0.0,
    )
    # Unpriced offers keep their place at the end rather than being dropped:
    # "found, but not priced in rubles" is information, and for Taobao the yuan
    # price still rides along in price_native.
    unpriced = [
        offer
        for offer in offers
        if not _ranks_in_rubles(offer) or (options.in_stock_only and offer.in_stock is not True)
    ]
    ranked = priced + unpriced
    foreign = [offer for offer in offers if offer.currency != "rub"]

    ok_sources = [outcome.source for outcome in outcomes if outcome.status == "ok"]
    failed_sources = [outcome.source for outcome in outcomes if outcome.status != "ok"]

    cheapest = priced[0] if priced else None
    # A size the query states and the title contradicts is explicit text, not a
    # heuristic, so it keeps the offer out of the like-for-like candidate rather
    # than merely warning about it; so does a title missing a model word or number
    # the query names (R-COMPARE-MODEL-TOKENS). The offer keeps its rank and may
    # remain plain cheapest; only cheapest_comparable skips it.
    query_sizes = extract_size_tokens(query)
    comparable: list[MarketOffer] = []
    size_excluded: list[frozenset[str]] = []
    for offer in priced:
        if _looks_like_an_accessory(offer.title, query) or _looks_like_another_condition(offer.title, query):
            continue
        if model_mismatch(query, offer.title):
            continue
        title_sizes = extract_size_tokens(offer.title)
        if sizes_conflict(query_sizes, title_sizes):
            size_excluded.append(title_sizes)
            continue
        comparable.append(offer)
    cheapest_comparable = comparable[0] if comparable else None
    price_spread = None
    if len(priced) >= 2:
        low, high = priced[0].price_rub, priced[-1].price_rub
        if low and high:
            price_spread = round(high - low, 2)

    warnings = _mixed_regions_notes(outcomes)
    if failed_sources:
        warnings.append(
            f"partial: {len(failed_sources)} of {len(outcomes)} marketplaces did not answer "
            f"({', '.join(failed_sources)}) — prices below cover only {', '.join(ok_sources) or 'none'}"
        )
    if not priced:
        warnings.append("no_prices: no marketplace returned a usable price for this query")
    if options.in_stock_only:
        excluded = len(priced_all) - len(priced)
        if excluded:
            warnings.append(f"stock_filter: excluded {excluded} priced offer(s) without confirmed stock")
        if not priced_all:
            warnings.append("stock_filter: no priced offers were returned")
    # A source's info: note stays a note: the prefix is what tells context from a
    # doubt about the data (mcp_core.health_warnings), and source_outcomes still
    # attributes it. _UNWRAPPED_PREFIXES says which source warnings keep their prefix.
    for outcome in outcomes:
        warnings.extend(
            warning if warning.startswith(_UNWRAPPED_PREFIXES) else f"source_warning:{outcome.source}: {warning}"
            for warning in outcome.warnings
        )
    # An adapter that knows a conditional price names it with the shared helper
    # (an everyday price beside a lower conditional one gets the info note, a
    # conditional price alone the warning). This loop is the safety net for an
    # offer that arrives marked conditional without a note: the same sentence
    # twice costs the agent twice.
    already_named = {warning for outcome in outcomes for warning in outcome.warnings}
    for offer in offers:
        kind, native = offer.price_kind, offer.price_native
        if kind and kind != "regular" and native is not None:
            decision = decide_conditional_price(offer.source, offer.product_id, None, native, kind, offer.currency)
            if decision.note and decision.note not in already_named:
                warnings.append(decision.note)
    warnings.extend(_relevance_warnings(query, priced))
    if size_excluded:
        warnings.append(_size_mismatch_warning(len(size_excluded), size_excluded, query_sizes))
    if cheapest is not None and cheapest_comparable is not None and cheapest is not cheapest_comparable:
        warnings.append(
            "comparable_price: the raw cheapest listing is an accessory, a different condition, variant or model; "
            "cheapest_comparable is the safer like-for-like candidate"
        )
    elif cheapest is not None and cheapest_comparable is None:
        warnings.append(
            "comparable_price: every priced listing looks like an accessory, a different condition, variant or model"
        )
    if foreign:
        currencies = ", ".join(sorted({offer.currency for offer in foreign}))
        warnings.append(
            f"foreign_currency: {len(foreign)} offer(s) are priced in {currencies}, not roubles — "
            f"their price is in price_native and they are excluded from the ranking and from cheapest"
        )

    log_event(
        "compare_prices.done",
        query=query,
        offers=len(ranked),
        ok_sources=len(ok_sources),
        failed=len(failed_sources),
    )
    return _fit_answer(
        CompareResponse(
            query=query,
            sources_queried=options.active,
            sources_ok=ok_sources,
            complete=not failed_sources,
            total_offers=len(ranked),
            cheapest=cheapest,
            cheapest_comparable=cheapest_comparable,
            price_spread_rub=price_spread,
            offers=ranked,
            source_outcomes=outcomes,
            warnings=warnings,
            server_version=options.server_version,
        )
    )
