"""Reference shape signature for the Ozon composer search parser.

The golden is the fingerprint of ``_search_items_from_payload`` over the
captured composer payload (``tests/fixtures/search_composer_live.json``,
captured 2026-08-07; provenance recorded alongside), measured with
``mcp_core.resilience.shape_signature``.

``SEARCH_REQUIRED_FAMILIES`` is the runtime tripwire: ``ozon_search`` feeds
the parsed tile dicts (wrapped as ``{"items": [...]}``) to
``mcp_core.resilience.shape_drift_warning``, and the response's
``meta.warnings`` carries ``shape_drift: ozon_search: missing <family>`` when
a parser-critical key vanishes. The tile parser emits most keys
unconditionally (``title``/``price`` may be null on a tile without them, and
tiles without a sku or title are dropped entirely), so those cannot lose
presence; ``url`` — together with ``canonical_path``/``card_input`` it is
derived from — is emitted only when ``action.link`` parses, and its loss
means every result silently stopped being navigable. That is the one
condition the required-family registry can honestly stand on: the zero-tile
and price-less cases are already loud through the no_items warning and
``validate_offer`` aggregation.
"""

# START_MODULE_CONTRACT
# PURPOSE: Pin the normalized search fingerprint and navigability tripwire.
# SCOPE: Captured fixture types; required URL family only, not upstream availability.
# DEPENDS: mcp_core.resilience; ozon_connector.server; captured search fixture.
# LINKS: M-OZON; R-OZON-SEARCH-NUMERIC; R-SHAPE-DRIFT-WARNING.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# SEARCH_SHAPE_REFERENCE: Numeric normalized types measured from the captured tiles.
# SEARCH_REQUIRED_FAMILIES: URL presence required by tool and canary.
# END_MODULE_MAP

from __future__ import annotations

# START_BLOCK_SEARCH_SHAPE
SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "items[].canonical_path:str",
    "items[].card_input:str",
    "items[].price:float",
    "items[].price_original:float",
    "items[].price_original:null",
    "items[].rating:float",
    "items[].rating_count:int",
    "items[].sku:int",
    "items[].stock:null",
    "items[].stock:str",
    "items[].title:str",
    "items[].url:str",
)

# Key families whose PRESENCE the tile parser depends on; the type may
# legitimately vary with the data (``price_original:float`` on a discounted
# tile, ``:null`` on a tile without one).
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (("items[].url",),)
# END_BLOCK_SEARCH_SHAPE
