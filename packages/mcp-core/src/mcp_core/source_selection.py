"""Operator-chosen subset of marketplace sources.

Every advertised tool costs its schema on EVERY client request, so an operator
who only reads three marketplaces should not pay for eleven.
``MARKETPLACE_SOURCES`` names the ones to keep.

Unset means "all", the behaviour before this variable existed — the pinned tool
counts in the test suite stay valid, and an operator who never sets the
variable sees no change at all.

The two mount points name sources differently (``yandex`` in the unified
server's mount table, ``yandex_market`` in compare's source map), so names are
canonicalised here rather than in either caller.
"""
# START_MODULE_CONTRACT
# PURPOSE: Resolve the operator-selected subset of marketplace sources.
# SCOPE: Environment selection and aliases; ignore removed MPStats with one stderr notice; callers perform mounts/filtering.
# DEPENDS: mcp_core.sources (the registry's mountable vocabulary and aliases),mcp_core.logging; stdlib os,json,functools.
# LINKS: M-SOURCE-SELECTION; M-SOURCES; M-CORE; R-MPSTATS-REMOVED.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# SourceSelectionError: Reject requested source names absent from this release.
# canonical: Normalize case, separators and supported source aliases.
# _warn_mpstats_removed: Emit one stderr migration notice per process.
# selected: Read MARKETPLACE_SOURCES; blank means all, removed MPStats is skipped, other unknown names fail loudly.
# wanted: Decide whether a canonical source survives the resolved selection.
# END_MODULE_MAP

from __future__ import annotations

import json
import os
from functools import cache

from mcp_core.logging import make_stderr_logger
from mcp_core.sources import ALIASES, MOUNTABLE

ENV_VAR = "MARKETPLACE_SOURCES"

# The vocabulary and its aliases come from the source registry, so a source
# cannot be mountable and unselectable at once: one record says what the unified
# server mounts and what this variable may name. A typo still fails at startup
# instead of producing a plausible-looking server with one or zero marketplaces
# mounted.
KNOWN_SOURCES = MOUNTABLE

_ALIASES = ALIASES


class SourceSelectionError(ValueError):
    """The selection names a source that this release does not provide."""


def canonical(name: str) -> str:
    """One spelling per source, whichever alias a caller or operator used."""
    cleaned = name.strip().lower().replace("-", "_")
    return _ALIASES.get(cleaned, cleaned)


# START_BLOCK_SOURCE_SELECTION
@cache
def _warn_mpstats_removed() -> None:
    """Report the upgrade once, even when multiple profiles resolve selection."""
    # Entirely static: no token is read, and the URL placeholder is not a secret.
    make_stderr_logger(__name__).warning(
        json.dumps(
            {
                "event": "source.removed",
                "message": (
                    "MPStats was removed in 2.11.0; use the official MPStats MCP server: "
                    "https://mcp.mpstats.io/mcp?token=<your MPStats token>"
                ),
            }
        )
    )


def selected() -> set[str] | None:
    """Canonical names the operator asked for, or ``None`` meaning all of them.

    An empty or whitespace-only value is treated as unset: a client config that
    passes the variable through with nothing in it must not silently mount an
    empty server. Unknown names raise ``SourceSelectionError`` so a typo cannot
    silently produce a partial server. The removed MPStats name is ignored with
    one migration notice, preserving the other requested sources.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    chosen = {canonical(part) for part in raw.split(",") if part.strip()}
    if "mpstats" in chosen:
        chosen.remove("mpstats")
        _warn_mpstats_removed()
    unknown = sorted(chosen - KNOWN_SOURCES)
    if unknown:
        names = ", ".join(unknown)
        supported = ", ".join(sorted(KNOWN_SOURCES))
        raise SourceSelectionError(f"{ENV_VAR} contains unknown source(s): {names}. Supported: {supported}")
    return chosen


# END_BLOCK_SOURCE_SELECTION


def wanted(name: str, chosen: set[str] | None) -> bool:
    """Whether this source survives the operator's selection."""
    return chosen is None or canonical(name) in chosen
