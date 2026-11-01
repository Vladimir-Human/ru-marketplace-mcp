"""Reference shape signature for the Megamarket search parser.

The golden is the fingerprint of ``_parse_items`` over the captured search
payload (``tests/fixtures/search_live.json``, captured 2026-08-07; provenance
recorded alongside), measured with ``mcp_core.resilience.shape_signature``.

``SEARCH_REQUIRED_FAMILIES`` is the runtime tripwire: ``megamarket_search``
feeds the RAW payload to ``mcp_core.resilience.shape_drift_warning`` and the
response's ``meta.warnings`` carries
``shape_drift: megamarket_search: missing <family>`` when a whole
parser-critical alias family vanishes. The parsed items cannot serve that
guard — ``_parse_items`` emits every key unconditionally, so a key-presence
check on items would never fire — while the raw envelope binds through the
alias families below. A rename WITHIN a family is tolerated; the loss of every
member is drift.

The families mirror the parser exactly: ``_parse_items`` accepts four
container keys (``items`` / ``goods`` / ``products`` / ``data.items``) and
``pick`` searches the ``goods`` / ``favoriteOffer`` / item scopes for each
alias, so a family lists every container x scope x alias combination. The
generator keeps the registry in step with those parser tables.

Never edit by hand: regenerate by re-running the parser over a fresh capture.
"""

from __future__ import annotations

from collections.abc import Iterable

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "container_found:bool",
    "items[].is_available:bool",
    "items[].item_id:str",
    "items[].old_price_rub:null",
    "items[].price_rub:float",
    "items[].rating:float",
    "items[].rating_count:int",
    "items[].title:str",
    "items[].url:str",
    "total:int",
)

# The container keys _parse_items accepts, in the order it tries them.
_CONTAINERS: tuple[str, ...] = ("items", "goods", "products", "data.items")
# The nested scopes pick() searches, outermost first.
_SCOPES: tuple[str, ...] = ("", "goods.", "favoriteOffer.")


def _family(*aliases: str) -> tuple[str, ...]:
    """Every container x scope path an alias can be read from."""
    return tuple(f"{container}[].{scope}{alias}" for container in _CONTAINERS for scope in _SCOPES for alias in aliases)


# Key families the parser binds through, as alternatives (mirroring pick's
# alias tables): drift means the WHOLE family vanished, not a single alias.
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (
    _family("goodsId", "id", "goods_id", "itemId"),
    _family("title", "name", "goodsName"),
    _family("finalPrice", "price", "currentPrice"),
    _family("webUrl", "url", "link"),
)


def missing_required_families(signature: Iterable[str]) -> list[tuple[str, ...]]:
    """The required families that have no member present in the signature."""
    entries = set(signature)
    return [
        family
        for family in SEARCH_REQUIRED_FAMILIES
        if not any(entry.startswith(f"{path}:") for entry in entries for path in family)
    ]
