"""Pure helpers the compare adapters share: number and stock coercion, the offer batch, card-id checks.

Moved verbatim out of server.py (move-only); server.py re-imports every name, so
``server._as_price`` and the other helpers keep working for callers and tests.
"""
# START_MODULE_CONTRACT
# PURPOSE: Pure helpers for the compare adapters (no network): prices, counts, stock labels, OfferBatch, card ids.
# SCOPE: Coercion of native fields into MarketOffer values; bounded source warnings; source-native card id checks.
# DEPENDS: mcp_core resilience/errors/sources; models_output.
# LINKS: M-COMPARE; R-COMPARE-CONDITIONAL-PRICE; R-WARNING-BUDGET; R-NAV-HOSTS.invariant.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# OfferBatch: Carry normalized offers plus source warnings and health evidence.
# _numeric_card_id: Accept a numeric card id or a URL on the source's own hosts.
# END_MODULE_MAP

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from mcp_core import resilience as R
from mcp_core.errors import BadRequestError, raise_tool_error
from mcp_core.sources import BY_NAME

from compare_connector.models_output import MarketOffer

# How many native diagnostics one source may contribute to a comparison. The
# list stays bounded, and a bounded list that had to be cut says how many
# diagnostics it suppressed (mcp_core.bound_warnings).
SOURCE_WARNING_LIMIT = 10


def _wb_product_url(nm_id: object) -> str:
    return f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx" if nm_id else ""


# Native search prices are numeric or None; shared coercion also accepts older
# display strings, including localized decimal marks and grouping spaces.
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
    broken parser produced; ``warnings`` is the normalized diagnostic list;
    ``region`` is the region the source's own answer names, empty when it names none.
    """

    def __init__(self, offers: Iterable[MarketOffer], response: object, *, region: str = "") -> None:
        super().__init__(offers)
        self.region = region
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


def _numeric_card_id(source: str, value: str) -> int:
    """Accept the source's numeric ID or canonical product path, never a stray digit."""
    candidate = value.strip()
    if re.fullmatch(r"[0-9]+", candidate):
        number = int(candidate)
        if number > 0:
            return number
    else:
        path = {
            "wildberries": r"/catalog/([0-9]+)/detail\.aspx/?",
            "detsky_mir": r"/product/index/id/([0-9]+)/?",
            # The url a compare_prices Yandex offer carries (ssr.card_url).
            "yandex_market": r"/card/[^/]+/([0-9]+)/?",
        }[source]
        try:
            parsed = urlsplit(candidate)
            valid_origin = (
                parsed.scheme in {"https", "http"}
                and parsed.hostname in BY_NAME[source].page_hosts
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
