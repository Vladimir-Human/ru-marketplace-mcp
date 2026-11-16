"""Record what the compare adapters read, so a comparison can be replayed.

Plan item A7. The recorder sits on the boundary the compare adapters call -
``SOURCES[name].<search tool>`` - and stores, per compare call, the query, the
capture time and each source's search payload cut down to the fields the
benchmark reads. The replay reads those files back through today's adapters and
``decide.build_response``, so a recorded comparison and a live one are judged by
the same code.

Nothing here contacts a marketplace: the recorder only writes down an answer
somebody else asked for, and the capture hook wraps the tool without changing
what it returns or how it fails.

START_MODULE_CONTRACT
PURPOSE: Capture the compare adapters' raw search payload per compare call and store it as a replayable recording.
SCOPE: Adapter fields including images, city-free URLs, scrubbed diagnostics and regions, the recording document, cancellation-safe capture and the child launcher.
DEPENDS: mcp_core.sources (the registry), mcp_core.errors, mcp_core.redact; FastMCP ToolError; stdlib argparse,datetime,importlib,json,os,pathlib,re,runpy,urllib.parse.
LINKS: M-COMPARE-RECORDING, M-COMPARE, M-LIVE-AUDIT, R-COMPARE-BENCHMARK-REPLAY.
START_MODULE_MAP
  SOURCE_PLANS: The fields each compare adapter reads, per source.
  _recorded_url: Remove Avito city paths and location queries without losing item/image identity.
  cut_response: One source's typed search response cut down to those fields, with URLs and text scrubbed.
  scrub_regions: Replace regions with R1, R2, ... and their diagnostic mentions with the same placeholders.
  build_recording: One recording document: the query, the capture time, the sources.
  assemble_recording: One recording document from the captured sources and the compare outcomes.
  write_recording: Write one recording file.
  error_entry: The recorded form of a source's error.
  capture_sources: Wrap the search tools so the adapters' calls are captured.
  serve: Run a stdio server with the capture hook installed.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import argparse
import datetime
import functools
import importlib
import itertools
import json
import math
import os
import re
import runpy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastmcp.exceptions import ToolError
from mcp_core.errors import ConnectorError
from mcp_core.redact import redact_error_text

# The recording format version. A reader that does not know this version must
# refuse the file rather than guess.
SCHEMA = 1

# The region placeholders, in order of appearance: R1, R2, ...
REGION_KEYS = ("region", "regions")


@dataclass(frozen=True)
class SourcePlan:
    """What one marketplace's search payload looks like to the compare adapters.

    ``item_fields`` are the attributes the adapter reads, one by one, off every
    item of the search response - nothing else is stored. ``extra_fields`` keep
    supplemental adapter reads (Yandex's image) in the same field plan.
    ``region_fields`` name the response
    attributes that say which region the answer is for.
    """

    source: str
    tool: str
    item_fields: tuple[str, ...]
    response_model: str
    item_model: str
    extra_fields: tuple[str, ...] = ()
    region_fields: tuple[str, ...] = ()


# START_BLOCK_FIELD_PLAN
# The field lists are the adapters' own reads (packages/compare-connector/src/
# compare_connector/server.py, one `_search_<source>` per marketplace); the
# model paths are where the typed payload comes from and is rebuilt to.
SOURCE_PLANS: dict[str, SourcePlan] = {
    "wildberries": SourcePlan(
        source="wildberries",
        tool="wb_search",
        item_fields=(
            "nm_id",
            "name",
            "brand",
            "supplier",
            "price_rub",
            "price_kind",
            "wallet_price_rub",
            "review_rating",
            "feedbacks",
            "in_stock",
            "total_quantity",
        ),
        response_model="wb_connector.models_output:WbSearchResponse",
        item_model="wb_connector.models_output:WbCardItem",
    ),
    "yandex_market": SourcePlan(
        source="yandex_market",
        tool="yandex_search",
        item_fields=(
            "product_id",
            "sku_id",
            "title",
            "brand",
            "seller",
            "price_rub",
            "price_with_plus",
            "rating",
            "rating_count",
            "in_stock",
            "url",
        ),
        response_model="yandex_connector.models_output:YandexSearchResponse",
        item_model="yandex_connector.models_output:YandexProduct",
        extra_fields=("image",),
    ),
    "ozon": SourcePlan(
        source="ozon",
        tool="ozon_search",
        item_fields=("sku", "title", "price", "rating", "rating_count", "stock", "url", "image_url"),
        response_model="ozon_connector.models_output:OzonSearchResponse",
        item_model="ozon_connector.models_output:OzonSearchItemOut",
    ),
    "avito": SourcePlan(
        source="avito",
        tool="avito_search",
        item_fields=("item_id", "title", "seller_name", "price_rub", "url"),
        response_model="avito_connector.models_output:AvitoSearchResponse",
        item_model="avito_connector.models_output:AvitoSearchItemOut",
        region_fields=("location_id",),
    ),
    "taobao": SourcePlan(
        source="taobao",
        tool="taobao_search",
        item_fields=("item_id", "title", "shop_name", "price_cny", "url"),
        response_model="taobao_connector.models_output:TaobaoSearchResponse",
        item_model="taobao_connector.models_output:TaobaoSearchItemOut",
    ),
    "megamarket": SourcePlan(
        source="megamarket",
        tool="megamarket_search",
        item_fields=("item_id", "title", "price_rub", "rating", "rating_count", "is_available", "url", "image_url"),
        response_model="megamarket_connector.models_output:MegamarketSearchResponse",
        item_model="megamarket_connector.models_output:MegamarketSearchItemOut",
    ),
    "lamoda": SourcePlan(
        source="lamoda",
        tool="lamoda_search",
        item_fields=("sku", "title", "brand", "price_rub", "url"),
        response_model="lamoda_connector.models_output:LamodaSearchResponse",
        item_model="lamoda_connector.models_output:LamodaSearchItemOut",
    ),
    "dns": SourcePlan(
        source="dns",
        tool="dns_search",
        item_fields=("product_id", "title", "price_rub", "url", "image_url"),
        response_model="dns_connector.models_output:DnsSearchResponse",
        item_model="dns_connector.models_output:DnsSearchItemOut",
    ),
    "citilink": SourcePlan(
        source="citilink",
        tool="citilink_search",
        item_fields=("product_id", "title", "price_rub", "url"),
        response_model="citilink_connector.models_output:CitilinkSearchResponse",
        item_model="citilink_connector.models_output:CitilinkSearchItemOut",
    ),
    "aliexpress": SourcePlan(
        source="aliexpress",
        tool="aliexpress_search",
        item_fields=("item_id", "title", "price_rub", "rating", "url", "image_url"),
        response_model="aliexpress_connector.models_output:AliSearchResponse",
        item_model="aliexpress_connector.models_output:AliSearchItemOut",
    ),
}
# END_BLOCK_FIELD_PLAN


# START_BLOCK_PURE_FUNCTIONS


def now() -> str:
    """The capture time, in UTC, as the recording carries it."""
    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")


def _scalar(value: Any) -> Any:
    """A value the recording can hold: a scalar, or nothing at all.

    A price that is not a finite number is dropped rather than written as
    ``NaN``: the file must stay readable JSON, and an unusable number is no
    number.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _meta_field(meta: Any, name: str, default: Any) -> Any:
    """One field of the response's meta block, dict or model."""
    if isinstance(meta, dict):
        return meta.get(name, default)
    return getattr(meta, name, default)


def _region(response: Any, plan: SourcePlan) -> str | None:
    """The region the answer is for, when the payload names one."""
    for name in plan.region_fields:
        value = getattr(response, name, None)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _recorded_url(source: str, value: Any, *, image: bool = False) -> Any:
    """Remove location-bearing URL parts without inventing an item or an image.

    Avito accepts /<id> (avito_card's own input parser); no city/category/slug
    survives. Other page URLs keep their item path but lose query and fragment,
    except Taobao/Tmall's identity-bearing id query. CDN paths name images, not
    cities; keep their rendering/signature queries except location parameters.
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return ""
    if source == "avito" and not image:
        if host and host not in {"avito.ru", "www.avito.ru"}:
            return ""
        match = re.fullmatch(r"(?:[A-Za-z0-9_-]+_)?(\d{6,})", parts.path.rstrip("/").rsplit("/", 1)[-1])
        return f"https://www.avito.ru/{match.group(1)}" if match else ""
    query = ""
    if image:
        location_keys = {"city", "city_id", "region", "region_id", "location", "location_id", "lr", "dest"}
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if not any(key.lower() in location_keys for key, _ in pairs):
            query = parts.query
        else:
            query = urlencode([(key, val) for key, val in pairs if key.lower() not in location_keys])
    elif source == "taobao":
        query = urlencode([(key, val) for key, val in parse_qsl(parts.query) if key == "id"])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def cut_response(source: str, response: Any) -> dict[str, Any]:
    """One source's typed search response, cut down to the fields the benchmark reads.

    The adapter reads the item attributes named in the plan and nothing else;
    this stores exactly those, plus the response's own diagnostics (whether the
    source considered its answer healthy, and what it warned about), the region
    and the moment the answer was observed. Reviewer, phone and e-mail fields
    are excluded. Seller/shop/supplier names are business names, not personal
    data, and stay. Diagnostic text uses the runtime redactor; the completed
    recording also replaces region names with placeholders.
    """
    plan = SOURCE_PLANS[source]
    fields = plan.item_fields + plan.extra_fields
    items = getattr(response, "items", None) or []
    meta = getattr(response, "meta", None)
    region = _region(response, plan)

    def field(item: Any, name: str) -> Any:
        value = _scalar(getattr(item, name, None))
        if name in {"url", "image", "image_url"}:
            value = _recorded_url(source, value, image=name != "url")
        return redact_error_text(value, len(value)) if isinstance(value, str) else value

    return {
        "items": [{name: field(item, name) for name in fields} for item in items],
        "meta": {
            "healthy": _meta_field(meta, "healthy", None),
            "warnings": [redact_error_text(str(warning)) for warning in (_meta_field(meta, "warnings", []) or [])],
        },
        **({"region": region} if region else {}),
        "observed_at": now(),
    }


def scrub_regions(document: dict[str, Any]) -> dict[str, Any]:
    """Every region value becomes R1, R2, ... in order of appearance.

    The same region keeps the same placeholder, so a recording that mixed
    regions still mixes them after the scrub: the condition survives, only the
    name is gone. Diagnostic mentions use the same mapping, including labeled
    regions in warnings without a region field; text also uses runtime redaction.
    """
    placeholders: dict[str, str] = {}

    def placeholder(value: str) -> str:
        if value not in placeholders:
            placeholders[value] = f"R{len(placeholders) + 1}"
        return placeholders[value]

    # Discover names before numbering them: a warning may precede the region
    # field that identifies its otherwise unlabeled mention.
    diagnostic_region = re.compile(r"\b(?:region|city|location)(?:_id)?\s*[:=]\s*([^;,\n]+)", re.IGNORECASE)
    names: set[str] = set()

    def collect(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for name, value in node.items():
                collect(value, name)
        elif isinstance(node, list):
            for value in node:
                collect(value, key)
        elif isinstance(node, str):
            if key in REGION_KEYS:
                names.add(node)
            elif key in {"warnings", "message"}:
                names.update(match.group(1).strip() for match in diagnostic_region.finditer(node))

    collect(document)
    ordered_names = sorted((value for value in names if value), key=lambda value: (-len(value), value))
    pattern = (
        re.compile(r"(?<!\w)(?:" + "|".join(re.escape(value) for value in ordered_names) + r")(?!\w)")
        if ordered_names
        else None
    )

    # Number fields and diagnostic mentions in the same document walk. A
    # warning-only city encountered first must be R1, not the last placeholder.
    def number(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for name, value in node.items():
                number(value, name)
        elif isinstance(node, list):
            for value in node:
                number(value, key)
        elif isinstance(node, str):
            if key in REGION_KEYS:
                placeholder(node)
            elif key in {"warnings", "message"} and pattern:
                for match in pattern.finditer(node):
                    placeholder(match.group(0))

    number(document)

    def walk(node: Any, key: str = "") -> Any:
        if isinstance(node, dict):
            return {name: walk(value, name) for name, value in node.items()}
        if isinstance(node, list):
            return [walk(value, key) for value in node]
        if isinstance(node, str):
            if key in REGION_KEYS:
                return placeholders[node]
            text = redact_error_text(node, len(node))
            if key in {"warnings", "message"} and pattern:
                return pattern.sub(lambda match: placeholders[match.group(0)], text)
            return text
        return node

    return walk(document)


def build_recording(query: str, captured_at: str, sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One recording: the query, the capture time and what each source answered.

    A source that failed is recorded as its error, so the replay reproduces the
    partial answer instead of a complete one. The regions are scrubbed here,
    at record time: a file that leaves the repository never carries a region
    name.
    """
    document = {
        "query": query,
        "captured_at": captured_at,
        "sources": {name: sources[name] for name in sorted(sources)},
        "schema": SCHEMA,
    }
    return scrub_regions(document)


