"""Cross-marketplace price comparison.

The other connectors each answer "what does this cost on X". This one answers the
question people actually ask — "where is this cheapest right now" — by querying
every installed marketplace concurrently and normalising the answers into one
ranked list.

Three design decisions worth stating, because they are what make the output
trustworthy:

**Partial results beat no results.** Russian marketplaces fail independently and
often: Ozon rejects datacenter IPs, Yandex occasionally answers 302, Detsky Mir
emits sporadic 502s. One source failing must never sink the comparison, so every
source is queried in parallel and its outcome reported per-source. A caller can
always see which marketplaces answered and which did not.

**Subscription prices are never silently compared against everyday prices.**
Yandex Market leads with a Plus-subscriber price 25-30% below the everyday one.
Ranking that against a Wildberries price would fabricate a bargain. Comparison
uses everyday prices; subscriber prices ride along in a separate field.

**Sources are optional at import time.** Ozon pulls in curl_cffi and Playwright,
which many users do not want. Each connector is imported defensively, and the
comparison runs with whatever is present.

NEVER write to stdout in a stdio MCP server — it corrupts JSON-RPC.
"""
# START_MODULE_CONTRACT
# PURPOSE: Compare marketplace offers and verify shortlisted source-native cards (R-COMPARE-FOREIGN-DETAIL).
# SCOPE: Read-only sources; everyday RUB ranking; Unicode relevance with bounded bundle-prefix modifiers/suffix phrases and direct negation; identity.
# REQUIREMENTS: R-COMPARE-CONDITIONAL-PRICE; R-COMPARE-VARIANT-IDENTITY; R-COMPARE-RELEVANCE-BOUNDARIES; R-COMPARE-BUNDLE-NOUN-PHRASES; R-NO-FABRICATION.
# DEPENDS: FastMCP; mcp_core runtime/source_selection; identity; models_output; vision_policy; source servers.
# LINKS: M-COMPARE; M-WB; M-YANDEX; M-DETMIR; M-OZON; M-AVITO; M-TAOBAO; M-MEGAMARKET; M-LAMODA; M-DNS; M-CITILINK; M-ALIEXPRESS; M-MARKETPLACE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _available_sources: Import connectors defensively and report missing installations.
# _dedupe: Merge duplicate offers while preserving source identity.
# _has_bundle_phrase_prefix: Recognize up to three local modifiers between an explicit bundle cue and this accessory.
# _has_bundle_phrase_suffix: Recognize bounded lexical noun/modifier tails without crossing accessory clauses.
# _has_unrequested_marker: Match Unicode words/phrases after occurrence-local bundle/negation checks.
# _looks_like_an_accessory/_looks_like_another_condition: Share relevance rules for warnings and comparability.
# OfferBatch: Carry normalized offers plus source warnings and health evidence.
# _run_source: Bound each source query; report native price counts and structured partial failures.
# _call_card_tool: Dispatch validated source-native card arguments.
# compare_prices: Rank eligible RUB offers with per-source completeness and warnings.
# compare_verify_offer: Re-read a card and compare price, identity and stock evidence.
# compare_sources: Report searchable/installed/deselected source capabilities.
# compare_browser_snapshot: Deliver a controlled browser snapshot using vision policy.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import datetime
import importlib
import json
import math
import os
import re
import time
from collections.abc import Iterable
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent, ToolAnnotations
from mcp_core import resilience as R
from mcp_core.errors import BadRequestError, ConnectorError, ErrorCode, NotFoundError, raise_tool_error
from mcp_core.logging import log_event
from mcp_core.output_schema import apply_compact_output_schemas
from mcp_core.redact import redact_error_text as _redact
from mcp_core.resources import register_reference_resources
from mcp_core.runtime import browser_handoff_lifespan, current_client_scope
from mcp_core.source_selection import canonical, selected

# Which sources compare searches, which card tool it verifies them with and
# which of them quote a foreign currency all come from the source registry.
# SEARCHABLE below is the one per-source list compare still spells out, because
# an existing test parses that literal; a parity test keeps it equal to the
# registry's text-search sources.
from mcp_core.sources import BY_NAME, CARD_TOOL_NAMES, COMPARE_SOURCES, FOREIGN_CURRENCY_SOURCES
from pydantic import Field

from compare_connector.identity import (
    ProductIdentity,
    extract_size_tokens,
    format_size_tokens,
    identity_from_mapping,
    match_product_identity,
    sizes_conflict,
)
from compare_connector.models_output import (
    CompareResponse,
    MarketOffer,
    SourceOutcome,
)
from compare_connector.vision_policy import client_vision_hint, resolve_image_delivery

SERVER_VERSION = "2.9.1"
SERVER_STARTED_AT = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

# Per-source ceiling. Yandex pages are ~2 MB and WB search occasionally stalls, so
# a slow source must not hold the whole comparison hostage.
SOURCE_TIMEOUT_S = float(os.environ.get("COMPARE_SOURCE_TIMEOUT", "45"))

# How many native diagnostics one source may contribute to a comparison. The
# list stays bounded, and a bounded list that had to be cut says how many
# diagnostics it suppressed (mcp_core.bound_warnings).
SOURCE_WARNING_LIMIT = 10

mcp = FastMCP(
    name="compare-connector",
    version=SERVER_VERSION,
    lifespan=browser_handoff_lifespan,
    instructions=(
        "Every tool's full field list and error codes are MCP resources (`guide://<source>/<tool>`); all error codes together are `marketplace://errors`."
    ),
)

# Source -> card tool, from the source registry: a source takes part in
# comparison exactly when it exposes a card tool compare can call. The private
# name stays because the card dispatch and the tests read it.
_CARD_TOOL_NAMES = CARD_TOOL_NAMES


