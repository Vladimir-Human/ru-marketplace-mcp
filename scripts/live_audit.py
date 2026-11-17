"""Private outside-in audit, dispatched only by live_check.py audit.

START_MODULE_CONTRACT
PURPOSE: Plan and run P1-4 through budgeted full-profile MCP stdio; retain private evidence.
SCOPE: Pure A/B/C plans, cost splits, source pauses/skip, child-hook refusal, sequential ledger guards,
       owned raw-CDP captures,
       schema/catalogue/guide/PII/host checks, compare timing, compare recordings and offline cycle summary.
DEPENDS: live_matrix helpers (costs, items, ids, history); live_budget; e2e_stdio_check client;
         compare_recording (the compare recorder); mcp_core.sources,resources,transport.chrome_cdp; jsonschema; stdlib.
LINKS: M-LIVE-AUDIT, M-COMPARE-RECORDING, R-LIVE-AUDIT.child, R-LIVE-AUDIT.plans, R-LIVE-AUDIT.capture,
       R-LIVE-AUDIT.checks, R-LIVE-AUDIT.timing, R-LIVE-AUDIT.runner, R-COMPARE-BENCHMARK-REPLAY.
START_MODULE_MAP
  load_cases: Read audit additions while reusing matrix fixed-query validation.
  plan_run: Pure cost-aware run slice with unresolved compare-winner slots.
  child_environment/require_hook/open_session: Full-profile child and fail-closed nonce handshake.
  json_ld_evidence/capture_card: Product/Offer evidence and two raw-CDP PNGs with owned-tab cleanup.
  check_answer: Output schema, served error catalogue, guide, personal fields and URL host checks.
  compare_trigger: Eight-call cycle timing threshold, with explicit incomplete status.
  _record_comparison: One recording per compare call, from the sources the child captured.
  execute_plan: One call at a time, guard every resolved source, checkpoint private JSON and ids.
  _child_refused: Whether the child's budget refused a request during a call (its [refused] stderr line).
  select_options/run/summary: CLI validation, live runner and request-free T3 skeleton.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import compare_recording
import live_budget
import live_matrix

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = (live_matrix.PLAN_LIMIT, live_matrix.SOURCE_LIMIT)

# START_BLOCK_PURE_PLANS


def load_cases() -> dict[str, Any]:
    cases = json.loads(live_matrix.AUDIT_CASES_PATH.read_text(encoding="utf-8"))
    fixed = live_matrix.load_cases()
    if cases.get("fixed_queries") != fixed or set(cases.get("phase_a_tools", {})) != set(fixed):
        raise ValueError("invalid audit cases")
    from mcp_core.sources import BY_NAME

    for source in fixed:
        tools = cases["phase_a_tools"][source]
        names = [tool["tool"] for tool in tools]
        if len(names) != BY_NAME[source].tools or len(set(names)) != len(names):
            raise ValueError("invalid audit tool inventory")
        if not all(name.startswith(BY_NAME[source].tool_prefix) for name in names):
            raise ValueError("invalid audit tool prefix")
        if not cases.get("domains", {}).get(source):
            raise ValueError("audit domains missing")
    return cases


def _card_args(cases: dict[str, Any], source: str, item_id: Any) -> dict[str, Any]:
    card = cases["fixed_queries"][source]["card"]
    value: Any = item_id
    if item_id is None:
        value = "$id"
    elif card.get("id_many"):
        value = [int(item_id)]
    elif source == "detsky_mir":
        value = int(item_id)
    else:
        value = str(item_id)
        template = cases.get("card_url_templates", {}).get(source)
        if template and not value.startswith("https://"):
            value = template.format(id=value)
    return {card["id_argument"]: value}


def _call(
    key: str, tool: str, source: str, kind: str, args: dict[str, Any], costs: dict[str, int], **extra: Any
) -> dict[str, Any]:
    return {"key": key, "tool": tool, "source": source, "kind": kind, "args": args, "costs": costs, **extra}


def _group(cases: dict[str, Any], source: str) -> str:
    return cases["fixed_queries"][source].get("ledger_source", source)


def _phase_a(
    cases: dict[str, Any], ids: dict[str, Any], costs: dict[tuple[str, str], int], guard: tuple[int, int]
) -> list[list[dict[str, Any]]]:
    bins: list[list[dict[str, Any]]] = [[], []]
    spent: list[Counter[str]] = [Counter(), Counter()]
    for source, tools in cases["phase_a_tools"].items():
        bundle = []
        for spec in tools:
            kind = spec.get("kind", "other")
            args = dict(spec.get("kwargs", {}))
            if kind == "listing":
                args = dict(cases["fixed_queries"][source]["listing"]["kwargs"])
            elif kind == "card":
                args = _card_args(cases, source, (ids.get(source) or [None])[0])
            cost = costs.get((source, kind), live_matrix.SEED_COSTS.get((source, kind), 1))
            bundle.append(_call("A:" + spec["tool"], spec["tool"], source, kind, args, {_group(cases, source): cost}))
        chunks = [bundle]
        if source == "wildberries":
            split = amount = 0
            for call in bundle:
                cost = sum(call["costs"].values())
                if amount + cost > guard[1]:
                    break
                amount += cost
                split += 1
            chunks = [bundle[:split], bundle[split:]]
        for i, chunk in enumerate(chunks):
            amount = sum(sum(c["costs"].values()) for c in chunk)
            # Keep other source bundles whole and WB chunks in their assigned runs.
            candidates = [i] if source == "wildberries" else [min(range(2), key=lambda n: len(bins[n]))]
            selected = next(
                (
                    n
                    for n in candidates
                    if spent[n].total() + amount <= guard[0] and spent[n][_group(cases, source)] + amount <= guard[1]
                ),
                None,
            )
            if selected is None:
                raise ValueError("phase A inventory cannot fit two guarded runs")
            bins[selected].extend(chunk)
            spent[selected][_group(cases, source)] += amount
    return bins


def _unique(values: list[Any]) -> list[Any]:
    """Saved ids without repeats (123 and "123" are one offer), first spelling kept."""
    seen: set[str] = set()
    unique = []
    for value in values:
        if str(value) not in seen:
            seen.add(str(value))
            unique.append(value)
    return unique


def _phase_b(
    cases: dict[str, Any], ids: dict[str, Any], costs: dict[tuple[str, str], int], run: int, guard: tuple[int, int]
) -> list[dict[str, Any]]:
    sources = list(cases["fixed_queries"])[(run - 1) * 4 : run * 4]
    plan = []
    for source in sources:
        cost = costs.get((source, "card"), 1)
        offers = min(3 if cost < 2 else 2, guard[1] // (cost + 1))
        for index, item_id in enumerate(_unique(ids.get(source) or [])[:offers]):
            key = f"B:{source}:{index}"
            plan.append(
                _call(
                    key + ":card",
                    cases["fixed_queries"][source]["card"]["tool"],
                    source,
                    "card",
                    _card_args(cases, source, item_id),
                    {_group(cases, source): cost},
                )
            )
            plan.append(
                _call(
                    key + ":capture",
                    "capture",
                    source,
                    "capture",
                    {},
                    {_group(cases, source): 1},
                    depends=key + ":card",
                )
            )
    if sum(sum(c["costs"].values()) for c in plan) > guard[0]:
        raise ValueError("phase B cannot fit the guard")
    return plan


def _phase_c(
    cases: dict[str, Any], costs: dict[tuple[str, str], int], run: int, cycle: int, guard: tuple[int, int]
) -> list[dict[str, Any]]:
    from mcp_core.sources import BY_NAME, COMPARE_SOURCES

    queries = cases.get("compare_queries", {}).get(str(cycle))
    if not isinstance(queries, list) or len(queries) != 8:
        raise ValueError("missing eight compare queries for cycle")
    # compare_prices searches only the text-search sources by default (Detsky Mir is card-only).
    sources = [s for s in COMPARE_SOURCES if BY_NAME[s].text_search][:11]
    query_costs = {
        _group(cases, s): costs.get((s, "listing"), live_matrix.SEED_COSTS.get((s, "listing"), 1)) for s in sources
    }
    if any(value > guard[1] for value in query_costs.values()):
        raise ValueError("compare query cannot fit source guard")
    max_card = max(costs.get((s, "card"), 1) for s in sources)
    index = run - 1
    key = f"C:{index}"
    plan = [
        _call(key, "compare_prices", "compare", "compare", {"query": queries[index], "sources": sources}, query_costs)
    ]
    for winner in ("cheapest", "cheapest_comparable"):
        plan.append(
            _call(
                key + ":" + winner,
                "compare_verify_offer",
                "$winner",
                "verify",
                {},
                {"$winner": max_card},
                depends=key,
                winner=winner,
            )
        )
    plan.append(
        _call(
            key + ":capture",
            "capture",
            "$winner",
            "capture",
            {},
            {"$winner": 1},
            depends=key,
            winner="cheapest_comparable",
        )
    )
    if sum(sum(c["costs"].values()) for c in plan) > guard[0]:
        raise ValueError("compare plan cannot fit total guard")
    return plan


def plan_run(
    cases: dict[str, Any],
    ids: dict[str, Any],
    costs: dict[tuple[str, str], int],
    phase: str,
    run: int,
    *,
    cycle: int = 1,
    guard: tuple[int, int] = GUARD,
) -> list[dict[str, Any]]:
    """Pure plan; winner slots are resolved and source-guarded only after the answer."""
    if phase not in {"A", "B", "C"} or not 1 <= run <= {"A": 2, "B": 3, "C": 8}[phase] or cycle < 1:
        raise ValueError("invalid audit phase/run/cycle")
    if phase == "A":
        if cycle != 1:
            raise ValueError("phase A is cycle 1 only")
        return _phase_a(cases, ids, costs, guard)[run - 1]
    if phase == "B":
        return _phase_b(cases, ids, costs, run, guard)
    return _phase_c(cases, costs, run, cycle, guard)


# END_BLOCK_PURE_PLANS
# START_BLOCK_CHILD_SESSION


def child_environment(ledger: Path, marker: Path, nonce: str, capture_dir: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        LIVE_BUDGET_LEDGER=str(ledger.resolve()),
        LIVE_AUDIT_HOOK_FILE=str(marker.resolve()),
        LIVE_AUDIT_NONCE=nonce,
        MCP_TRANSPORT="stdio",
        MARKETPLACE_SOURCES="",
        CHROME_CHALLENGE_HANDOFF_S="0",
    )
    if capture_dir is not None:
        # The child writes what the compare adapters read into this directory;
        # the parent turns it into one recording per compare call.
        env["COMPARE_CAPTURE_DIR"] = str(capture_dir.resolve())
    return env


def require_hook(marker: Path, ledger: Path, nonce: str) -> None:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("audit child hook handshake missing") from None
    if (
        not isinstance(data, dict)
        or data.get("installed") is not True
        or data.get("nonce") != nonce
        or data.get("ledger") != str(ledger.resolve())
    ):
        raise RuntimeError("audit child hook handshake invalid")


@contextlib.asynccontextmanager
async def open_session(
    budget: live_budget.Budget, directory: Path, *, target: Path | None = None, capture_dir: Path | None = None
) -> AsyncIterator[Any]:
    # Same actual SDK client/initialize protocol as the existing stdio probe.
    from e2e_stdio_check import TIMEOUT_S, ClientSession, StdioServerParameters, stdio_client

    if budget.ledger.path is None:
        raise RuntimeError("audit requires a shared file ledger")
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ("hook-" + uuid.uuid4().hex + ".json")
    nonce = uuid.uuid4().hex
    server = Path(__file__).with_name("audit_server.py")
    args = [str(server)]
    if target is not None:
        args.extend(["--target", str(target)])
    if capture_dir is not None:
        # The recorder wraps the search tools before the server starts, so the
        # comparison the audit measures is the comparison that gets recorded.
        args = [str(Path(__file__).with_name("compare_recording.py")), "--serve", *args]
    params = StdioServerParameters(
        command=sys.executable, args=args, env=child_environment(budget.ledger.path, marker, nonce, capture_dir)
    )

    class AuditSession(ClientSession):
        async def validate_tool_result(self, name: str, result: Any) -> None:
            # Retain malformed answers as audit evidence; check_answer owns validation.
            # The ordinary SDK raises here and would discard the faulty payload.
            return None

    with (directory / "child.log").open("w", encoding="utf-8") as diagnostics:
        async with stdio_client(params, errlog=diagnostics) as (read, write), AuditSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=TIMEOUT_S)
            require_hook(marker, budget.ledger.path, nonce)
            yield session


# END_BLOCK_CHILD_SESSION
# START_BLOCK_CAPTURE


def _nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _schema_type(value: Any) -> str | None:
    if isinstance(value, str):
        return re.sub(r"^https?://schema\.org/", "", value)
    return None


def json_ld_evidence(blocks: list[str]) -> dict[str, Any]:
    from mcp_core.resilience import coerce_price

    prices = []
    exists = False
    for block in blocks:
        try:
            parsed = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in _nodes(parsed):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            kind = next((_schema_type(t) for t in types if _schema_type(t) in {"Product", "Offer"}), None)
            if kind is None:
                continue
            exists = True
            price = coerce_price(node.get("price"))
            if price is not None:
                prices.append({"type": kind, "price": price, "currency": node.get("priceCurrency")})
    return {
        "product_or_offer_exists": exists,
        "json_ld_prices": prices,
        "screenshot_price": None,
        "json_ld_matches_screenshot": None,
    }


def _domains(cases: dict[str, Any], source: str) -> tuple[str, ...]:
    from mcp_core.sources import BY_NAME

    domains = getattr(BY_NAME[source], "domains", None) or cases["domains"][source]
    return tuple(domains)


def _valid_url(url: str, domains: tuple[str, ...]) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        return (
            parts.scheme == "https"
            and not parts.username
            and not parts.password
            and parts.port in {None, 443}
            and any(host == domain or host.endswith("." + domain) for domain in domains)
        )
    except ValueError:
        return False


@contextlib.asynccontextmanager
async def _capture_page(url: str, **kwargs: Any) -> AsyncIterator[Any]:
    from mcp_core.transport import chrome_cdp

    # Raw helper owns create/close in finally and is exactly the installed navigation hook.
    async with chrome_cdp._raw_cdp_page(url, 1000, wait_for_dom_parsed=True) as page:
        yield page


async def capture_card(
    url: str, domains: tuple[str, ...], directory: Path, stem: str, *, opener: Callable[..., Any] | None = None
) -> dict[str, Any]:
    """Capture only an owned, policy-checked tab. The opener closes it in finally.

    START_CONTRACT: capture_card
    PURPOSE: Preserve the visible card and Product/Offer JSON-LD without image libraries.
    INPUTS: HTTPS card URL on the source's domains; private output directory and internal filename.
    OUTPUTS: Two PNG names, final URL/host and JSON-LD evidence; screenshot price remains null.
    SIDE_EFFECTS: One booked raw navigation, viewport override on owned tab, files; always close tab.
    END_CONTRACT
    """
    if not _valid_url(url, domains) or not re.fullmatch(r"[A-Za-z0-9_-]+", stem):
        raise ValueError("invalid audit capture URL or filename")
    directory.mkdir(parents=True, exist_ok=True)
    async with (opener or _capture_page)(url) as page:
        final_url = await page.evaluate("() => location.href")
        if not isinstance(final_url, str) or not _valid_url(final_url, domains):
            raise ValueError("audit capture left source host policy")
        await page._send(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1280, "height": 1600, "deviceScaleFactor": 1, "mobile": False},
        )
        blocks = await page.evaluate(
            "() => Array.from(document.querySelectorAll('script[type=\"application/ld+json\"]'), e => e.textContent)"
        )
        result = json_ld_evidence(blocks if isinstance(blocks, list) else [])
        names = []
        for label, scale in (("1280x1600", 1), ("960x1200", 0.75)):
            shot = await page._send(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": False,
                    "clip": {"x": 0, "y": 0, "width": 1280, "height": 1600, "scale": scale},
                },
            )
            name = f"{stem}-{label}.png"
            (directory / name).write_bytes(base64.b64decode(shot["data"], validate=True))
            names.append(name)
        result.update(capture_files=names, final_url=final_url, final_host=urlsplit(final_url).hostname)
        return result


async def _window_hidden() -> bool | None:
    from mcp_core.transport import chrome_cdp

    try:
        targets = await asyncio.to_thread(chrome_cdp._cdp_http_json, "/json", timeout_s=2)
        target = next(t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl"))
        # Attach one existing tab; never navigate, activate, open or reveal a window.
        ws_url = target["webSocketDebuggerUrl"]
        if urlsplit(ws_url).hostname != chrome_cdp.CDP_HOST:
            return None
        async with chrome_cdp._websockets.connect(ws_url, open_timeout=3) as ws:
            page = chrome_cdp._RawCdpPage(ws, target["id"])
            value = await asyncio.wait_for(page.evaluate("() => document.visibilityState"), timeout=5)
        return value == "hidden" if value in {"hidden", "visible"} else None
    except Exception:
        return None


# END_BLOCK_CAPTURE
# START_BLOCK_ANSWER_CHECKS


def _payload(result: Any) -> tuple[Any, Any]:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured, structured["result"]
        return structured, structured
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if text:
            try:
                return None, json.loads(text)
            except ValueError:
                continue
    return None, None


def _error(value: Any) -> str | None:
    if isinstance(value, dict):
        code = value.get("error")
        if code is None:
            code = value.get("error_code")
        if code is not None:
            return code if isinstance(code, str) and code else "unknown_error"
        if value.get("status") == "error":
            return "unknown_error"
    return None


def _catalogue(text: str) -> set[str]:
    return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", text, re.MULTILINE))


def check_answer(
    value: Any, structured: Any, tool: Any, resources: set[str], errors: str, cases: dict[str, Any], source: str
) -> dict[str, Any]:
    from jsonschema import SchemaError, ValidationError
    from jsonschema.validators import validator_for
    from mcp_core.resources import guide_uri
    from referencing import Registry
    from referencing.exceptions import Unresolvable

    code = _error(value)
    schema = getattr(tool, "output_schema", None) or {"type": "object"}
    checked = structured
    if code is not None:
        # ConnectorError.to_dict is the error wire contract; success schemas do not apply.
        schema = {
            "type": "object",
            "required": ["error", "message", "retryable"],
            "properties": {
                "error": {"type": "string", "minLength": 1},
                "message": {"type": "string"},
                "retryable": {"type": "boolean"},
                "provider": {"type": "string"},
                "retry_after_s": {"type": "number", "minimum": 0},
                "status_code": {"type": "integer"},
                "retryable_now": {"type": "boolean"},
                "requires_user_action": {"type": "boolean"},
            },
        }
        checked = value
    try:
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema, registry=Registry()).validate(checked)
        schema_ok = checked is not None
    except (ValidationError, SchemaError, Unresolvable):
        schema_ok = False
    nodes = list(_nodes(value))
    personal = [field for node in nodes for field in cases["personal_data_fields"] if node.get(field) is not None]
    urls = [
        (key, v)
        for node in nodes
        for key, v in node.items()
        if (key == "url" or key.endswith("_url")) and isinstance(v, str) and v
    ]
    hosts = sorted({urlsplit(url).hostname or "" for _key, url in urls})
    domains = _domains(cases, source)
    return {
        "schema": schema_ok,
        "guide": guide_uri(tool.name) in resources,
        "error_catalogue": code is None or code in _catalogue(errors),
        "personal_data": not personal,
        "personal_data_fields": sorted(set(personal)),
        # Page links stay on the source's domains; images come from CDNs on other hosts
        # (AliExpress: aliexpress-media.com) and are not navigated, so only their scheme is checked.
        "hosts": all(
            urlsplit(url).scheme == "https" if key == "image_url" else _valid_url(url, domains) for key, url in urls
        ),
        "final_hosts": hosts,
    }


def compare_trigger(records: list[dict[str, Any]]) -> dict[str, Any]:
    times = [
        row["latency_s"]
        for row in records
        if row.get("tool") == "compare_prices"
        and row.get("state") != "skipped"
        and isinstance(row.get("latency_s"), (int, float))
    ]
    median = statistics.median(times) if times else None
    peak = max(times) if times else None
    return {
        "calls": len(times),
        "complete": len(times) == 8,
        "median_s": median,
        "max_s": peak,
        "fired": len(times) == 8 and (median > 60 or peak > 120),
    }


# END_BLOCK_ANSWER_CHECKS
# START_BLOCK_SEQUENTIAL_RUNNER


async def _listed(session: Any, method: str, field: str) -> list[Any]:
    rows = []
    cursor = None
    while True:
        response = await getattr(session, method)(**({"cursor": cursor} if cursor else {}))
        rows.extend(getattr(response, field))
        cursor = getattr(response, "next_cursor", None)
        if not cursor:
            return rows


def _load(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_audit(directory: Path, phase: str, rows: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    document = {"phase": phase, "calls": rows}
    temporary = directory / "audit.json.tmp"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "audit.json")


def _allowed(budget: live_budget.Budget, costs: dict[str, int]) -> bool:
    entries = budget.ledger.load()
    counts = Counter(entry.get("group") for entry in entries)
    return len(entries) + sum(costs.values()) <= min(GUARD[0], budget.total) and all(
        counts[source] + cost <= min(GUARD[1], budget.per_source) for source, cost in costs.items()
    )


def _resolve_value(value: Any, source: str, ids: dict[str, Any], context: dict[str, Any]) -> Any:
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    item_id = (ids.get(source) or [None])[0]
    if value == "$id":
        return str(item_id) if item_id is not None else None
    if value == "$id_int":
        return int(item_id) if item_id is not None else None
    name, field = value[1:].split(":", 1)
    return next((node[field] for node in _nodes(context.get(name)) if node.get(field) not in (None, "")), None)


def _resolved(
    call: dict[str, Any],
    cases: dict[str, Any],
    ids: dict[str, Any],
    context: dict[str, Any],
    completed: dict[str, Any],
    costs: dict[tuple[str, str], int],
) -> dict[str, Any] | None:
    resolved = {**call, "args": dict(call["args"])}
    source = call["source"]
    if source == "$winner":
        answer = completed.get(call["depends"], {}).get("result")
        offer = answer.get(call["winner"]) if isinstance(answer, dict) else None
        if not isinstance(offer, dict) or offer.get("source") not in cases["fixed_queries"]:
            return None
        source = offer["source"]
        resolved["source"] = source
        resolved["offer"] = offer
        cost = 1 if call["kind"] == "capture" else costs.get((source, "card"), 1)
        resolved["costs"] = {_group(cases, source): cost}
        if call["kind"] == "verify":
            identity = offer.get("url") or offer.get("product_id")
            if not identity:
                return None
            resolved["args"] = {
                "source": source,
                "product_id_or_url": identity,
                "expected_price_rub": offer.get("price_rub"),
            }
            if offer.get("identity"):
                resolved["args"]["expected_identity"] = offer["identity"]
            if source == "yandex_market" and offer.get("variant_id"):
                resolved["args"]["expected_variant_id"] = offer["variant_id"]
    elif call["kind"] == "card" and call["key"].startswith("A:"):
        resolved["args"] = _card_args(cases, source, (ids.get(source) or [None])[0])
        if resolved["args"] == {cases["fixed_queries"][source]["card"]["id_argument"]: "$id"}:
            return None
    else:
        resolved["args"] = {key: _resolve_value(value, source, ids, context) for key, value in resolved["args"].items()}
        if any(value is None for value in resolved["args"].values()):
            return None
    if source != "compare":
        kind = "card" if call["kind"] == "verify" else call["kind"]
        expected = costs.get((source, call["tool"]), costs.get((source, kind)))
        if expected is not None:
            resolved["costs"] = {_group(cases, source): expected}
    else:
        resolved["costs"] = {
            _group(cases, name): costs.get((name, "listing"), call["costs"][_group(cases, name)])
            for name in resolved["args"]["sources"]
        }
    return resolved


def _filter_paused_compare(call: dict[str, Any], cases: dict[str, Any], paused: dict[str, float]) -> dict[str, Any]:
    """Copy the resolved compare call, dropping paused sources before its ledger guard."""
    now = time.time()
    sources = call["args"]["sources"]
    dropped = sorted(source for source in sources if paused.get(source, 0) > now)
    remaining = [source for source in sources if source not in dropped]
    return {
        **call,
        "args": {**call["args"], "sources": remaining},
        "costs": {_group(cases, source): call["costs"][_group(cases, source)] for source in remaining},
        "dropped_sources": dropped,
    }


def _record(call: dict[str, Any], code: str | None = None) -> dict[str, Any]:
    return {
        "key": call["key"],
        "tool": call["tool"],
        "source": call["source"],
        "args": call["args"],
        "state": "skipped" if code else "inconclusive",
        "error_code": code,
        "requests": 0,
        "latency_s": 0.0,
        "window_hidden": None,
        "source_latencies_ms": {},
        "parsed_price": None,
        "json_ld_prices": [],
        "capture_files": [],
        "checks": {},
        "result": None,
        **({"dropped_sources": call["dropped_sources"]} if "dropped_sources" in call else {}),
    }


def _print_row(row: dict[str, Any], cases: dict[str, Any]) -> None:
    known = {s["tool"] for tools in cases["phase_a_tools"].values() for s in tools} | {
        "capture",
        "compare_prices",
        "compare_verify_offer",
    }
    tool = row["tool"] if row.get("tool") in known else "unknown"
    state = row["state"] if row.get("state") in live_matrix.STATES | {"skipped"} else "inconclusive"
    codes = set(live_matrix.ERROR_STATES) | {
        "budget_skipped",
        "missing_dependency",
        "source_paused",
        "unknown_error",
        "capture_failed",
        "check_failed",
        "missing_card_url",
    }
    code = (
        row.get("error_code")
        if row.get("error_code") in codes
        else "unknown_error"
        if row.get("error_code")
        else "none"
    )
    requests = row.get("requests") if type(row.get("requests")) is int else 0
    print(f"{tool}: {state}; error_code {code}; requests {requests}")


async def _capture_call(
    call: dict[str, Any], completed: dict[str, Any], cases: dict[str, Any], directory: Path
) -> dict[str, Any]:
    source = call["source"]
    previous = completed.get(call["depends"], {})
    if call.get("offer"):
        value = call["offer"]
    else:
        if previous.get("state") != "healthy":
            return {"error_code": "missing_dependency", "state": "skipped"}
        value = previous.get("result")
    urls = [node.get("url") for node in _nodes(value) if isinstance(node.get("url"), str) and node["url"]]
    if not urls:
        return {"error_code": "missing_card_url", "state": "skipped"}
    evidence = await capture_card(
        urls[0], _domains(cases, source), directory, re.sub(r"[^A-Za-z0-9_-]", "-", call["key"])
    )
    items = live_matrix._items(value, cases["fixed_queries"][source], "card")
    price = (
        live_matrix._price(items[0], cases["fixed_queries"][source].get("card_price_field", "price_rub"))
        if items
        else None
    )
    return {**evidence, "parsed_price": price, "state": "healthy", "error_code": None}


async def _tool_call(
    call: dict[str, Any],
    session: Any,
    tools: dict[str, Any],
    resources: set[str],
    errors: str,
    cases: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    tool = tools.get(call["tool"])
    if tool is None:
        raise RuntimeError("audit tool missing from full profile")
    hidden = call.get("window_hidden")
    started = time.monotonic()
    result = await session.call_tool(call["tool"], call["args"])
    tool_latency = time.monotonic() - started
    structured, value = _payload(result)
    code = _error(value)
    if getattr(result, "is_error", False) and code is None:
        code = "unknown_error"
    checks = check_answer(value, structured, tool, resources, errors, cases, call["source"]) if phase == "A" else {}
    if phase == "A" and any(
        checks[key] is False for key in ("schema", "guide", "error_catalogue", "personal_data", "hosts")
    ):
        state = "drift"
    else:
        state = (
            live_matrix.ERROR_STATES.get(code, "inconclusive")
            if code
            else "healthy"
            if value is not None
            else "inconclusive"
        )
    evidence: dict[str, Any] = {"result": value, "error_code": code, "state": state, "checks": checks}
    if call["source"] in cases["fixed_queries"]:
        case = cases["fixed_queries"][call["source"]]
        items = live_matrix._items(value, case, "listing" if call["kind"] == "listing" else "card")
        if items:
            evidence["parsed_price"] = live_matrix._price(
                items[0],
                case.get("listing_price_field" if call["kind"] == "listing" else "card_price_field", "price_rub"),
            )
    if call["kind"] == "compare":
        evidence.update(tool_latency_s=round(tool_latency, 6), window_hidden=hidden)
        outcomes = value.get("source_outcomes", []) if isinstance(value, dict) else []
        evidence["source_latencies_ms"] = {
            node["source"]: node["elapsed_ms"]
            for node in outcomes
            if isinstance(node, dict)
            and node.get("source") in cases["fixed_queries"]
            and type(node.get("elapsed_ms")) is int
        }
    return evidence


def _pause_from(row: dict[str, Any], paused: dict[str, float]) -> None:
    if row.get("budget_refused"):
        return  # our own budget said no; the site did not refuse anything
    value = row.get("result")
    candidates = [(row["source"], value)]
    if isinstance(value, dict):
        candidates.extend(
            (node.get("source"), node) for node in value.get("source_outcomes", []) if isinstance(node, dict)
        )
    for source, result in candidates:
        code = _error(result)
        if code in {"challenge_required", "rate_limited", "permission_denied", "transport_down"} and isinstance(
            source, str
        ):
            retry = result.get("retry_after_s") if isinstance(result, dict) else None
            until = (
                row.get("finished_epoch", time.time()) + retry
                if isinstance(retry, (int, float)) and retry > 0 and code != "challenge_required"
                else float("inf")
            )
            paused[source] = max(paused.get(source, 0), until)


def _observe_costs(
    call: dict[str, Any], row: dict[str, Any], cases: dict[str, Any], measured: dict[tuple[str, str], int]
) -> None:
    if row["state"] == "skipped" and not row.get("budget_refused"):
        return
    # A call the budget cut short needed at least one request more than it booked.
    spent = row["requests"] + (1 if row.get("budget_refused") else 0)
    source = call["source"]
    if source == "compare":
        for name in call["args"]["sources"]:
            measured[(name, "listing")] = row["requests_by_source"].get(_group(cases, name), 0)
    elif source in cases["fixed_queries"]:
        measured[(source, call["tool"])] = spent
        kind = "card" if call["kind"] == "verify" else call["kind"]
        measured[(source, kind)] = spent


def _log_size(log: Path) -> int:
    return log.stat().st_size if log.is_file() else 0


def _child_refused(log: Path, start: int) -> bool:
    """Whether the child's budget printed a refusal line after byte ``start`` of its stderr log."""
    if not log.is_file():
        return False
    with log.open("rb") as handle:
        handle.seek(start)
        text = handle.read().decode("utf-8", "replace")
    return any(line.startswith(live_budget.REFUSED_PREFIX) for line in text.splitlines())


