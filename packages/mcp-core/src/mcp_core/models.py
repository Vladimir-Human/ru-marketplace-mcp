"""Response envelopes every connector shares.

Two contracts live here, and both were previously copy-pasted into each
connector — four ``MetaOut`` classes and four near-identical selfcheck response
shapes. Editing the response envelope meant editing it four times and hoping
none drifted.

**They are base classes, not fixed schemas, and that is deliberate.** The four
copies were never actually identical: Yandex reports *how* it extracted a page
(``extraction``), and Detsky Mir reports whether a response came from its TTL
cache (``cached``). Those fields are real information about real differences
between connectors, so collapsing everything into one flat class would have had
to delete them and change two wire contracts. Instead each connector subclasses
and adds what only it knows.

The selfcheck contract is the tri-state drift canary described in the README:

``success``
    Every sub-check matched its baseline. Parsers are healthy.
``drift_detected``
    A payload no longer matches. Someone needs to look; the connector is
    returning less than it should.
``inconclusive``
    The check could not reach a verdict — geo-block, transport failure, an
    endpoint refusing datacenter traffic. **Not** a parser verdict, and the
    distinction matters: reporting a geo-block as ``drift_detected`` would send
    maintainers hunting a schema change that never happened.

``SelfCheckEntryBase`` sets ``extra="allow"`` because
``resilience.selfcheck_entry`` attaches per-check diagnostics that vary by
endpoint family (``missing_widgets``, ``max_pages``, ``smoke``, ...). Pinning an
exhaustive field list would mean silently dropping the very detail a drift
report is read for.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MetaOutBase",
    "SelfCheckEntryBase",
    "SelfCheckResponseBase",
]


class MetaOutBase(BaseModel):
    """Per-response provenance: which tool answered, and can you trust it.

    ``healthy=False`` with a populated ``warnings`` list is the honest middle
    ground between a clean result and a raised error: the payload parsed, but
    something about it was off, and the caller deserves to know before quoting a
    price to someone.
    """

    source: str = Field(default="", description="Tool name that produced this response.")
    healthy: bool = Field(default=True, description="Whether the response passed structural validation.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Connector-level warnings (schema drift, partial data, fallbacks).",
    )


class SelfCheckEntryBase(BaseModel):
    """One sub-check verdict inside a selfcheck response.

    ``extra="allow"`` preserves the variable diagnostics that
    ``resilience.selfcheck_entry`` injects per endpoint family.
    """

    model_config = ConfigDict(extra="allow")

    state: str = Field(default="", description="Sub-check verdict: healthy, drift, or inconclusive.")
    notes: list[str] = Field(default_factory=list, description="Diagnostic notes.")


class SelfCheckResponseBase(BaseModel):
    """Shared shape of every ``*_selfcheck()`` tool response.

    Subclasses narrow ``checks`` to their own entry type and set the ``connector``
    default. Process identity (version, start time, pid) is included because the
    first question about a drift report is always which build produced it.
    """

    status: str = Field(
        default="",
        description="Overall verdict: success, drift_detected, or inconclusive.",
    )
    connector: str = Field(default="", description="Connector name.")
    checks: dict[str, Any] = Field(default_factory=dict, description="Per-subcheck results.")
    server_version: str = Field(default="", description="Connector server version.")
    server_started_at: str = Field(default="", description="Server start timestamp (UTC ISO-8601).")
    process_id: int = Field(default=0, description="OS process id.")
    config_loaded: bool = Field(default=False, description="Whether settings loaded successfully from env/defaults.")
    tool_count: int = Field(default=0, description="Number of MCP tools registered on the server.")