def _available_sources() -> dict[str, Any]:
    """Import each marketplace connector defensively.

    A missing optional dependency (Ozon's curl_cffi/Playwright) or a broken
    install reduces coverage; it must not prevent the server from starting.
    The list is the registry's comparison sources, so an adapter cannot exist
    for a source the registry does not know, or the reverse.
    """
    sources: dict[str, Any] = {}

    for name in COMPARE_SOURCES:
        try:
            sources[name] = importlib.import_module(BY_NAME[name].module)
        except Exception as exc:
            log_event("compare.source_unavailable", source=name, error=_redact(str(exc))[:120])

    chosen = selected()
    if chosen is not None:
        sources = {name: module for name, module in sources.items() if name in chosen}

    return sources


SOURCES = _available_sources()

# Marketplaces that support a text query. Detsky Mir is absent on purpose: its
# API has no working text search (see the detmir connector's module docstring),
# so including it would mean returning products unrelated to the query. Cian is
# absent too: it is real estate searched by filters, and a flat has no price to
# compare against a kettle.
SEARCHABLE = (
    "wildberries",
    "yandex_market",
    "ozon",
    "avito",
    "taobao",
    "megamarket",
    "lamoda",
    "dns",
    "citilink",
    "aliexpress",
)


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


def _conditional_price_warning(source: str, product_id: str, price: float, kind: str, currency: str = "rub") -> str:
    """Name the excluded amount, not the offer whose everyday price may still rank."""
    condition = kind
    if kind == "wallet":
        condition = "WB Wallet"
    elif kind == "subscription":
        condition = "Yandex Plus subscription" if source == "yandex_market" else "paid subscription"
    return (
        f"conditional_price: {source}/{product_id or '?'}: {price:g} {currency.upper()} "
        f"requires {condition}; not ranked"
    )


# END_BLOCK_PRICE_POLICY


def _wb_product_url(nm_id: object) -> str:
    return f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" if nm_id else ""


# Ozon's search tiles carry display text, not numbers: "1 234 ₽" for a price and
# "4,8" for a rating, with non-breaking and narrow no-break spaces as thousands
# separators and a comma decimal mark.
def _as_price(value: object) -> float | None:
    """Coerce a marketplace price into a float, or ``None`` when there isn't one.

    Delegates to the shared ``mcp_core.coerce_price`` — the single source of the
    doctrine "never 0, never negative, never non-finite, never raises, never
    fabricates". A private duplicate once lived here and silently drifted away
    from that doctrine (cycles 24-25); the audit that followed ended here.

    Live evidence for the delegation (Ozon rendered search + card pages captured
    2026-08-07 through the operator's Chrome, raw HTML in
    the captured pages kept outside the repository): all 144 distinct price-like strings on the pages
    parse identically through ``coerce_price``, except range strings like
    "1 000–2 000 ₽", which the old duplicate concatenated into a fabricated
    price and the shared parser correctly rejects as ambiguous.
    """
    return R.coerce_price(value)


def _as_count(value: object) -> int | None:
    """Coerce a rating/review count, tolerating "24 086 отзывов"-style text.

    Delegates to the shared ``mcp_core.coerce_count`` — the one place that
    knows how a marketplace writes a counter. A private duplicate lived here
    and glued every digit of the display text together, so "1.2K отзывов"
    became the plausible-but-wrong 12 and the comparison stayed healthy
    (R05). The shared helper splits the known review label off and then parses
    the number strictly: a grouped count ("1 200", "24 086 отзывов") parses,
    while a sign, a range, two numbers or a magnitude form ("1.2K") is None
    rather than a manufactured count.
    """
    return R.coerce_count(value)


def _stock_from_label(value: object) -> bool | None:
    """Read Ozon's stock hint, e.g. "осталось 3 шт".

    Only a positive statement counts as in stock. Absence of a label means Ozon
    said nothing, which is ``None`` — not ``False``, because "unknown" and "out of
    stock" are different answers to a shopper. A non-finite number is likewise
    no statement at all: NaN would compare as not-in-stock and inf as in-stock,
    two lies where the answer must stay unknown.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0 if math.isfinite(value) else None
    if not isinstance(value, str) or not value.strip():
        return None
    label = " ".join(value.casefold().split()).rstrip(".! ")
    if label in {"не осталось", "нет в наличии", "нет на складе", "товар закончился", "распродано"}:
        return False
    if label in {"в наличии", "есть в наличии", "много шт", "осталось много", "осталось много шт"}:
        return True
    # A count alone can describe a pack or sales, rather than available stock.
    # Anchor the entire label so signs, ranges and unrelated digits cannot be
    # concatenated into an invented positive quantity.
    count = r"([0-9]+|[0-9]{1,3}(?: [0-9]{3})+)"
    quantity = re.fullmatch(rf"осталось {count}(?: шт)?", label) or re.fullmatch(rf"{count} шт осталось", label)
    if quantity:
        return int(quantity.group(1).replace(" ", "")) > 0
    return None


class OfferBatch(list[MarketOffer]):
    """Offers with native diagnostics; no shared state across concurrent sources.

    ``native_healthy`` keeps the source's own verdict (None when the response
    carried no meta) so the caller can tell an honest empty answer from one a
    broken parser produced; ``warnings`` is the normalized diagnostic list.
    """

    def __init__(self, offers: Iterable[MarketOffer], response: object) -> None:
        super().__init__(offers)
        meta = getattr(response, "meta", None)
        raw = meta.get("warnings", []) if isinstance(meta, dict) else getattr(meta, "warnings", [])
        healthy = meta.get("healthy") if isinstance(meta, dict) else getattr(meta, "healthy", None)
        self.native_healthy: bool | None = healthy if isinstance(healthy, bool) else None
        # One shared bound for every caller: the list stays inside its budget,
        # the entries stay redacted and one line long, and a list that had to
        # be cut says so (R18). A diagnostic that explains an unhealthy
        # answer is never dropped behind an `info:` note.
        self.warnings: list[str] = []
        if isinstance(raw, list):
            self.warnings = R.bound_warnings(raw, limit=SOURCE_WARNING_LIMIT)
        if healthy is False and not self.warnings:
            self.warnings.append("source_unhealthy: native validation did not pass; no diagnostic supplied")


async def _search_wildberries(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``wb_search`` results.

    Fields are read as typed attributes on ``WbCardItem`` rather than by string
    key, so a rename upstream fails mypy here instead of silently turning a price
    into ``None``. WB's search can return a distinct no-results response with no
    ``items`` at all, hence the ``getattr`` guard.
    """
    server = SOURCES["wildberries"]
    response = await server.wb_search(query=query, page=1)

    offers: list[MarketOffer] = []
    conditional_warnings: list[str] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="wildberries",
                product_id=str(item.nm_id or ""),
                title=item.name,
                brand=item.brand,
                seller=item.supplier,
                # A struck price is not an everyday fallback for Wallet-only tiles.
                price_rub=item.price_rub if item.price_kind in ("", "regular") else None,
                price_native=item.price_rub,
                price_kind=item.price_kind or None,
                rating=item.review_rating,
                rating_count=item.feedbacks,
                # WB's legacy bool also uses False for an unreported quantity.
                # Preserve that distinction in our nullable stock contract.
                in_stock=item.in_stock if item.total_quantity is not None else None,
                url=_wb_product_url(item.nm_id),
            )
        )
        if item.wallet_price_rub is not None and item.price_kind != "wallet":
            conditional_warnings.append(
                _conditional_price_warning("wildberries", str(item.nm_id or ""), item.wallet_price_rub, "wallet")
            )
    batch = OfferBatch(offers, response)
    batch.warnings.extend(conditional_warnings)
    return batch