def _capture_names(capture_dir: Path) -> set[str]:
    """The capture files the child has written so far."""
    return {path.name for path in capture_dir.glob("*.json")} if capture_dir.is_dir() else set()


def _record_comparison(
    call: dict[str, Any],
    capture_dir: Path,
    before: set[str],
    row: dict[str, Any],
    record_dir: Path,
    prefix: str,
) -> Path | None:
    """Write one recording for a compare call, from the sources the child captured.

    The child wrote what each adapter received; this turns those captures and
    the compare outcome into one recording file. A source that never answered
    is taken from the outcome, so the replay reproduces the same partial
    answer. The capture files are removed afterwards: they still hold the
    unscrubbed regions.
    """
    captures: list[dict[str, Any]] = []
    for path in sorted(capture_dir.glob("*.json")):
        if path.name in before:
            continue
        try:
            captures.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        path.unlink(missing_ok=True)
    # A card read alone (compare_verify_offer re-reading an Ozon card) is no comparison.
    if not [capture for capture in captures if "card" not in capture]:
        return None
    result = row.get("result")
    outcomes = result.get("source_outcomes") if isinstance(result, dict) else []
    document = compare_recording.assemble_recording(
        str(call["args"].get("query", "")), compare_recording.now(), captures, outcomes
    )
    stem = f"compare-{prefix}{call['key'].replace(':', '-')}"
    path = compare_recording.write_recording(record_dir, stem, document)
    print(f"recording: {stem} written; {len(captures)} source(s)")
    return path