def assemble_recording(
    query: str,
    captured_at: str,
    captures: list[dict[str, Any]],
    outcomes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One recording from the captured sources and the compare call's outcomes.

    A source that answered has a capture; a source that did not (a timeout, a
    refusal, a skipped call) is taken from the compare outcome, so the replay
    sees the same partial answer the live run produced.
    """
    sources: dict[str, dict[str, Any]] = {}
    for capture in captures:
        name = capture.get("source")
        if not isinstance(name, str) or not name:
            continue
        sources[name] = {"error": capture["error"]} if "error" in capture else {"response": capture["response"]}
    for outcome in outcomes or []:
        name = outcome.get("source")
        if not isinstance(name, str) or name in sources:
            continue
        code = outcome.get("error_code") or outcome.get("status") or "unknown_error"
        detail = outcome.get("detail") or ""
        sources[name] = {"error": {"code": str(code), "message": str(detail)}}
    return build_recording(query, captured_at, sources)


def error_entry(exc: Exception) -> dict[str, str]:
    """The recorded form of a source's error: its code and its message.

    The message is redacted the same way the runtime redacts one: an upstream
    text may carry a path or a token, and a recording is a file that leaves the
    machine.
    """
    payload: dict[str, Any] = {}
    if isinstance(exc, ConnectorError):
        payload = exc.to_dict()
    elif isinstance(exc, ToolError):
        try:
            decoded = json.loads(str(exc))
        except (ValueError, RecursionError):
            decoded = None
        if isinstance(decoded, dict):
            payload = decoded
    code = payload.get("error")
    message = payload.get("message")
    return {
        "code": code if isinstance(code, str) and code else "unknown_error",
        "message": redact_error_text(str(message if message is not None else exc), 200),
    }


def write_recording(directory: Path, stem: str, document: dict[str, Any]) -> Path:
    """Write one recording file: UTF-8, LF line endings, one file per compare call."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


# END_BLOCK_PURE_FUNCTIONS


# START_BLOCK_CAPTURE_HOOK
_COUNTER = itertools.count(1)


def registry_modules() -> dict[str, Any]:
    """Every comparable connector module, imported defensively.

    A connector whose optional dependency is missing reduces coverage, exactly
    as it does for the comparison itself; it must not stop the recorder.
    """
    from mcp_core.sources import BY_NAME

    modules: dict[str, Any] = {}
    for source in SOURCE_PLANS:
        try:
            modules[source] = importlib.import_module(BY_NAME[source].module)
        except Exception:
            continue
    return modules


def _write_capture(directory: Path, source: str, query: str, entry: dict[str, Any]) -> Path:
    """Write one captured source answer: its name, the query and what came back."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"source": source, "query": query, "captured_at": now(), **entry}
    path = directory / f"{next(_COUNTER):04d}-{source}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def _capturing(source: str, tool: Any, directory: Path) -> Any:
    """Capture answers and ordinary errors; cancellation/control flow only re-raises."""

    @functools.wraps(tool)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        query = kwargs.get("query", args[0] if args else "")
        try:
            response = await tool(*args, **kwargs)
        except Exception as exc:
            _write_capture(directory, source, str(query), {"error": error_entry(exc)})
            raise
        _write_capture(directory, source, str(query), {"response": cut_response(source, response)})
        return response

    return wrapper


def capture_sources(directory: Path, modules: dict[str, Any] | None = None) -> None:
    """Wrap every comparable connector's search tool so the adapters' calls are captured.

    The wrapper sits exactly where the compare adapters call - the module
    attribute ``SOURCES[name].<search tool>`` - so the recorded payload is what
    the adapter received, not what the marketplace sent. The original tool
    still runs and still raises: only the answer is written down.
    """
    for source, module in (modules if modules is not None else registry_modules()).items():
        plan = SOURCE_PLANS.get(source)
        if plan is None:
            continue
        tool = getattr(module, plan.tool, None)
        if tool is None or getattr(tool, "__compare_recording__", False):
            continue
        wrapper = _capturing(source, tool, directory)
        wrapper.__compare_recording__ = True  # type: ignore[attr-defined]
        setattr(module, plan.tool, wrapper)


# END_BLOCK_CAPTURE_HOOK
# START_BLOCK_CHILD_LAUNCHER


def serve(server: str, argv: list[str] | None = None) -> int:
    """Run a stdio server with the capture hook installed.

    The audit child runs through here when the run records: the hook is
    installed before the server starts, so the comparison that the audit
    measures is the comparison that gets recorded.
    """
    capture = os.environ.get("COMPARE_CAPTURE_DIR")
    if capture:
        capture_sources(Path(capture))
    sys.argv = [server, *(argv or [])]
    runpy.run_path(server, run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compare_recording.py", description=__doc__.splitlines()[0])
    parser.add_argument("--serve", metavar="SERVER", help="run this stdio server with the capture hook installed")
    args, rest = parser.parse_known_args(argv)
    if args.serve:
        return serve(args.serve, rest)
    parser.print_help()
    return 4


# END_BLOCK_CHILD_LAUNCHER

if __name__ == "__main__":
    raise SystemExit(main())