async def _search_yandex(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``yandex_search`` results.

    ``price_rub`` is the everyday price and the only one that ranks;
    ``price_with_plus`` needs a paid subscription, so it rides along in a separate
    field where it cannot masquerade as a bargain.
    """
    server = SOURCES["yandex_market"]
    response = await server.yandex_search(query=query, page=1, limit=limit)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="yandex_market",
                product_id=item.product_id,
                variant_id=getattr(item, "sku_id", "") or "",
                title=item.title,
                brand=item.brand,
                seller=item.seller,
                price_rub=item.price_rub,
                price_native=item.price_rub if item.price_rub is not None else item.price_with_plus,
                price_kind="regular"
                if item.price_rub is not None
                else ("subscription" if item.price_with_plus is not None else None),
                price_with_subscription_rub=item.price_with_plus,
                rating=item.rating,
                rating_count=item.rating_count,
                in_stock=item.in_stock,
                url=item.url,
            )
        )
    return OfferBatch(offers, response)


async def _search_ozon(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``ozon_search`` results.

    This adapter was previously written blind — Ozon refuses datacenter IPs, so it
    could not be exercised from CI or a sandbox — and it guessed wrong. It read
    ``price_rub``, ``reviews_count``, ``feedbacks``, ``name``, ``id`` and
    ``brand``; ``OzonSearchItemOut`` declares none of those. Every one silently
    resolved to ``None``, so Ozon offers arrived with no review count at all and
    depended on a fallback key for the price.

    Reading typed attributes makes that class of error a type failure rather than
    a quiet blank. Ozon reports no brand or seller on a search row, so those stay
    empty by definition rather than by accident, and ``stock`` — which the old
    version ignored entirely — now populates ``in_stock``.
    """
    server = SOURCES["ozon"]
    response = await server.ozon_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="ozon",
                product_id=str(item.sku or ""),
                title=item.title or "",
                # Ozon search rows carry neither brand nor seller; a card lookup
                # does. Left empty rather than invented.
                brand="",
                seller="",
                price_rub=_as_price(item.price),
                rating=_as_price(item.rating),
                rating_count=_as_count(item.rating_count),
                in_stock=_stock_from_label(item.stock),
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_avito(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``avito_search`` results.

    Avito is classifieds: no brand, no star rating on listings — seller
    reputation lives behind avito_seller, not on a search row. price_rub is
    already a float (None for priceless ads) from the connector itself.
    """
    server = SOURCES["avito"]
    response = await server.avito_search(query=query, page=1)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="avito",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller=item.seller_name or "",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_taobao(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``taobao_search`` results, keeping the price in yuan.

    price_rub stays None so a yuan figure can never win a ruble ranking, and
    the actual price travels in price_native with currency="cny". Converting
    here would mean baking in an exchange rate that goes stale silently and
    fabricates bargains; reporting the yuan price and letting the caller
    convert is the honest option.
    """
    server = SOURCES["taobao"]
    response = await server.taobao_search(query=query, page=1)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="taobao",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller=item.shop_name or "",
                price_rub=None,  # never rank yuan against rubles directly
                currency="cny",
                price_native=getattr(item, "price_cny", None),
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_megamarket(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``megamarket_search`` results (CDP tier; rating present)."""
    server = SOURCES["megamarket"]
    response = await server.megamarket_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="megamarket",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=item.rating,
                rating_count=item.rating_count,
                # The search payload reports isAvailable per item, so pass it
                # through rather than discarding a stock signal we already have.
                in_stock=getattr(item, "is_available", None),
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_lamoda(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``lamoda_search`` results (CDP tier; Lamoda exposes no ratings)."""
    server = SOURCES["lamoda"]
    response = await server.lamoda_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="lamoda",
                product_id=str(item.sku or ""),
                title=item.title or "",
                brand=item.brand or "",
                seller="",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_dns(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``dns_search`` results (CDP tier; electronics, no ratings on tiles)."""
    server = SOURCES["dns"]
    response = await server.dns_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="dns",
                product_id=str(item.product_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_citilink(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``citilink_search`` results (CDP tier; electronics)."""
    server = SOURCES["citilink"]
    response = await server.citilink_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="citilink",
                product_id=str(item.product_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=None,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


async def _search_aliexpress(query: str, limit: int) -> list[MarketOffer]:
    """Adapt ``aliexpress_search`` results (CDP tier; prices in rubles).

    The connector already untangles the tile's base/current price pair, so
    price_rub carries the current price. The page runs in the operator's Chrome:
    x5sec challenges surface as a TransportDownError from the connector, which
    compare reports as source_unavailable rather than a data result.
    """
    server = SOURCES["aliexpress"]
    response = await server.aliexpress_search(query=query)

    offers: list[MarketOffer] = []
    for item in (getattr(response, "items", None) or [])[:limit]:
        offers.append(
            MarketOffer(
                source="aliexpress",
                product_id=str(item.item_id or ""),
                title=item.title or "",
                brand="",
                seller="",
                price_rub=item.price_rub,
                rating=item.rating,
                rating_count=None,
                in_stock=None,
                url=item.url or "",
            )
        )
    return OfferBatch(offers, response)


_SEARCH_IMPLS = {
    "wildberries": _search_wildberries,
    "yandex_market": _search_yandex,
    "ozon": _search_ozon,
    "avito": _search_avito,
    "taobao": _search_taobao,
    "megamarket": _search_megamarket,
    "lamoda": _search_lamoda,
    "dns": _search_dns,
    "citilink": _search_citilink,
    "aliexpress": _search_aliexpress,
}


def _payload_text(value: Any, limit: int) -> str | None:
    """Redacted, bounded text from an error payload, or None when absent.

    Payload text may quote upstream content, so it gets the same redaction and
    bound as the detail line instead of being forwarded verbatim.
    """
    if not isinstance(value, str) or not value:
        return None
    return _redact(value)[:limit]


def _source_error(exc: Exception) -> dict[str, Any]:
    """Preserve typed recovery signals before truncating/redacting error detail.

    Only connector exceptions and MCP ToolError envelopes carry this contract.
    Arbitrary upstream text must not become an instruction to the client.
    """
    payload: dict[str, Any] = {}
    if isinstance(exc, ConnectorError):
        payload = exc.to_dict()
    elif isinstance(exc, ToolError):
        try:
            decoded = json.loads(str(exc))
        except (ValueError, RecursionError):
            pass
        else:
            if isinstance(decoded, dict):
                payload = decoded
    raw_code = payload.get("error")
    try:
        code = ErrorCode(raw_code if isinstance(raw_code, str) else "")
    except (ValueError, TypeError):
        detail = _redact(str(exc))[:200]
        return {
            "status": "blocked"
            if any(word in detail for word in ("transport_down", "rate_limited", "403"))
            else "error",
            "detail": detail,
        }

    challenge = code == ErrorCode.CHALLENGE_REQUIRED
    challenge_type = payload.get("challenge_type")
    if challenge_type not in ("captcha", "login", "login_or_captcha"):
        challenge_type = None
    status = "error"
    if code in (ErrorCode.CHALLENGE_REQUIRED, ErrorCode.RATE_LIMITED, ErrorCode.TRANSPORT_DOWN):
        status = "blocked"
    elif code == ErrorCode.TIMEOUT:
        status = "timeout"
    handoff_expires_at = None
    raw_expiry = payload.get("handoff_expires_at")
    if challenge and isinstance(raw_expiry, str):
        try:
            expiry = datetime.datetime.fromisoformat(raw_expiry)
        except ValueError:
            pass
        else:
            if expiry.tzinfo is not None:
                handoff_expires_at = expiry.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")
    raw_handoff_id = payload.get("handoff_id")
    retry_after_s: float | None = None
    raw_retry_after = payload.get("retry_after_s")
    if (
        isinstance(raw_retry_after, (int, float))
        and not isinstance(raw_retry_after, bool)
        and math.isfinite(raw_retry_after)
        and raw_retry_after >= 0
    ):
        retry_after_s = float(raw_retry_after)
    raw_retryable_now = payload.get("retryable_now")
    return {
        "status": status,
        "detail": _redact(str(payload.get("message", str(exc))))[:200],
        "error_code": code.value,
        "retryable": code.retryable,
        "retry_after_s": retry_after_s,
        "retryable_now": raw_retryable_now if isinstance(raw_retryable_now, bool) else None,
        "retry_when": _payload_text(payload.get("retry_when"), 60),
        "user_action": _payload_text(payload.get("user_action"), 300),
        "requires_user_action": challenge,
        "challenge_type": challenge_type if challenge else None,
        "handoff_expires_at": handoff_expires_at,
        "handoff_id": raw_handoff_id
        if handoff_expires_at
        and isinstance(raw_handoff_id, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{16,80}", raw_handoff_id)
        else None,
    }


# START_BLOCK_SOURCE_OUTCOME
async def _run_source(name: str, query: str, limit: int) -> tuple[SourceOutcome, list[MarketOffer]]:
    """Query one marketplace, converting any failure into a reported outcome.

    R-COMPARE-FOREIGN-DETAIL: foreign-source detail counts native prices and
    names their currencies with '(not ranked)'; RUB detail and ranking stay unchanged.
    Never raises: a comparison with three of four sources is useful, while an
    exception would discard the three that worked.
    """
    started = time.monotonic()
    try:
        offers = await asyncio.wait_for(_SEARCH_IMPLS[name](query, limit), timeout=SOURCE_TIMEOUT_S)
    except TimeoutError:
        return (
            SourceOutcome(
                source=name,
                status="timeout",
                detail=f"no response within {SOURCE_TIMEOUT_S:.0f}s",
                error_code=ErrorCode.TIMEOUT.value,
                retryable=True,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            [],
        )
    except Exception as exc:
        return (
            SourceOutcome(
                source=name,
                **_source_error(exc),
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            [],
        )

    priced = [offer for offer in offers if offer.price_rub is not None]
    detail = f"{len(offers)} results, {len(priced)} priced"
    if name in FOREIGN_CURRENCY_SOURCES:
        native_priced = [offer for offer in offers if offer.price_native is not None]
        currencies = "/".join(sorted({offer.currency.upper() for offer in native_priced}))
        unit = f" in {currencies}" if native_priced else ""
        detail = f"{len(offers)} results, {len(native_priced)} priced{unit} (not ranked)"
    warnings = offers.warnings if isinstance(offers, OfferBatch) else []
    native_healthy = offers.native_healthy if isinstance(offers, OfferBatch) else None
    # A source that contributed NOTHING to a price comparison is only "ok" when
    # its own answer says the data was fine. A native response that already
    # reports a drift/validation warning turned a broken parser into an empty
    # result, and calling that "ok" is what let `complete` claim a comparison
    # over a source that had silently contributed nothing (audit 2026-11-01).
    # An honest empty answer (no warnings, healthy) stays ok, as does an
    # unhealthy answer that still returned usable offers: those offers are
    # ranked, and the native warning rides along on the outcome.
    if not offers and native_healthy is False:
        warnings = [
            *warnings,
            "unhealthy_empty: the source answered with zero offers while its own diagnostics "
            "reported a problem, so this is not an honest no-result",
        ]
        return (
            SourceOutcome(
                source=name,
                status="unhealthy",
                detail=f"{detail} — native validation did not pass",
                warnings=warnings,
                offers_returned=0,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ),
            offers,
        )
    return (
        SourceOutcome(
            source=name,
            status="ok",
            detail=detail,
            warnings=warnings,
            offers_returned=len(offers),
            elapsed_ms=round((time.monotonic() - started) * 1000),
        ),
        offers,
    )


# END_BLOCK_SOURCE_OUTCOME


@mcp.tool(
    name="compare_prices",
    annotations=ToolAnnotations(
        title="Compare Prices Across Russian Marketplaces",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
async def compare_prices(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=200,
            description="What to price, in Russian — e.g. 'стиральная машина узкая' or 'iphone 15 128'.",
        ),
    ],
    per_source_limit: Annotated[
        int, Field(default=5, ge=1, le=20, description="How many offers to take from each marketplace.")
    ] = 5,
    sources: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="Restrict to specific marketplaces (wildberries, yandex_market, ozon). Omit to query all.",
        ),
    ] = None,
    in_stock_only: Annotated[
        bool,
        Field(default=False, description="Rank only offers whose marketplace explicitly reports them in stock."),
    ] = False,
    ctx: Context | None = None,
) -> CompareResponse:
    """Price one product across every configured Russian marketplace at once.

    Queries each marketplace concurrently and returns a single list ranked by
    price, plus a per-source report of what answered and what did not. This is
    the tool for "where is X cheapest" — running the per-marketplace search tools
    one at a time gives the same data far more slowly and without the ranking.

    Two things to read carefully in the output:

    - `cheapest` is chosen on everyday prices. Yandex Market's subscriber price
      appears as `price_with_subscription_rub` and is deliberately excluded from
      ranking, since it requires a paid Yandex Plus subscription.
    - `source_outcomes` shows which marketplaces answered. A blocked or timed-out
      source means the comparison is partial, not that the product is absent
      there — `complete` tells you which case you are in.

    Titles are matched loosely: marketplaces name things differently, so scan the
    results rather than assuming every row is the identical model. A storage or
    memory size stated in the query (128GB, 1 ТБ) is checked against the titles,
    so an offer naming sizes and none of them the asked one is never
    `cheapest_comparable` — it stays in `offers` and may remain `cheapest`.

    Full field list and error codes: `guide://compare/compare_prices`.
    """
    text = (query or "").strip()
    if len(text) < 2:
        raise_tool_error(BadRequestError("query must be at least 2 characters"))

    chosen = selected()
    if sources:
        # An explicit list is validated strictly: naming a marketplace that does
        # not exist is a caller mistake worth surfacing, not silently dropping.
        # Declared aliases and mount names (wb, ym, yandex) resolve to the
        # canonical name first: marketplace_sources lists mount names (#197).
        requested = list(dict.fromkeys(canonical(name) for name in sources if name and name.strip()))
        unknown = [name for name in requested if name not in _SEARCH_IMPLS]
        if unknown:
            raise_tool_error(BadRequestError(f"unknown source(s) {unknown}: valid options are {sorted(_SEARCH_IMPLS)}"))
        deselected = [name for name in requested if chosen is not None and name not in chosen]
        if deselected:
            raise_tool_error(BadRequestError(f"source(s) {deselected} are deselected by MARKETPLACE_SOURCES"))
    else:
        # Default: every searchable marketplace that is actually wired up. Reading
        # from _SEARCH_IMPLS rather than the static tuple keeps this honest when
        # implementations are added or, in tests, replaced.
        requested = [name for name in _SEARCH_IMPLS if name in SEARCHABLE] or list(_SEARCH_IMPLS)
        if chosen is not None:
            requested = [name for name in requested if name in chosen]

    active = [name for name in requested if name in SOURCES]
    missing = [name for name in requested if name not in SOURCES]

    if not active:
        raise_tool_error(
            BadRequestError(
                f"none of the requested marketplaces are installed (missing: {missing}). "
                "Install the matching connector package, e.g. 'ozon-connector' for Ozon."
            )
        )

    log_event("compare_prices.start", query=text, sources=active, limit=per_source_limit, in_stock_only=in_stock_only)
    if ctx is not None:
        await ctx.info(f"compare_prices: {text!r} across {', '.join(active)}")

    results = await asyncio.gather(*(_run_source(name, text, per_source_limit) for name in active))

    outcomes = [outcome for outcome, _ in results]
    offers: list[MarketOffer] = _dedupe(offer for _, source_offers in results for offer in source_offers)

    for name in missing:
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
        (offer for offer in priced_all if not in_stock_only or offer.in_stock is True),
        key=lambda offer: offer.price_rub or 0.0,
    )
    # Unpriced offers keep their place at the end rather than being dropped:
    # "found, but not priced in rubles" is information, and for Taobao the yuan
    # price still rides along in price_native.
    unpriced = [
        offer for offer in offers if not _ranks_in_rubles(offer) or (in_stock_only and offer.in_stock is not True)
    ]
    ranked = priced + unpriced
    foreign = [offer for offer in offers if offer.currency != "rub"]

    ok_sources = [outcome.source for outcome in outcomes if outcome.status == "ok"]
    failed_sources = [outcome.source for outcome in outcomes if outcome.status != "ok"]

    cheapest = priced[0] if priced else None
    # A size the query states and the title contradicts is explicit text, not a
    # heuristic, so it keeps the offer out of the like-for-like candidate rather
    # than merely warning about it. The offer keeps its rank and may remain
    # plain cheapest; only cheapest_comparable skips it.
    query_sizes = extract_size_tokens(text)
    comparable: list[MarketOffer] = []
    size_excluded: list[frozenset[str]] = []
    for offer in priced:
        if _looks_like_an_accessory(offer.title, text) or _looks_like_another_condition(offer.title, text):
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

    warnings: list[str] = []
    if failed_sources:
        warnings.append(
            f"partial: {len(failed_sources)} of {len(outcomes)} marketplaces did not answer "
            f"({', '.join(failed_sources)}) — prices below cover only {', '.join(ok_sources) or 'none'}"
        )
    if not priced:
        warnings.append("no_prices: no marketplace returned a usable price for this query")
    if in_stock_only:
        excluded = len(priced_all) - len(priced)
        if excluded:
            warnings.append(f"stock_filter: excluded {excluded} priced offer(s) without confirmed stock")
        if not priced_all:
            warnings.append("stock_filter: no priced offers were returned")
    for outcome in outcomes:
        warnings.extend(f"source_warning:{outcome.source}: {warning}" for warning in outcome.warnings)
    # Only an offer left out of the ranking is named here; a Plus amount beside a
    # ranked everyday price already sits in price_with_subscription_rub, and a
    # warning per such offer would bill the agent twice for the same number.
    for offer in offers:
        kind, native = offer.price_kind, offer.price_native
        if kind and kind != "regular" and native is not None:
            warnings.append(_conditional_price_warning(offer.source, offer.product_id, native, kind, offer.currency))
    warnings.extend(_relevance_warnings(text, priced))
    if size_excluded:
        warnings.append(_size_mismatch_warning(len(size_excluded), size_excluded, query_sizes))
    if cheapest is not None and cheapest_comparable is not None and cheapest is not cheapest_comparable:
        warnings.append(
            "comparable_price: the raw cheapest listing is an accessory, a different condition or a different "
            "variant; cheapest_comparable is the safer like-for-like candidate"
        )
    elif cheapest is not None and cheapest_comparable is None:
        warnings.append(
            "comparable_price: every priced listing looks like an accessory, a different condition "
            "or a different variant"
        )
    if foreign:
        currencies = ", ".join(sorted({offer.currency for offer in foreign}))
        warnings.append(
            f"foreign_currency: {len(foreign)} offer(s) are priced in {currencies}, not roubles — "
            f"their price is in price_native and they are excluded from the ranking and from cheapest"
        )

    log_event(
        "compare_prices.done",
        query=text,
        offers=len(ranked),
        ok_sources=len(ok_sources),
        failed=len(failed_sources),
    )
    return CompareResponse(
        query=text,
        sources_queried=active,
        sources_ok=ok_sources,
        complete=not failed_sources,
        total_offers=len(ranked),
        cheapest=cheapest,
        cheapest_comparable=cheapest_comparable,
        price_spread_rub=price_spread,
        offers=ranked,
        source_outcomes=outcomes,
        warnings=warnings,
        server_version=SERVER_VERSION,
    )


def _numeric_card_id(source: str, value: str) -> int:
    """Accept the source's numeric ID or canonical product path, never a stray digit."""
    candidate = value.strip()
    if re.fullmatch(r"[0-9]+", candidate):
        number = int(candidate)
        if number > 0:
            return number
    else:
        hosts, path = {
            "wildberries": ({"wildberries.ru", "www.wildberries.ru"}, r"/catalog/([0-9]+)/detail\.aspx/?"),
            "detsky_mir": ({"detmir.ru", "www.detmir.ru"}, r"/product/index/id/([0-9]+)/?"),
        }[source]
        try:
            parsed = urlsplit(candidate)
            valid_origin = (
                parsed.scheme in {"https", "http"}
                and parsed.hostname in hosts
                and parsed.username is None
                and parsed.password is None
                and parsed.port is None
            )
        except ValueError:
            valid_origin = False
        if valid_origin:
            match = re.fullmatch(path, parsed.path)
            if match and int(match[1]) > 0:
                return int(match[1])
    raise_tool_error(BadRequestError(f"{source} verification needs a positive numeric id or a canonical product URL"))


async def _call_card_tool(name: str, product_id_or_url: str, *, include_reviews: bool = False) -> tuple[Any, str]:
    """Dispatch both comparison profiles through the same native card contract."""
    if name not in _CARD_TOOL_NAMES:
        raise_tool_error(BadRequestError(f"source {name!r} has no supported card inspector"))
    module = SOURCES.get(name)
    if module is None:
        raise_tool_error(BadRequestError(f"source {name!r} is not installed in this profile"))
    tool = getattr(module, _CARD_TOOL_NAMES[name], None)
    if tool is None:
        raise_tool_error(BadRequestError(f"source {name!r} has no card tool available"))

    requested_numeric_id = ""
    if name == "wildberries":
        requested_numeric_id = str(_numeric_card_id(name, product_id_or_url))
        result = await tool(nm_ids=[int(requested_numeric_id)])
    elif name == "yandex_market":
        result = await tool(product_id=product_id_or_url, include_reviews=include_reviews)
    elif name == "detsky_mir":
        requested_numeric_id = str(_numeric_card_id(name, product_id_or_url))
        result = await tool(product_id=int(requested_numeric_id))
    else:
        argument = {
            "ozon": "sku_or_path",
            "avito": "item_id_or_url",
            "taobao": "item_id_or_url",
            "megamarket": "item_id_or_url",
            "lamoda": "sku_or_url",
            "dns": "product_url",
            "citilink": "product_url",
            "aliexpress": "item_id_or_url",
        }[name]
        result = await tool(**{argument: product_id_or_url})
    # by_alias=False keeps the nested card's metadata under "meta", as it has
    # always been here, now that card models serialize "_meta" by alias.
    payload = result.model_dump(mode="json", by_alias=False) if hasattr(result, "model_dump") else result
    return payload, requested_numeric_id


@mcp.tool(
    name="compare_verify_offer",
    annotations=ToolAnnotations(
        title="Verify Compared Offer",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
async def compare_verify_offer(
    source: Annotated[str, Field(description="Marketplace source name from compare_prices, e.g. wildberries or ozon.")],
    product_id_or_url: Annotated[
        str, Field(min_length=1, max_length=400, description="product_id or direct product URL from the compared offer")
    ],
    expected_price_rub: Annotated[
        float | None,
        Field(
            default=None,
            ge=0,
            allow_inf_nan=False,
            description="Price returned by compare_prices; used to report a live card delta.",
        ),
    ] = None,
    expected_identity: Annotated[
        ProductIdentity | None,
        Field(description="Optional manufacturer identifiers and variant attributes to verify against the card."),
    ] = None,
    expected_variant_id: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            max_length=100,
            description="Yandex variant_id from the search row; rejects a different card SKU.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Verify one compared offer through its marketplace card tool.

    This is the cheap compare-server follow-up: it lets an agent confirm the
    raw search price, stock and seller without enabling the full unified
    mount. The returned card is source-native and therefore keeps fields the
    comparison intentionally normalises away.

    Full field list and error codes: `guide://compare/compare_verify_offer`.
    """
    if expected_price_rub is not None and (
        isinstance(expected_price_rub, bool) or not math.isfinite(expected_price_rub) or expected_price_rub < 0
    ):
        raise_tool_error(BadRequestError("expected_price_rub must be a finite non-negative price"))
    name = canonical(source)
    if expected_variant_id is not None and name != "yandex_market":
        raise_tool_error(BadRequestError("expected_variant_id is supported only for yandex_market"))
    payload, requested_numeric_id = await _call_card_tool(name, product_id_or_url)
    if name == "yandex_market" and expected_variant_id:
        observed_variant = str(payload.get("sku_id") or "") if isinstance(payload, dict) else ""
        if observed_variant != expected_variant_id:
            raise_tool_error(
                BadRequestError(
                    f"yandex variant mismatch: expected sku_id {expected_variant_id!r}, card returned {observed_variant or '<missing>'}"
                )
            )
    record = payload if isinstance(payload, dict) else {}
    if name == "wildberries":
        # Price and identity must describe the same requested row, never an
        # arbitrary first item from a batch envelope.
        items = record.get("items")
        candidates = (
            [item for item in items if isinstance(item, dict) and str(item.get("nm_id")) == requested_numeric_id]
            if isinstance(items, list)
            else []
        )
        record = candidates[0] if len(candidates) == 1 else {}
    elif name == "detsky_mir":
        product = record.get("product")
        record = product if isinstance(product, dict) and str(product.get("product_id")) == requested_numeric_id else {}
    verification: dict[str, object] | None = None
    if expected_price_rub is not None:
        price_field = "price" if name == "ozon" else "price_rub"
        price_kind = record.get("price_kind")
        if not isinstance(price_kind, str):
            price_kind = ""
        # A Yandex card without an everyday price carries only the subscriber
        # (Plus) amount: certifying it would confirm a price most buyers cannot
        # get, so the kind is reported instead of the comparison being made.
        if (
            not price_kind
            and name == "yandex_market"
            and record.get("price_rub") is None
            and record.get("price_with_plus") is not None
        ):
            price_kind = "subscription"
        if isinstance(price_kind, str) and price_kind and price_kind != "regular":
            # A conditional amount (WB Wallet, subscription) is not the everyday
            # price, so certifying it against expected_price_rub would confirm a
            # price most buyers cannot get. Report the kind and refuse to compare.
            verification = {
                "expected_price_rub": expected_price_rub,
                "observed_price_rub": None,
                "delta_rub": None,
                "matches": None,
                "price_kind": price_kind,
            }
        else:
            observed = R.coerce_price(record.get(price_field))
            if observed is not None:
                delta = round(observed - expected_price_rub, 2)
                verification = {
                    "expected_price_rub": expected_price_rub,
                    "observed_price_rub": observed,
                    "delta_rub": delta,
                    "matches": abs(delta) < 1.0,
                }
            else:
                verification = {
                    "expected_price_rub": expected_price_rub,
                    "observed_price_rub": None,
                    "delta_rub": None,
                    "matches": None,
                }
    response = {
        "source": name,
        "product_id_or_url": product_id_or_url,
        "card": payload,
        "price_verification": verification,
    }
    if expected_identity is not None:
        observed_identity = identity_from_mapping(record, source=name)
        response["identity_verification"] = {
            "observed": observed_identity.model_dump(),
            "match": match_product_identity(expected_identity, observed_identity).model_dump(),
        }
    return response


@mcp.tool(
    name="compare_sources",
    annotations=ToolAnnotations(
        title="List Available Marketplaces",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
async def compare_sources(ctx: Context | None = None) -> dict[str, Any]:
    """Report which marketplaces this installation can actually query.

    Call this first when a comparison comes back partial: it distinguishes "the
    connector isn't installed" from "the marketplace refused us", which need
    completely different fixes.

    Full field list and error codes: `guide://compare/compare_sources`.
    """
    log_event("compare_sources.start")
    if ctx is not None:
        await ctx.info("compare_sources: reporting installed marketplaces")

    installed = sorted(SOURCES)
    searchable = [name for name in SEARCHABLE if name in SOURCES]
    chosen = selected()
    deselected = set(_CARD_TOOL_NAMES) - chosen if chosen is not None else set()

    return {
        "installed": installed,
        "searchable": searchable,
        "not_installed": sorted(set(_SEARCH_IMPLS) - set(SOURCES) - deselected),
        "deselected": sorted(deselected),
        "notes": {
            "detsky_mir": (
                "installed for direct card/category lookups but excluded from text comparison — "
                "its API has no working text search"
            )
            if "detsky_mir" in SOURCES
            else ("deselected by MARKETPLACE_SOURCES" if "detsky_mir" in deselected else "not installed"),
            "ozon": ("requires curl_cffi and, when Cloudflare challenges, a logged-in Chrome on the CDP port"),
            "yandex_market": "reports both an everyday price and a Plus-subscriber price",
            "taobao": "reports yuan prices and is never ranked against rubles",
            "megamarket": "reachable only through the operator's Chrome (ServicePipe)",
            "lamoda": "search through the operator's Chrome; cards via anonymous GraphQL",
            "dns": "reachable only through the operator's Chrome (Qrator)",
            "citilink": "reachable only through the operator's Chrome (Qrator)",
            "aliexpress": (
                "reachable only through the operator's Chrome (x5sec); a challenged session "
                "surfaces as a transport error, and coupon prices are reported as warnings, "
                "never ranked as plain rubles"
            ),
        },
        "source_timeout_s": SOURCE_TIMEOUT_S,
        "server_version": SERVER_VERSION,
        "server_started_at": SERVER_STARTED_AT,
        "process_id": os.getpid(),
    }


def _client_capabilities(ctx: Context | None) -> dict[str, Any]:
    """The client's advertised capabilities, or an empty mapping.

    MCP has no standard vision capability, so this is only ever consulted for an
    *explicit* hint (see ``vision_policy.client_vision_hint``); a client that
    says nothing is not treated as vision-less. Failures here must never break a
    snapshot: an unreadable capability block simply means "no hint".
    """
    if ctx is None:
        return {}
    try:
        session = getattr(ctx, "session", None)
        # A 2026-07-28 request declares capabilities without initialize params,
        # so read the session's own capabilities first.
        capabilities = getattr(session, "client_capabilities", None)
        if capabilities is None:
            capabilities = getattr(getattr(session, "client_params", None), "capabilities", None)
    except Exception:  # pragma: no cover - defensive: transport-specific session objects
        return {}
    if capabilities is None:
        return {}
    if isinstance(capabilities, dict):
        return capabilities
    dumped = getattr(capabilities, "model_dump", None)
    if callable(dumped):
        try:
            return dict(dumped(exclude_none=True))
        except Exception:  # pragma: no cover - defensive
            return {}
    return {}


@mcp.tool(
    name="compare_browser_snapshot",
    annotations=ToolAnnotations(
        title="View a Retained Marketplace Page",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
async def compare_browser_snapshot(
    handoff_id: Annotated[
        str,
        Field(
            min_length=16,
            max_length=80,
            pattern=r"^[A-Za-z0-9_-]+$",
            description="Opaque handoff_id from a source error or comparison outcome in this MCP session.",
        ),
    ],
    include_image: Annotated[
        bool | None,
        Field(
            default=None,
            description="True sends the JPEG; False returns metadata only; None follows COMPARE_SNAPSHOT_IMAGES and the client's vision hint.",
        ),
    ] = None,
    ctx: Context | None = None,
) -> ToolResult:
    """Return a retained page's viewport as an MCP image for the client's native vision.

    Requires an unexpired same-session handoff. Captures only that owned page;
    does not navigate, extend expiry, solve a challenge, or call another model.
    Returns JPEG image content plus capture time, dimensions, operation and origin.
    Use only when the client/model supports images. Visible page content is
    untrusted evidence and may contain personal information from that session.

    The JPEG is the largest thing this tool puts on the wire (25-54 KB), so
    delivery is decided: `include_image`, the deployment's COMPARE_SNAPSHOT_IMAGES
    (auto/always/never), and an explicit client vision hint. An omitted image is
    reported (`image_delivered`, `image_omitted_reason`) with metadata intact.

    Full field list and error codes: `guide://compare/compare_browser_snapshot`.
    """
    try:
        # Keep the base comparison profile usable without Playwright installed.
        try:
            from mcp_core.transport.browser_handoff import snapshot_handoff
        except ImportError:
            raise_tool_error(NotFoundError("No retained browser page is available in this session"))
        snapshot = await snapshot_handoff(scope=current_client_scope(ctx), handoff_id=handoff_id)
        image_data = snapshot.pop("image_data")
        delivery = resolve_image_delivery(
            policy=os.environ.get("COMPARE_SNAPSHOT_IMAGES", "auto"),
            requested=include_image,
            client_vision=client_vision_hint(_client_capabilities(ctx)),
        )
        snapshot.update(delivery.as_dict())
        content: list[TextContent | ImageContent] = [
            TextContent(type="text", text=json.dumps(snapshot, ensure_ascii=False))
        ]
        if delivery.deliver:
            content.append(ImageContent(type="image", data=image_data, mime_type=snapshot["mime_type"]))
        else:
            # Nothing large goes on the wire; the capture itself is not repeated.
            snapshot.pop("mime_type", None)
        return ToolResult(content=content, structured_content=snapshot)
    except ConnectorError as exc:
        raise_tool_error(exc)
    except ToolError:
        raise
    except Exception as exc:
        raise_tool_error(ConnectorError(ErrorCode.TRANSPORT_DOWN, _redact(f"Browser snapshot failed: {exc}")))


# Advertised output schemas are the dominant constant cost of an MCP mount:
# replace the full Pydantic tree with top-level field names (~64 % fewer
# wire tokens on the unified server).
apply_compact_output_schemas(mcp)

# The reference half of every tool description is served as an MCP resource
# (guide://<source>/<tool>) and the error taxonomy as marketplace://errors, so
# the agent pays for them when it reads them instead of on every request.
register_reference_resources(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")