async def execute_plan(
    plan: list[dict[str, Any]],
    session: Any,
    budget: live_budget.Budget,
    cases: dict[str, Any],
    directory: Path,
    phase: str,
    *,
    previous: list[dict[str, Any]] | None = None,
    costs: dict[tuple[str, str], int] | None = None,
    skip: set[str] | None = None,
    record_dir: Path | None = None,
    capture_dir: Path | None = None,
    record_prefix: str = "",
) -> list[dict[str, Any]]:
    """Sequential I/O shell; forecast and actual spending always come from shared ledger.

    START_CONTRACT: execute_plan
    PURPOSE: Drop paused compare sources; execute only resolved, unpaused calls within the current 36/7 guard.
    INPUTS: Pure plan, initialized/attested stdio session, file budget, trusted cases, private directory.
    OUTPUTS: Per-call evidence; skipped calls explicitly say budget/dependency/pause, never health.
    SIDE_EFFECTS: MCP calls, owned captures, atomic private checkpoints, refresh matrix ids, safe stdout.
    END_CONTRACT
    """
    from live_check import _quiet_diagnostics

    with _quiet_diagnostics():
        tools = {tool.name: tool for tool in await _listed(session, "list_tools", "tools")}
        resources = {str(resource.uri) for resource in await _listed(session, "list_resources", "resources")}
        reference = await session.read_resource("marketplace://errors")
        errors = "\n".join(getattr(block, "text", "") for block in reference.contents)
    ids = _load(REPO_ROOT / ".live-ids.json", {})
    context = {row["tool"]: row.get("result") for row in previous or [] if row.get("result") is not None}
    completed: dict[str, Any] = {}
    paused: dict[str, float] = {source: float("inf") for source in skip or set()}
    for row in previous or []:
        _pause_from(row, paused)
    rows = []
    measured = dict(costs or {})
    for original in plan:
        call = _resolved(original, cases, ids, context, completed, measured)
        reason = "missing_dependency" if call is None else None
        call = call or original
        if call["kind"] == "compare":
            call = _filter_paused_compare(call, cases, paused)
            if not call["args"]["sources"]:
                reason = "source_paused"
        elif paused.get(call["source"], 0) > time.time():
            reason = "source_paused"
        if reason is None and not _allowed(budget, call["costs"]):
            reason = "budget_skipped"
        row = _record(call, reason)
        captures_before = (
            _capture_names(capture_dir)
            if capture_dir is not None and record_dir is not None and call["kind"] == "compare"
            else None
        )
        if reason is None:
            if call["kind"] == "compare":
                with _quiet_diagnostics():
                    row["window_hidden"] = await _window_hidden()
                call = {**call, "window_hidden": row["window_hidden"]}
            before = len(budget.ledger.load())
            log_start = _log_size(directory / "child.log")
            started = time.monotonic()
            with _quiet_diagnostics():
                try:
                    evidence = (
                        await _capture_call(call, completed, cases, directory)
                        if call["kind"] == "capture"
                        else await _tool_call(call, session, tools, resources, errors, cases, phase)
                    )
                    row.update(evidence)
                except live_budget.BudgetExhausted:
                    row.update(error_code="budget_skipped", state="skipped")
                except TimeoutError:
                    row.update(error_code="timeout", state="inconclusive")
                except Exception:
                    row.update(
                        error_code="capture_failed" if call["kind"] == "capture" else "unknown_error",
                        state="inconclusive",
                    )
            if row.get("error_code") not in (None, "budget_skipped") and _child_refused(
                directory / "child.log", log_start
            ):
                # The child's budget said no mid-call; the connector reported it as its own error.
                row.update(
                    tool_error_code=row["error_code"], error_code="budget_skipped", state="skipped", budget_refused=True
                )
            row.update(
                requests=len(budget.ledger.load()) - before,
                latency_s=row.pop("tool_latency_s", round(time.monotonic() - started, 6)),
                finished_epoch=time.time(),
            )
            row["requests_by_source"] = dict(Counter(entry.get("group") for entry in budget.ledger.load()[before:]))
            _observe_costs(call, row, cases, measured)
            _pause_from(row, paused)
            if captures_before is not None and capture_dir is not None and record_dir is not None:
                _record_comparison(call, capture_dir, captures_before, row, record_dir, record_prefix)
            if row.get("result") is not None:
                context[call["tool"]] = row["result"]
            if call["kind"] == "listing":
                case = cases["fixed_queries"][call["source"]]
                saved = [
                    live_matrix._id(item, case["item_id_field"])
                    for item in live_matrix._items(row.get("result"), case, "listing")[:3]
                ]
                saved = [item_id for item_id in saved if item_id is not None]
                if saved:
                    ids[call["source"]] = saved
                    # Reuse the matrix writer; redirect only its root, not duplicate its format.
                    with _matrix_root():
                        live_matrix._save_ids({call["source"]: saved}, live_matrix._utc())
        rows.append(row)
        completed[call["key"]] = row
        _write_audit(directory, phase, rows)
        _print_row(row, cases)
    return rows


