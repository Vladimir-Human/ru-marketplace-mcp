"""The recording-backed stub: the source search calls, answered from recordings.

Plan item A7. The benchmark replays a recorded comparison through today's
compare adapters and ``decide.build_response``. The only thing that changes is
where the search answers come from: this stub stands in for the marketplace
search calls and returns the recorded payload rebuilt into the connector's own
typed response - the same type the adapter would have received.

A request no recording covers is an error, never an empty answer: a silent
"no results" would turn a missing recording into a plausible comparison.

START_MODULE_CONTRACT
PURPOSE: Answer the compare adapters' search calls from committed recordings.
SCOPE: The recording index, the typed rebuild, the replay of one recording, the mixed-region check.
DEPENDS: compare_connector.server, compare_connector.models_output; the connector output models; scripts/compare_recording.py; FastMCP ToolError; stdlib importlib,json.
LINKS: M-COMPARE, M-COMPARE-RECORDING, R-COMPARE-BENCHMARK-REPLAY.
START_MODULE_MAP
  RecordingStub: The recorded answers, indexed by source and query.
  RecordingSource: One marketplace's search tool, answered from the recordings.
  rebuild: The connector's own typed response, rebuilt from the recorded fields.
  replay: One recorded comparison, run through today's adapters and build_response.
  mixed_regions: Whether the recorded sources answered for different regions.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from compare_connector import server
from compare_connector.models_output import CompareResponse
from fastmcp.exceptions import ToolError

ROOT = Path(__file__).resolve().parents[4]


def _recorder() -> Any:
    """The recorder module, loaded from scripts/ by path (scripts/ is not a package)."""
    path = ROOT / "scripts" / "compare_recording.py"
    spec = importlib.util.spec_from_file_location("compare_recording", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"the recorder module is missing at {path}")
    module = importlib.util.module_from_spec(spec)
    # The module has to be in sys.modules while it runs: a dataclass looks its
    # own module up to resolve its annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RECORDING = _recorder()


class UnrecordedRequest(Exception):
    """A search call no recording covers."""


# START_BLOCK_RECORDING_STUB


def _model(path: str) -> Any:
    """One model class, named "module:Class"."""
    module_name, _, class_name = path.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def rebuild(source: str, payload: dict[str, Any]) -> Any:
    """The connector's own typed response, rebuilt from the recorded fields.

    The recorded fields are the model's own attributes, so the object the
    adapter receives is the object it would have received from the marketplace.
    """
    plan = RECORDING.SOURCE_PLANS[source]
    item_model = _model(plan.item_model)
    response_model = _model(plan.response_model)
    items = [item_model(**item) for item in payload.get("items", [])]
    return response_model(items=items, meta=payload.get("meta", {}))


class RecordingStub:
    """The recorded answers, indexed by source and query."""

    def __init__(self, recordings: list[dict[str, Any]]) -> None:
        self.entries: dict[tuple[str, str], dict[str, Any]] = {}
        for recording in recordings:
            query = str(recording.get("query", ""))
            for source, entry in (recording.get("sources") or {}).items():
                self.entries[(source, query)] = entry
        self.unrecorded: list[tuple[str, str]] = []

    def answer(self, source: str, query: str) -> dict[str, Any]:
        """The recorded answer for one source and query, or a clear failure."""
        entry = self.entries.get((source, query))
        if entry is None:
            self.unrecorded.append((source, query))
            raise UnrecordedRequest(f"no recording covers {source!r} for query {query!r}")
        return entry

    def sources(self, names: Iterable[str]) -> dict[str, Any]:
        """One stand-in module per source, each answering from the recordings."""
        return {name: RecordingSource(name, self) for name in names}


class RecordingSource:
    """One marketplace's search tool, answered from the recordings.

    The compare adapter calls ``SOURCES[name].<search tool>``; this object
    answers that call with the recorded payload, rebuilt into the connector's
    own response type.
    """

    def __init__(self, source: str, stub: RecordingStub) -> None:
        self.source = source
        self.stub = stub
        plan = RECORDING.SOURCE_PLANS[source]
        setattr(self, plan.tool, self._search)

    async def _search(self, **kwargs: Any) -> Any:
        query = str(kwargs.get("query", ""))
        entry = self.stub.answer(self.source, query)
        if "error" in entry:
            # The same wire envelope the connector raised - "error" is the code
            # the compare server reads - so the replayed outcome carries the
            # same status and the same error code the live run produced.
            error = entry["error"]
            raise ToolError(
                json.dumps(
                    {"error": error.get("code"), "message": error.get("message")},
                    ensure_ascii=False,
                )
            )
        return rebuild(self.source, entry["response"])


# END_BLOCK_RECORDING_STUB
# START_BLOCK_REPLAY


def mixed_regions(recording: dict[str, Any]) -> bool:
    """Whether the recorded sources answered for different regions.

    A check on the recording for the later benchmark labels; the replay itself
    adds nothing to the answer, so a replayed answer is today's answer.
    """
    regions = set()
    for entry in (recording.get("sources") or {}).values():
        region = (entry.get("response") or {}).get("region")
        if isinstance(region, str) and region:
            regions.add(region)
    return len(regions) > 1


async def replay(recording: dict[str, Any], stub: RecordingStub | None = None) -> CompareResponse:
    """Replay one recorded comparison through today's adapters and build_response.

    The adapters, the ranking and the warnings are the ones the live server
    uses: only the search calls are answered from the recording. A request no
    recording covers fails the replay instead of shrinking the answer.
    """
    stub = stub if stub is not None else RecordingStub([recording])
    names = list(recording.get("sources") or {})
    previous = server.SOURCES
    server.SOURCES = stub.sources(names)
    try:
        result = await server.compare_prices(str(recording.get("query", "")), sources=names)
    finally:
        server.SOURCES = previous
    if stub.unrecorded:
        source, query = stub.unrecorded[0]
        raise UnrecordedRequest(f"no recording covers {source!r} for query {query!r}")
    return result


# END_BLOCK_REPLAY
