#!/usr/bin/env python3
"""On-demand marketplace matrix; dispatched by live_check.py, never scheduled.

START_MODULE_CONTRACT
PURPOSE: Probe fixed listings and their first cards through the shared call path and ledger.
SCOPE: Sequential guarded calls, catalogue states, same-id RUB deltas, whitelisted local history,
       first-three audit ids, measured-cost planning and previous-release stdout diff; no publishing.
DEPENDS: live_check._call_tool,_quiet_diagnostics; live_budget; mcp_core errors,sources,resilience,version;
         stdlib asyncio,datetime,json,re,subprocess,time,pathlib,urllib.parse.
LINKS: M-LIVE-MATRIX, R-LIVE-MATRIX, R-LIVE-MATRIX.whitelist, R-LIVE-MATRIX.guard, R-LIVE-PAUSE,
       R-LIVE-MATRIX.delta, R-LIVE-MATRIX.probes.
START_MODULE_MAP
  load_cases           read the data-driven fixed-query/tool/response-field registry
  select_cases         validate --only/--skip without importing connectors
  _expected_cost       newest measured ledger cost, then the seed, then one request
  _card_plan           remove costliest sources' cards until the expected plan fits
  _probe               re-read the ledger before and after the shared tool call
  _delta               compare integer RUB prices only with positively matched identity
  _print_diff          newest previous-release run, falling back to the previous run
  run                  sequential probes, safe rows, LF append and audit ids
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import datetime as dt
import http.client
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import live_budget

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_CASES_PATH = REPO_ROOT / "scripts" / "audit_cases.json"
PLAN_LIMIT = 36
SOURCE_LIMIT = 7
SEED_COSTS = {("ozon", "listing"): 3}

# START_BLOCK_CASES_AND_STATES
# The catalogue served by marketplace://errors is mcp_core.errors.ErrorCode.
# Unknown/new codes stay inconclusive; the coverage test demands an explicit decision.
ERROR_STATES = {
    "auth_missing": "inconclusive",
    "rate_limited": "refused",
    "timeout": "inconclusive",
    "transport_down": "inconclusive",
    "parser_drift": "drift",
    "bad_request": "inconclusive",
    "permission_denied": "refused",
    "challenge_required": "needs_human",
    "not_found": "inconclusive",
}
STATES = frozenset({"healthy", "drift", "needs_human", "refused", "inconclusive"})
# Probes that sent nothing: over the plan's budget, or the marketplace's own pause still runs.
SKIPPED = frozenset({"budget_skipped", "retry_after_skipped"})


def load_cases() -> dict[str, Any]:
    data = json.loads(AUDIT_CASES_PATH.read_text(encoding="utf-8"))
    from mcp_core.sources import SOURCES

    expected = {spec.name for spec in SOURCES if spec.kind == "marketplace" and spec.name != "mpstats"}
    cases = data.get("fixed_queries") if isinstance(data, dict) else None
    if not isinstance(cases, dict) or set(cases) != expected:
        raise ValueError("invalid fixed-query registry")
    return cases


def select_cases(argv: list[str]) -> dict[str, Any]:
    from mcp_core.sources import ALIASES

    cases = load_cases()
    selections: dict[str, set[str]] = {}
    if len(argv) % 2:
        raise ValueError("invalid matrix options")
    for option, value in zip(argv[::2], argv[1::2], strict=True):
        if option not in {"--only", "--skip"} or option in selections:
            raise ValueError("invalid matrix options")
        names = [part.strip() for part in value.split(",")]
        canonical = {ALIASES.get(name, name) for name in names}
        if not all(names) or not canonical <= cases.keys():
            raise ValueError("unknown matrix source")
        selections[option] = canonical
    chosen = selections.get("--only", set(cases)) - selections.get("--skip", set())
    if not chosen:
        raise ValueError("empty matrix selection")
    return {source: case for source, case in cases.items() if source in chosen}


def _error_code(value: Any) -> str | None:
    if not isinstance(value, dict):
        return "unknown_error"
    code = value.get("error")
    if code is None and value.get("status") != "error":
        return None
    return code if isinstance(code, str) and code in ERROR_STATES else "unknown_error"


def _objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _items(value: Any, case: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    field = case.get("items_field", "items") if kind == "listing" else case.get("card_items_field")
    return _objects(value.get(field)) if field else [value]


def _price(item: dict[str, Any], field: str) -> float | None:
    from mcp_core.resilience import coerce_price

    price = coerce_price(item.get(field))
    return price if price is not None and price > 0 else None


# END_BLOCK_CASES_AND_STATES
# START_BLOCK_METADATA_AND_HISTORY


def _utc() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _package_version() -> str:
    from mcp_core import __version__

    return __version__ if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][0-9A-Za-z.-]+)?", __version__) else "unknown"


def _commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    return text if result.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{4,64}", text) else None


async def _chrome_major() -> int | None:
    from mcp_core.transport import chrome_cdp

    if not chrome_cdp.CDP_IS_LOOPBACK:
        return None
    try:
        # This local version read neither navigates nor autostarts nor books a request.
        info = await asyncio.to_thread(chrome_cdp._cdp_http_json, "/json/version", timeout_s=2.0)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    browser = info.get("Browser") if isinstance(info, dict) else None
    match = re.fullmatch(r"(?:Headless)?Chrome/([0-9]+)(?:\.[0-9]+)*", browser) if isinstance(browser, str) else None
    return int(match[1]) if match else None


def _history() -> list[dict[str, Any]]:
    path = REPO_ROOT / ".live-history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _expected_cost(history: list[dict[str, Any]], source: str, kind: str) -> int:
    for row in reversed(history):
        probe = row.get(kind)
        if row.get("source") != source or not isinstance(probe, dict) or probe.get("error_code") in SKIPPED:
            continue
        requests = probe.get("requests")
        if type(requests) is int and requests >= 0:
            return requests
    return SEED_COSTS.get((source, kind), 1)


def _card_plan(cases: dict[str, Any], history: list[dict[str, Any]]) -> set[str]:
    costs = {
        source: (_expected_cost(history, source, "listing"), _expected_cost(history, source, "card"))
        for source in cases
    }
    total = sum(sum(pair) for pair in costs.values())
    cards = set(cases)
    for source in sorted(cases, key=lambda name: sum(costs[name]), reverse=True):
        if total <= PLAN_LIMIT:
            break
        cards.remove(source)
        total -= costs[source][1]
        print(f"{source}: dropped card probe; expected requests {total}")
    return cards


def _save_ids(ids: dict[str, list[Any]], run_id: str) -> None:
    if not ids:
        return
    path = REPO_ROOT / ".live-ids.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = previous if isinstance(previous, dict) else {}
    data.update(ids)
    data["run_id"] = run_id
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")


def _print_diff(history: list[dict[str, Any]], rows: list[dict[str, Any]], version: str, run_id: str) -> None:
    earlier = [row for row in history if isinstance(row.get("run_id"), str) and row["run_id"] < run_id]
    previous = next((row for row in reversed(earlier) if row.get("package_version") != version), None)
    if previous is None:
        previous = earlier[-1] if earlier else None
    if previous is None:
        print("diff: no previous run")
        return
    baseline = {row.get("source"): row for row in earlier if row.get("run_id") == previous["run_id"]}
    for row in rows:
        old = baseline.get(row["source"])
        if old is None:
            print(f"diff {row['source']}: no previous row")
            continue
        state = old.get("state") if old.get("state") in STATES else "inconclusive"
        listing = old.get("listing") if isinstance(old.get("listing"), dict) else {}
        items = listing.get("items") if type(listing.get("items")) is int else 0
        priced = listing.get("priced") if type(listing.get("priced")) is int else 0
        print(
            f"diff {row['source']}: state {state} → {row['state']}; "
            f"items {items} → {row['listing']['items']}; priced {priced} → {row['listing']['priced']}"
        )


# END_BLOCK_METADATA_AND_HISTORY
# START_BLOCK_GUARDED_PROBES


def _counts(budget: live_budget.Budget, group: str) -> tuple[int, int]:
    entries = budget.ledger.load()
    return len(entries), sum(entry.get("group") == group for entry in entries)


async def _probe(
    source: str,
    case: dict[str, Any],
    kind: str,
    kwargs: dict[str, Any],
    budget: live_budget.Budget,
    history: list[dict[str, Any]],
) -> tuple[dict[str, Any], Any, bool]:
    """R-LIVE-MATRIX.guard: forecast before calling, measure only from ledger deltas."""
    from live_check import _call_tool, _quiet_diagnostics

    group = case.get("ledger_source", source)
    total, before = _counts(budget, group)
    expected = _expected_cost(history, source, kind)
    total_blocked = total + expected > min(PLAN_LIMIT, budget.total)
    source_blocked = before + expected > min(SOURCE_LIMIT, budget.per_source)
    probe = {"state": "inconclusive", "error_code": "budget_skipped", "priced": 0, "latency_s": 0.0, "requests": 0}
    if kind == "listing":
        probe["items"] = 0
    if total_blocked or source_blocked:
        return probe, None, total_blocked
    pause = budget.pause(group)
    if pause is not None:
        # R-LIVE-PAUSE: the site asked for a pause; asking again early is a breach, not a probe.
        probe["error_code"] = "retry_after_skipped"
        print(f"{source}: paused by the marketplace until {pause.get('until_utc')} UTC; {kind} probe skipped")
        return probe, None, False
    started = time.monotonic()
    result: Any = None
    code: str | None = None
    # Connector/budget diagnostics can contain addresses, titles, paths and messages.
    # They must never bypass the matrix whitelist, including on a caught exception.
    with _quiet_diagnostics():
        try:
            result = await _call_tool(case["module"], case[kind]["tool"], kwargs)
            code = _error_code(result)
        except live_budget.PauseActive:
            code = "retry_after_skipped"
        except live_budget.BudgetExhausted:
            code = "budget_skipped"
        except TimeoutError:
            code = "timeout"
        except Exception as exc:
            raw_code = getattr(exc, "code", None)
            if isinstance(raw_code, str) and raw_code in ERROR_STATES:
                code = raw_code
            else:
                try:
                    code = _error_code(json.loads(str(exc)))
                except (ValueError, TypeError):
                    code = "unknown_error"
    _, after = _counts(budget, group)
    items = _items(result, case, kind)
    field = case.get(f"{kind}_price_field", "price_rub")
    priced = sum(_price(item, field) is not None for item in items)
    probe.update(
        state=ERROR_STATES.get(code, "inconclusive") if code else "healthy" if priced else "drift",
        error_code=code,
        priced=priced,
        latency_s=round(time.monotonic() - started, 6),
        requests=after - before,
    )
    if kind == "listing":
        probe["items"] = len(items)
    return probe, result, False


def _id(item: dict[str, Any], field: str) -> str | int | None:
    value = item.get(field)
    return value if (type(value) is int and value > 0) or (isinstance(value, str) and value.strip()) else None


def _same_id(expected: Any, observed: Any) -> bool:
    if expected is None or observed is None:
        return False
    if str(expected) == str(observed):
        return True
    if not isinstance(observed, str) or not observed.startswith(("https://", "http://")):
        return False
    # Ozon cards expose only their canonical URL, with a SKU path token or suffix.
    # Never treat a number elsewhere in a title/URL/query as product identity.
    path = urlsplit(observed).path.rstrip("/")
    return str(expected) in path.split("/") or path.endswith("-" + str(expected))


def _delta(case: dict[str, Any], listing: Any, card: Any) -> dict[str, int] | None:
    from mcp_core.sources import BY_NAME

    # The registry's native currency is authoritative, even if an untrusted result
    # happens to carry an additional RUB-looking field (Taobao remains CNY).
    source = next((name for name, spec in BY_NAME.items() if spec.module == case["module"]), None)
    if source is None or BY_NAME[source].currency != "rub":
        return None
    listed = _items(listing, case, "listing")
    if not listed:
        return None
    first = listed[0]
    item_id = _id(first, case["item_id_field"])
    for item in _items(card, case, "card"):
        if not _same_id(item_id, _id(item, case["card_id_field"])):
            continue
        if any(not _same_id(_id(first, field), _id(item, field)) for field in case.get("identity_fields", [])):
            continue
        left = _price(first, case.get("listing_price_field", "price_rub"))
        right = _price(item, case.get("card_price_field", "price_rub"))
        if left is not None and right is not None and int(left) != int(right):
            return {"listing_rub": int(left), "card_rub": int(right)}
    return None


# END_BLOCK_GUARDED_PROBES
# START_BLOCK_RUN


async def run(argv: list[str], budget: live_budget.Budget) -> int:
    """Build explicit whitelist rows; never serialize a raw tool response or error.

    START_CONTRACT: run
    PURPOSE: Run the selected matrix with guarded sequential listing/first-card probes.
    INPUTS: Validated selection options and the already installed shared request budget.
    OUTPUTS: Exit 0 for recorded states, 3 for whole-run budget refusal, 4 from caller on setup/usage failure.
    SIDE_EFFECTS: Shared calls and ledger, append-only LF history, audit ids, whitelisted stdout.
    END_CONTRACT
    """
    cases = select_cases(argv)
    run_id = _utc()
    history = _history()
    cards = _card_plan(cases, history)
    version, commit, chrome = _package_version(), _commit(), await _chrome_major()
    rows: list[dict[str, Any]] = []
    ids: dict[str, list[Any]] = {}
    attempted = False
    total_refused = False
    for source, case in cases.items():
        listing, result, blocked = await _probe(source, case, "listing", case["listing"]["kwargs"], budget, history)
        attempted |= listing["error_code"] not in SKIPPED
        total_refused |= blocked
        items = _items(result, case, "listing")
        saved = [_id(item, case["item_id_field"]) for item in items[:3]]
        saved = [value for value in saved if value is not None]
        if saved:
            ids[source] = saved
        card = None
        card_result = None
        item_id = _id(items[0], case["item_id_field"]) if items else None
        if source in cards and item_id is not None and listing["state"] != "needs_human":
            argument = case["card"]["id_argument"]
            # Connector direct functions take string IDs, except WB's typed batch.
            value: Any = [int(item_id)] if case["card"].get("id_many") else str(item_id)
            # An accepted native URL can retain host identity (Taobao vs Tmall)
            # while audit ids and delta matching still use the first item's id.
            input_field = case.get("card_input_field")
            if input_field:
                value = _id(items[0], input_field) or value
            card, card_result, _ = await _probe(source, case, "card", {argument: value}, budget, history)
        state, error_code = listing["state"], listing["error_code"]
        if state == "healthy" and card is not None:
            state, error_code = card["state"], card["error_code"]
        elif state == "healthy" and source in cards and item_id is None:
            state, error_code = "inconclusive", "missing_item_id"
        row = {
            "run_id": run_id,
            "utc": _utc(),
            "source": source,
            "state": state,
            "error_code": error_code,
            "listing": listing,
            "card": card,
            "listing_card_delta": _delta(case, result, card_result),
            "package_version": version,
            "commit": commit,
            "chrome_major": chrome,
        }
        rows.append(row)
        with (REPO_ROOT / ".live-history.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{source}: {state}; error_code {error_code}; items {listing['items']}; priced {listing['priced']}")
    _save_ids(ids, run_id)
    _print_diff(history, rows, version, run_id)
    return 3 if total_refused and not attempted else 0


# END_BLOCK_RUN