@contextlib.contextmanager
def _matrix_root() -> Iterator[None]:
    previous = live_matrix.REPO_ROOT
    live_matrix.REPO_ROOT = REPO_ROOT
    try:
        yield
    finally:
        live_matrix.REPO_ROOT = previous


# END_BLOCK_SEQUENTIAL_RUNNER
# START_BLOCK_CLI_AND_SUMMARY


def select_options(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="live_check.py audit")
    parser.add_argument("--phase", choices=("A", "B", "C"))
    parser.add_argument("--run", type=int)
    parser.add_argument("--cycle", type=int, default=1)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--skip", metavar="SOURCES", help="comma-separated sources to pause for the whole run")
    parser.add_argument(
        "--record",
        type=Path,
        metavar="DIR",
        help=(
            "write one recording per compare call into DIR (phase C only); keep DIR outside the "
            "repository, or in the gitignored .live-recordings/"
        ),
    )
    args = parser.parse_args(argv)
    if (
        args.cycle < 1
        or (args.summary and (args.phase or args.run is not None or args.record is not None or args.skip is not None))
        or (not args.summary and (args.phase is None or args.run is None))
        or (args.record is not None and args.phase != "C")
        or (args.record is not None and not _private_record_dir(args.record))
    ):
        raise ValueError("invalid audit options")
    if not args.summary:
        from mcp_core.sources import ALIASES

        cases = load_cases()
        names = [part.strip() for part in args.skip.split(",")] if args.skip is not None else []
        args.skip = {ALIASES.get(name, name) for name in names}
        if (names and not all(names)) or not args.skip <= cases["fixed_queries"].keys():
            raise ValueError("invalid audit options")
        plan_run(cases, _load(REPO_ROOT / ".live-ids.json", {}), {}, args.phase, args.run, cycle=args.cycle)
    return args


def _cycle_records(cycle: int) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((REPO_ROOT / ".live-audit" / str(cycle)).glob("*/audit.json")):
        data = _load(path, {})
        rows.extend(data.get("calls", []))
    return rows


def summary(cycle: int) -> int:
    rows = _cycle_records(cycle)
    errors = sum(bool(row.get("error_code")) for row in rows)
    trigger = compare_trigger(rows)
    print(
        f"T3 cycle {cycle}: calls {len(rows)}; errors {errors}; compare_calls {trigger['calls']}; complete {str(trigger['complete']).lower()}; trigger {str(trigger['fired']).lower()}"
    )
    print("T3 screenshot comparisons: pending owner-side Claude review; no live claim")
    return 0


def _cost_table(cases: dict[str, Any], previous: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    with _matrix_root():
        history = live_matrix._history()
    costs = {
        (source, kind): live_matrix._expected_cost(history, source, kind)
        for source in cases["fixed_queries"]
        for kind in ("listing", "card", "other")
    }
    for row in previous:
        if type(row.get("requests")) is not int or row.get("state") == "skipped":
            continue
        source = row.get("source")
        if source not in cases["fixed_queries"]:
            continue
        case = cases["fixed_queries"][source]
        kind = next((k for k in ("listing", "card") if row.get("tool") == case[k]["tool"]), None)
        if kind:
            costs[(source, kind)] = row["requests"]
    return costs


def _private_record_dir(path: Path) -> bool:
    """A recording is private material: DIR is outside the repository or under the ignored .live-recordings/."""
    resolved = path.resolve()
    root = REPO_ROOT.resolve()
    outside = resolved != root and root not in resolved.parents
    return outside or (root / ".live-recordings") in (resolved, *resolved.parents)


async def run(argv: list[str], budget: live_budget.Budget) -> int:
    args = select_options(argv)
    if args.summary:
        return summary(args.cycle)
    cases = load_cases()
    directory = REPO_ROOT / ".live-audit" / str(args.cycle) / f"{args.phase}{args.run}"
    if (directory / "audit.json").exists():
        raise ValueError("audit run already recorded; use a new run/cycle, do not overwrite evidence")
    capture_dir = (directory / "captures") if args.record is not None else None
    previous = _cycle_records(args.cycle)
    costs = _cost_table(cases, previous)
    ids = _load(REPO_ROOT / ".live-ids.json", {})
    guard = (min(GUARD[0], budget.total), min(GUARD[1], budget.per_source))
    # Phase A's two runs split one inventory by cost. Both plan from history alone, so the
    # other run's measurements cannot move a tool between them (a repeat or a miss).
    plan_costs = _cost_table(cases, []) if args.phase == "A" else costs
    plan = plan_run(cases, ids, plan_costs, args.phase, args.run, cycle=args.cycle, guard=guard)
    if args.phase == "A":
        done = {row["key"] for row in previous if str(row.get("key", "")).startswith("A:")}
        keys = {call["key"] for call in plan}
        whole = {call["key"] for n in (1, 2) for call in plan_run(cases, ids, plan_costs, "A", n, guard=guard)}
        if done and (done & keys or done | keys != whole):
            raise ValueError("phase A split changed since the other run; start a new cycle")
    async with open_session(budget, directory, capture_dir=capture_dir) as session:
        rows = await execute_plan(
            plan,
            session,
            budget,
            cases,
            directory,
            args.phase,
            previous=previous,
            costs=costs,
            skip=args.skip,
            record_dir=args.record,
            capture_dir=capture_dir,
            record_prefix=f"c{args.cycle}-",
        )
    if args.phase == "C":
        summary(args.cycle)
    return 3 if rows and all(row.get("error_code") == "budget_skipped" for row in rows) else 0


# END_BLOCK_CLI_AND_SUMMARY
