"""Offline acceptance checks for the maintainer's P1-1 matrix brief.

START_MODULE_CONTRACT
PURPOSE: Prove the matrix's whitelist, ledger guard, same-id RUB delta and fixed probes through the public CLI.
SCOPE: Fake marketplace calls, preflight and metadata; real temporary ledger/history/ids files; no network or Chrome.
DEPENDS: live_check, live_matrix, live_budget; mcp_core errors/sources; pytest; stdlib.
LINKS: M-LIVE-MATRIX, R-LIVE-MATRIX, R-LIVE-MATRIX.whitelist, R-LIVE-MATRIX.guard, R-LIVE-MATRIX.delta, R-LIVE-MATRIX.probes,
       R-LIVE-PAUSE.
START_MODULE_MAP
  MatrixEdges                 offline I/O edges using the actually installed temporary ledger
  matrix_edges                isolate files, installation, preflight and metadata
  _native_listing/_native_card representative connector wire shapes, independent of extraction configuration
  _history_row                earlier release rows with the exact required schema
  test_*                      public-main acceptance checks and offline fixed-tool signature validation
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib
import inspect
import io
import json
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import live_budget
import live_check
from mcp_core.errors import ErrorCode
from mcp_core.output_schema import registered_tools
from mcp_core.resources import render_error_reference
from mcp_core.sources import SOURCES

# START_BLOCK_OFFLINE_EDGES

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_NAMES = tuple(spec.name for spec in SOURCES if spec.kind == "marketplace" and spec.name != "mpstats")
BUDGET_GROUPS = {"yandex_market": "yandex", "detsky_mir": "detmir"}
ROW_KEYS = {
    "run_id",
    "utc",
    "source",
    "state",
    "error_code",
    "listing",
    "card",
    "listing_card_delta",
    "package_version",
    "commit",
    "chrome_major",
}
LISTING_KEYS = {"state", "error_code", "items", "priced", "latency_s", "requests"}
CARD_KEYS = {"state", "error_code", "priced", "latency_s", "requests"}
EXPECTED_STATES = {
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
NATIVE_ID_FIELDS = {
    "wildberries": "nm_id",
    "ozon": "sku",
    "yandex_market": "product_id",
    "detsky_mir": "product_id",
    "avito": "item_id",
    "taobao": "item_id",
    "megamarket": "item_id",
    "lamoda": "sku",
    "dns": "product_id",
    "citilink": "product_id",
    "aliexpress": "item_id",
    "cian": "offer_id",
}
LEAK = "PRIVATE_SENTINEL_Мария"
PRIVATE_URL = "https://private.invalid/PRIVATE_SENTINEL_Мария"
PRIVATE_PATH = r"C:\Users\PRIVATE_SENTINEL_Мария\secret.txt"


def _native_listing(
    source: str, prices: tuple[Any, ...] = (1000,), ids: tuple[Any, ...] | None = None
) -> dict[str, Any]:
    """Wire fields are read from the connector models, not the matrix's case file."""
    if ids is None:
        ids = tuple(str(101 + index) for index in range(len(prices)))
    price_field = "price" if source == "ozon" else "price_cny" if source == "taobao" else "price_rub"
    items = [
        {NATIVE_ID_FIELDS[source]: item_id, price_field: price, "title": LEAK, "message": PRIVATE_PATH}
        for item_id, price in zip(ids, prices, strict=True)
    ]
    return {"status": "success", "items": items, "count": len(items), "query": LEAK, "message": PRIVATE_PATH}


def _native_card(source: str, price: Any = 1000, item_id: Any = "101") -> dict[str, Any]:
    if source == "ozon":
        return {"status": "success", "price": price, "url": f"https://www.ozon.ru/product/{item_id}/", "title": LEAK}
    price_field = "price_cny" if source == "taobao" else "price_rub"
    item = {NATIVE_ID_FIELDS[source]: item_id, price_field: price, "title": LEAK, "message": PRIVATE_PATH}
    if source == "wildberries":
        return {"items": [item], "count": 1}
    if source == "detsky_mir":
        return {"product": item}
    return item


def _history_row(
    source: str = "ozon",
    *,
    version: str = "1.0.0",
    run_id: str = "2025-01-01T00:00:00Z",
    state: str = "healthy",
    items: int = 1,
    priced: int = 1,
    listing_requests: Any = 1,
    card_requests: Any = 1,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "utc": run_id,
        "source": source,
        "state": state,
        "error_code": None,
        "listing": {
            "state": state,
            "error_code": None,
            "items": items,
            "priced": priced,
            "latency_s": 0.1,
            "requests": listing_requests,
        },
        "card": {"state": "healthy", "error_code": None, "priced": 1, "latency_s": 0.1, "requests": card_requests},
        "listing_card_delta": None,
        "package_version": version,
        "commit": "abcdef0",
        "chrome_major": 130,
    }


class MatrixEdges:
    def __init__(self, root: Path, matrix: Any) -> None:
        self.root = root
        self.matrix = matrix
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.events: list[str] = []
        self.results: dict[tuple[str, str], Any] = {}
        self.costs: dict[tuple[str, str], int] = {("ozon", "listing"): 3}
        self.extra_groups: dict[tuple[str, str], list[str]] = {}
        self.preflight_ok = True
        self.version = "2.0.0"
        self.cases = matrix.load_cases()
        self.by_tool = {
            (case["module"], case[kind]["tool"]): (source, kind)
            for source, case in self.cases.items()
            for kind in ("listing", "card")
        }

    async def preflight(self) -> bool:
        self.events.append("preflight")
        return self.preflight_ok

    def install(self) -> live_budget.Budget:
        self.events.append("install")
        budget = live_budget.Budget.from_env()
        # Use the real installed_budget() accessor, but install no transport hooks.
        live_budget._installation = live_budget._Installation(budget, [])
        return budget

    async def chrome_major(self) -> int:
        return 131

    async def call(self, module_path: str, tool_name: str, kwargs: dict[str, Any]) -> Any:
        source, kind = self.by_tool[(module_path, tool_name)]
        self.events.append(f"{source}:{kind}")
        self.calls.append((source, kind, copy.deepcopy(kwargs)))
        assert os.environ["CHROME_CHALLENGE_HANDOFF_S"] == "0"
        budget = live_budget.installed_budget()
        assert budget is not None
        assert budget.ledger.path == self.root / "ledger.json"
        entries = budget.ledger.load()
        group = BUDGET_GROUPS.get(source, source)
        entries.extend({"group": group} for _ in range(self.costs.get((source, kind), 1)))
        entries.extend({"group": other} for other in self.extra_groups.get((source, kind), []))
        budget.ledger.save(entries)
        result = self.results.get((source, kind))
        if isinstance(result, Exception):
            raise result
        if result is None:
            result = _native_listing(source) if kind == "listing" else _native_card(source)
        return copy.deepcopy(result)

    def seed_ledger(self, groups: list[str]) -> None:
        live_budget.Ledger(self.root / "ledger.json").save([{"group": group} for group in groups])

    def seed_history(self, rows: list[dict[str, Any]]) -> None:
        with (self.root / ".live-history.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def rows(self) -> list[dict[str, Any]]:
        path = self.root / ".live-history.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def run(self, *argv: str) -> int:
        return live_check.main(["matrix", *argv])


@pytest.fixture
def matrix_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MatrixEdges]:
    matrix = importlib.import_module("live_matrix")
    live_budget.uninstall()
    edge = MatrixEdges(tmp_path, matrix)
    for name in ("LIVE_BUDGET_TOTAL", "LIVE_BUDGET_PER_SOURCE", "LIVE_BUDGET_GAP_S", "CHROME_CHALLENGE_HANDOFF_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(live_check, "_preflight", edge.preflight)
    monkeypatch.setattr(live_check, "_call_tool", edge.call)
    monkeypatch.setattr(live_budget, "install", edge.install)
    monkeypatch.setattr(live_budget, "_installation", None)
    monkeypatch.setattr(matrix, "REPO_ROOT", tmp_path)
    # AUDIT_CASES_PATH deliberately remains the real workspace's fixed registry.
    monkeypatch.setattr(matrix, "_package_version", lambda: edge.version)
    monkeypatch.setattr(matrix, "_commit", lambda: "abcdef1")
    monkeypatch.setattr(matrix, "_chrome_major", edge.chrome_major)
    try:
        yield edge
    finally:
        live_budget.uninstall()


# END_BLOCK_OFFLINE_EDGES
# START_BLOCK_WHITELIST_AND_STATES


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.whitelist")
@pytest.mark.parametrize(
    "failure", [None, "parser_drift", PRIVATE_PATH], ids=["success", "catalogue-error", "malicious-unknown-code"]
)
def test_exact_recursive_whitelist_and_no_private_result_or_error_output(
    matrix_edges: MatrixEdges,
    capsys: pytest.CaptureFixture[str],
    failure: str | None,
) -> None:
    edge = matrix_edges
    if failure is not None:
        edge.results[("ozon", "listing")] = {
            "error": failure,
            "message": PRIVATE_PATH,
            "title": LEAK,
            "url": PRIVATE_URL,
        }
    else:
        edge.results[("ozon", "card")] = _native_card("ozon", 1050)
    assert edge.run("--only", "ozon") == 0
    rows = edge.rows()
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == ROW_KEYS
    assert set(row["listing"]) == LISTING_KEYS
    if row["card"] is not None:
        assert set(row["card"]) == CARD_KEYS
    if row["listing_card_delta"] is not None:
        assert set(row["listing_card_delta"]) == {"listing_rub", "card_rub"}
        assert all(type(value) is int for value in row["listing_card_delta"].values())
    for timestamp in (row["run_id"], row["utc"]):
        assert dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).utcoffset() == dt.timedelta(0)
    assert row["package_version"] == "2.0.0"
    assert row["commit"] == "abcdef1"
    assert row["chrome_major"] == 131
    assert row["listing"]["requests"] == 3
    assert isinstance(row["listing"]["latency_s"], int | float)
    assert row["listing"]["latency_s"] >= 0
    if failure == PRIVATE_PATH:
        assert row["error_code"] == "unknown_error"
        assert row["listing"]["error_code"] == "unknown_error"
    output = capsys.readouterr()
    text = json.dumps(rows, ensure_ascii=False) + output.out + output.err
    for forbidden in (LEAK, PRIVATE_URL, PRIVATE_PATH, "iphone 16 128gb", str(edge.root)):
        assert forbidden not in text
    raw = (edge.root / ".live-history.jsonl").read_bytes()
    assert raw.endswith(b"\n") and b"\r\n" not in raw


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.whitelist")
def test_raw_caller_exception_and_logs_do_not_leak(
    matrix_edges: MatrixEdges,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fastmcp.exceptions import ToolError

    edge = matrix_edges
    edge.results[("ozon", "listing")] = ToolError(json.dumps({"error": "timeout", "message": PRIVATE_PATH}))
    original = edge.call

    async def noisy_caller(module_path: str, tool_name: str, kwargs: dict[str, Any]) -> Any:
        print(PRIVATE_URL)
        print(PRIVATE_PATH, file=sys.stderr)
        return await original(module_path, tool_name, kwargs)

    async def noisy_preflight() -> bool:
        print(PRIVATE_PATH, file=sys.stderr)
        return await edge.preflight()

    monkeypatch.setattr(live_check, "_preflight", noisy_preflight)
    monkeypatch.setattr(live_check, "_call_tool", noisy_caller)
    assert edge.run("--only", "ozon") == 0
    row = edge.rows()[0]
    assert row["state"] == "inconclusive"
    assert row["error_code"] == "timeout"
    output = capsys.readouterr()
    assert LEAK not in json.dumps(row, ensure_ascii=False) + output.out + output.err


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.whitelist")
def test_prebound_logger_is_suppressed_and_logging_configuration_is_restored(
    matrix_edges: MatrixEdges, monkeypatch: pytest.MonkeyPatch
) -> None:
    edge = matrix_edges
    sink = io.StringIO()
    logger = logging.getLogger("matrix-prebound-logger")
    monkeypatch.setattr(logger, "handlers", [logging.StreamHandler(sink)])
    monkeypatch.setattr(logger, "level", logging.WARNING)
    monkeypatch.setattr(logger, "propagate", False)
    previous = logging.root.manager.disable
    original = edge.call

    async def noisy_caller(module_path: str, tool_name: str, kwargs: dict[str, Any]) -> Any:
        logger.warning(PRIVATE_PATH)
        return await original(module_path, tool_name, kwargs)

    async def noisy_preflight() -> bool:
        logger.warning(PRIVATE_PATH)
        return await edge.preflight()

    monkeypatch.setattr(live_check, "_preflight", noisy_preflight)
    monkeypatch.setattr(live_check, "_call_tool", noisy_caller)
    assert edge.run("--only", "ozon") == 0
    assert sink.getvalue() == ""
    assert logging.root.manager.disable == previous


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_mapping_covers_the_repository_error_catalogue(matrix_edges: MatrixEdges) -> None:
    catalogue = {code.value for code in ErrorCode}
    assert catalogue <= set(matrix_edges.matrix.ERROR_STATES)
    document_codes = set(re.findall(r"^\| `([^`]+)` \|", render_error_reference(), re.MULTILINE))
    assert document_codes == catalogue
    assert {code: matrix_edges.matrix.ERROR_STATES[code] for code in catalogue} == EXPECTED_STATES


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
@pytest.mark.parametrize("code,state", list(EXPECTED_STATES.items()))
def test_each_catalogue_error_is_mapped_through_public_main(matrix_edges: MatrixEdges, code: str, state: str) -> None:
    matrix_edges.results[("ozon", "listing")] = {"error": code, "message": PRIVATE_PATH}
    assert matrix_edges.run("--only", "ozon") == 0
    row = matrix_edges.rows()[0]
    assert row["listing"]["state"] == state
    assert row["state"] == state
    assert row["error_code"] == code


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
@pytest.mark.parametrize("prices,expected", [((), "drift"), ((None,), "drift"), ((1000,), "healthy")])
def test_success_requires_items_and_at_least_one_price(
    matrix_edges: MatrixEdges,
    prices: tuple[Any, ...],
    expected: str,
) -> None:
    matrix_edges.results[("ozon", "listing")] = _native_listing("ozon", prices)
    assert matrix_edges.run("--only", "ozon") == 0
    row = matrix_edges.rows()[0]
    assert row["listing"]["state"] == expected
    assert row["listing"]["items"] == len(prices)
    assert row["listing"]["priced"] == sum(price is not None for price in prices)
    assert row["state"] == expected


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
@pytest.mark.parametrize(
    "card_result,expected,code",
    [
        (_native_card("ozon", None), "drift", None),
        ({"error": "permission_denied", "message": PRIVATE_PATH}, "refused", "permission_denied"),
        ({"error": "challenge_required", "message": PRIVATE_PATH}, "needs_human", "challenge_required"),
    ],
)
def test_healthy_listing_takes_the_card_state(
    matrix_edges: MatrixEdges,
    card_result: dict[str, Any],
    expected: str,
    code: str | None,
) -> None:
    matrix_edges.results[("ozon", "card")] = card_result
    assert matrix_edges.run("--only", "ozon") == 0
    row = matrix_edges.rows()[0]
    assert row["listing"]["state"] == "healthy"
    assert row["card"]["state"] == expected
    assert row["state"] == expected
    assert row["error_code"] == code


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_needs_human_stops_that_source_but_continues_the_matrix(matrix_edges: MatrixEdges) -> None:
    result = _native_listing("ozon")
    result.update(error="challenge_required", message=PRIVATE_PATH)
    matrix_edges.results[("ozon", "listing")] = result
    assert matrix_edges.run("--only", "ozon,yandex_market") == 0
    assert [(source, kind) for source, kind, _ in matrix_edges.calls] == [
        ("ozon", "listing"),
        ("yandex_market", "listing"),
        ("yandex_market", "card"),
    ]
    rows = {row["source"]: row for row in matrix_edges.rows()}
    assert rows["ozon"]["state"] == "needs_human"
    assert rows["ozon"]["card"] is None
    assert rows["yandex_market"]["state"] == "healthy"


# END_BLOCK_WHITELIST_AND_STATES
# START_BLOCK_PRICE_DELTA


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.delta")
@pytest.mark.parametrize(
    "listing_price,card_price,card_id,expected",
    [
        (1000, 1050, "101", {"listing_rub": 1000, "card_rub": 1050}),
        (1000.0, 1050.0, "101", {"listing_rub": 1000, "card_rub": 1050}),
        (1000, 1000, "101", None),
        (None, 1050, "101", None),
        (1000, None, "101", None),
        (1000, 1050, "999", None),
    ],
)
def test_delta_only_when_both_integer_rub_prices_differ_for_the_same_id(
    matrix_edges: MatrixEdges,
    listing_price: Any,
    card_price: Any,
    card_id: str,
    expected: Any,
) -> None:
    edge = matrix_edges
    edge.results[("ozon", "listing")] = _native_listing("ozon", (listing_price,))
    edge.results[("ozon", "card")] = _native_card("ozon", card_price, card_id)
    assert edge.run("--only", "ozon") == 0
    row = edge.rows()[0]
    assert row["listing_card_delta"] == expected
    if expected is not None:
        assert all(type(value) is int for value in row["listing_card_delta"].values())
    assert [(source, kind) for source, kind, _ in edge.calls] == [("ozon", "listing"), ("ozon", "card")]
    assert str(next(iter(edge.calls[1][2].values()))) == "101"
    if listing_price is not None and card_price is not None:
        assert row["state"] == "healthy"  # A delta never upgrades or downgrades health.


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.delta")
def test_taobao_cny_prices_never_masquerade_as_rub_delta(matrix_edges: MatrixEdges) -> None:
    listing = _native_listing("taobao", (100,))
    card = _native_card("taobao", 120)
    listing["items"][0]["price_rub"] = 9999
    card["price_rub"] = 11111
    matrix_edges.results[("taobao", "listing")] = listing
    matrix_edges.results[("taobao", "card")] = card
    assert matrix_edges.run("--only", "taobao") == 0
    row = matrix_edges.rows()[0]
    assert row["listing"]["priced"] == 1
    assert row["card"]["priced"] == 1
    assert row["state"] == "healthy"
    assert row["listing_card_delta"] is None


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.delta")
@pytest.mark.parametrize(
    "listing_sku,card_sku,expected",
    [
        ("offer-a", "offer-a", {"listing_rub": 1000, "card_rub": 1050}),
        ("offer-a", "offer-b", None),
        ("offer-a", "", None),
        ("", "offer-a", None),
    ],
)
def test_yandex_delta_requires_same_product_and_known_same_offer_sku(
    matrix_edges: MatrixEdges,
    listing_sku: str,
    card_sku: str,
    expected: Any,
) -> None:
    listing = _native_listing("yandex_market")
    listing["items"][0]["sku_id"] = listing_sku
    card = _native_card("yandex_market", 1050)
    card["sku_id"] = card_sku
    matrix_edges.results[("yandex_market", "listing")] = listing
    matrix_edges.results[("yandex_market", "card")] = card
    assert matrix_edges.run("--only", "yandex_market") == 0
    row = matrix_edges.rows()[0]
    assert row["listing_card_delta"] == expected
    assert row["state"] == "healthy"


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.delta")
@pytest.mark.parametrize(
    "listing_unit,card_unit,expected",
    [
        ("month", "month", {"listing_rub": 1000, "card_rub": 1050}),
        ("month", "day", None),
        ("month", None, None),
        (None, "month", None),
    ],
)
def test_cian_delta_requires_known_equal_price_units(
    matrix_edges: MatrixEdges, listing_unit: str | None, card_unit: str | None, expected: Any
) -> None:
    listing = _native_listing("cian")
    listing["items"][0]["price_unit"] = listing_unit
    card = _native_card("cian", 1050)
    card["price_unit"] = card_unit
    matrix_edges.results[("cian", "listing")] = listing
    matrix_edges.results[("cian", "card")] = card
    assert matrix_edges.run("--only", "cian") == 0
    row = matrix_edges.rows()[0]
    assert row["listing_card_delta"] == expected
    assert row["state"] == "healthy"


# END_BLOCK_PRICE_DELTA
# START_BLOCK_LEDGER_GUARD


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
@pytest.mark.parametrize("source,group", [("ozon", "ozon"), ("yandex_market", "yandex"), ("detsky_mir", "detmir")])
def test_ledger_at_seven_skips_next_call_without_calling_any_tool(
    matrix_edges: MatrixEdges,
    source: str,
    group: str,
) -> None:
    edge = matrix_edges
    edge.seed_ledger([group] * 7)
    before = (edge.root / "ledger.json").read_bytes()
    assert edge.run("--only", source) == 0
    assert edge.calls == []
    assert (edge.root / "ledger.json").read_bytes() == before
    row = edge.rows()[0]
    assert row["state"] == "inconclusive"
    assert row["error_code"] == "budget_skipped"
    assert row["listing"]["error_code"] == "budget_skipped"
    assert row["listing"]["requests"] == 0


@pytest.mark.req("R-LIVE-PAUSE")
def test_a_marketplace_still_paused_is_skipped_without_a_call(
    matrix_edges: MatrixEdges, capsys: pytest.CaptureFixture[str]
) -> None:
    """The 2026-11-17 rerun went to Megamarket 17 minutes into its 1200 s pause; the matrix now skips it."""
    edge = matrix_edges
    pause = {"until": time.time() + 1200, "until_utc": "2026-11-17 16:51:36", "code": "rate_limited"}
    (edge.root / "ledger-pauses.json").write_text(json.dumps({"pauses": {"megamarket": pause}}), encoding="utf-8")
    assert edge.run("--only", "megamarket,lamoda") == 0
    assert [source for source, _kind, _kwargs in edge.calls] == ["lamoda", "lamoda"]
    skipped = next(row for row in edge.rows() if row["source"] == "megamarket")
    assert (skipped["state"], skipped["error_code"]) == ("inconclusive", "retry_after_skipped")
    assert skipped["listing"]["requests"] == 0 and skipped["card"] is None
    out = capsys.readouterr().out
    assert "megamarket: paused by the marketplace until 2026-11-17 16:51:36 UTC; listing probe skipped" in out


@pytest.mark.req("R-LIVE-PAUSE")
def test_a_skipped_probe_never_becomes_the_measured_cost(matrix_edges: MatrixEdges) -> None:
    measured = _history_row("megamarket", listing_requests=2)
    skipped = _history_row("megamarket", run_id="2025-01-02T00:00:00Z", listing_requests=0)
    skipped["listing"]["error_code"] = "retry_after_skipped"
    assert matrix_edges.matrix._expected_cost([measured, skipped], "megamarket", "listing") == 2


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
def test_whole_run_refused_by_ledger_exits_three_without_calls(matrix_edges: MatrixEdges) -> None:
    matrix_edges.seed_ledger(["other"] * 36)
    before = (matrix_edges.root / "ledger.json").read_bytes()
    assert matrix_edges.run() == 3
    assert matrix_edges.calls == []
    assert (matrix_edges.root / "ledger.json").read_bytes() == before


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
def test_guard_reloads_actual_ledger_before_card_and_costs_are_source_deltas(matrix_edges: MatrixEdges) -> None:
    edge = matrix_edges
    edge.seed_ledger(["ozon"] * 3)
    edge.costs[("ozon", "listing")] = 4
    edge.extra_groups[("ozon", "listing")] = ["yandex"]
    assert edge.run("--only", "ozon") == 0
    assert [(source, kind) for source, kind, _ in edge.calls] == [("ozon", "listing")]
    row = edge.rows()[0]
    assert row["listing"]["requests"] == 4  # Not 1 tool call, not 5 total ledger entries.
    assert row["card"]["state"] == "inconclusive"
    assert row["card"]["error_code"] == "budget_skipped"
    assert row["card"]["requests"] == 0
    assert row["state"] == "inconclusive"


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
@pytest.mark.parametrize("past_requests,initial,called", [(None, 5, False), (2, 5, True), (4, 4, False)])
def test_ozon_listing_expected_cost_is_newest_known_or_seed_three(
    matrix_edges: MatrixEdges,
    past_requests: int | None,
    initial: int,
    called: bool,
) -> None:
    edge = matrix_edges
    edge.seed_ledger(["ozon"] * initial)
    if past_requests is not None:
        edge.seed_history(
            [
                _history_row(listing_requests=past_requests),
                _history_row(run_id="2025-01-02T00:00:00Z", listing_requests=None),
            ]
        )
        edge.costs[("ozon", "listing")] = past_requests
    assert edge.run("--only", "ozon") == 0
    assert bool(edge.calls) is called
    row = edge.rows()[-1]
    assert row["listing"]["requests"] == (past_requests if called else 0)
    if not called:
        assert row["listing"]["error_code"] == "budget_skipped"


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
def test_measured_zero_request_listing_is_not_replaced_by_ozon_seed(matrix_edges: MatrixEdges) -> None:
    edge = matrix_edges
    edge.seed_ledger(["ozon"] * 7)
    edge.seed_history([_history_row(listing_requests=0)])
    edge.costs[("ozon", "listing")] = 0
    assert edge.run("--only", "ozon") == 0
    assert [(source, kind) for source, kind, _ in edge.calls] == [("ozon", "listing")]
    assert edge.rows()[-1]["listing"]["requests"] == 0
    assert edge.rows()[-1]["card"]["error_code"] == "budget_skipped"
    assert len(live_budget.Ledger(edge.root / "ledger.json").load()) == 7


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
def test_card_expected_cost_is_measured_by_kind_not_listing_cost(matrix_edges: MatrixEdges) -> None:
    edge = matrix_edges
    edge.seed_ledger(["yandex"] * 4)
    edge.seed_history([_history_row("yandex_market", listing_requests=1, card_requests=3)])
    assert edge.run("--only", "yandex_market") == 0
    assert [(source, kind) for source, kind, _ in edge.calls] == [("yandex_market", "listing")]
    assert edge.rows()[-1]["card"]["error_code"] == "budget_skipped"


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
def test_unmeasured_calls_cost_one_and_exact_36_boundary_is_allowed(matrix_edges: MatrixEdges) -> None:
    edge = matrix_edges
    edge.seed_ledger(["other"] * 35)
    assert edge.run("--only", "yandex_market") == 0
    assert [(source, kind) for source, kind, _ in edge.calls] == [("yandex_market", "listing")]
    row = edge.rows()[0]
    assert row["listing"]["requests"] == 1
    assert row["card"]["error_code"] == "budget_skipped"
    assert len(live_budget.Ledger(edge.root / "ledger.json").load()) == 36


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.guard")
def test_stop_rule_drops_cards_by_highest_total_source_cost_until_plan_fits(
    matrix_edges: MatrixEdges,
    capsys: pytest.CaptureFixture[str],
) -> None:
    edge = matrix_edges
    # 26 baseline +3 Ozon +4 Avito +3 Lamoda +2 Yandex +2 Cian = 40.
    # Total-source ranking: Ozon 7, Avito 6, Lamoda 5, Yandex/Cian 4.
    # Drop Ozon's card (2): 38 still exceeds 36; then Avito's card (5): 33.
    # Card-only ranking would incorrectly drop Avito first and retain Ozon.
    costs = {source: (3, 1) if source == "ozon" else (1, 1) for source in SOURCE_NAMES}
    costs.update(ozon=(5, 2), avito=(1, 5), lamoda=(1, 4), yandex_market=(2, 2), cian=(2, 2))
    assert sum(listing + card for listing, card in costs.values()) == 40
    edge.seed_history(
        [
            _history_row(source, listing_requests=listing, card_requests=card)
            for source, (listing, card) in costs.items()
        ]
    )
    edge.costs.update(
        {
            (source, kind): cost
            for source, pair in costs.items()
            for kind, cost in zip(("listing", "card"), pair, strict=True)
        }
    )
    assert edge.run() == 0
    current = edge.rows()[-12:]
    assert {row["source"] for row in current} == set(SOURCE_NAMES)
    assert {row["source"] for row in current if row["card"] is None} == {"ozon", "avito"}
    assert sum(row["listing"]["requests"] + (row["card"]["requests"] if row["card"] else 0) for row in current) == 33
    assert len(live_budget.Ledger(edge.root / "ledger.json").load()) == 33
    output = capsys.readouterr().out
    for dropped in ("ozon", "avito"):
        assert any(
            dropped in line and ("drop" in line.lower() or "skip" in line.lower()) and "card" in line.lower()
            for line in output.splitlines()
        )


# END_BLOCK_LEDGER_GUARD
# START_BLOCK_HISTORY_SELECTION_AND_PROBES


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
@pytest.mark.parametrize("different_release", [True, False], ids=["previous-release", "fallback-newest-run"])
def test_diff_uses_previous_release_before_newest_same_release(
    matrix_edges: MatrixEdges,
    capsys: pytest.CaptureFixture[str],
    different_release: bool,
) -> None:
    edge = matrix_edges
    edge.version = "1.0.0" if different_release else "2.0.0"
    edge.results[("ozon", "listing")] = _native_listing("ozon", (None, None))
    assert edge.run("--only", "ozon") == 0
    capsys.readouterr()
    if different_release:
        # A more recent run of the current release must not displace the previous release.
        edge.seed_history(
            [*edge.rows(), _history_row(version="2.0.0", run_id=dt.datetime.now(dt.UTC).isoformat(), items=9, priced=9)]
        )
    edge.version = "2.0.0"
    edge.seed_ledger([])  # Separate release runs start a fresh request budget.
    edge.results[("ozon", "listing")] = _native_listing("ozon", (1000,))
    assert edge.run("--only", "ozon") == 0
    output = capsys.readouterr().out
    assert "ozon" in output
    assert re.search(r"drift\s*(?:→|->)\s*healthy", output)
    assert re.search(r"items\s*[:=]?\s*2\s*(?:→|->)\s*1", output)
    assert re.search(r"priced\s*[:=]?\s*0\s*(?:→|->)\s*1", output)
    assert "9 → 1" not in output and "9 -> 1" not in output
    assert len(edge.rows()) == (3 if different_release else 2)


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_first_run_prints_no_baseline_and_one_progress_line_per_source(
    matrix_edges: MatrixEdges,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert matrix_edges.run("--only", "ozon,yandex_market") == 0
    output = capsys.readouterr().out
    assert "previous" in output.lower() and any(word in output.lower() for word in ("no ", "none", "first"))
    assert any("ozon" in line and "healthy" in line for line in output.splitlines())
    assert any("yandex_market" in line and "healthy" in line for line in output.splitlines())
    assert matrix_edges.events[:2] == ["preflight", "install"]
    assert matrix_edges.events.count("preflight") == 1


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
@pytest.mark.parametrize(
    "argv,selected",
    [
        (("--only", "ozon,yandex_market", "--skip", "ozon"), {"yandex_market"}),
        (("--skip", "ozon,cian"), set(SOURCE_NAMES) - {"ozon", "cian"}),
    ],
)
def test_only_and_skip_select_canonical_sources(
    matrix_edges: MatrixEdges, argv: tuple[str, ...], selected: set[str]
) -> None:
    assert matrix_edges.run(*argv) == 0
    assert {row["source"] for row in matrix_edges.rows()} == selected
    assert {source for source, _, _ in matrix_edges.calls} == selected
    assert "mpstats" not in {source for source, _, _ in matrix_edges.calls}


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
@pytest.mark.parametrize(
    "argv",
    [
        ("--only", "not-a-source"),
        ("--skip", "not-a-source"),
        ("--only", "mpstats"),
        ("--unexpected",),
        ("--only",),
    ],
)
def test_invalid_usage_exits_four_without_tool_calls(matrix_edges: MatrixEdges, argv: tuple[str, ...]) -> None:
    assert matrix_edges.run(*argv) == 4
    assert matrix_edges.calls == []
    assert matrix_edges.rows() == []


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_preflight_failure_exits_four_before_install_or_calls(matrix_edges: MatrixEdges) -> None:
    matrix_edges.preflight_ok = False
    assert matrix_edges.run("--only", "ozon") == 4
    assert matrix_edges.events == ["preflight"]
    assert matrix_edges.calls == []
    assert matrix_edges.rows() == []
    assert not (matrix_edges.root / "ledger.json").exists()


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_first_three_ids_are_saved_without_erasing_unselected_sources(matrix_edges: MatrixEdges) -> None:
    edge = matrix_edges
    saved = {"run_id": "2025-01-01T00:00:00Z", "yandex_market": ["old-a", "old-b", "old-c"], "ozon": ["old-ozon"]}
    (edge.root / ".live-ids.json").write_text(json.dumps(saved), encoding="utf-8")
    edge.results[("ozon", "listing")] = _native_listing("ozon", (1, 2, 3, 4), ("101", "102", "103", "104"))
    assert edge.run("--only", "ozon") == 0
    ids = json.loads((edge.root / ".live-ids.json").read_text(encoding="utf-8"))
    assert ids == {
        "run_id": edge.rows()[0]["run_id"],
        "ozon": ["101", "102", "103"],
        "yandex_market": saved["yandex_market"],
    }


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_all_fixed_probes_run_native_listing_and_first_card_offline(matrix_edges: MatrixEdges) -> None:
    edge = matrix_edges
    assert edge.run() == 0
    assert len(edge.rows()) == 12
    assert all(row["state"] == "healthy" for row in edge.rows())
    assert edge.events.count("preflight") == 1
    assert [(source, kind) for source, kind, _ in edge.calls] == [
        (source, kind) for source in SOURCE_NAMES for kind in ("listing", "card")
    ]
    assert len(live_budget.Ledger(edge.root / "ledger.json").load()) == 26
    for source, kind, kwargs in edge.calls:
        case = edge.cases[source]
        if kind == "listing":
            assert kwargs == case["listing"]["kwargs"]
        else:
            argument = case["card"]["id_argument"]
            assert argument in kwargs
            value = kwargs[argument]
            if source == "wildberries":
                assert isinstance(value, list) and len(value) == 1
                value = value[0]
            assert str(value) == "101"


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_taobao_first_item_url_preserves_tmall_early_refusal_and_audit_id(matrix_edges: MatrixEdges) -> None:
    edge = matrix_edges
    item_id = "12345678901"
    url = f"https://detail.tmall.com/item.htm?id={item_id}"
    listing = _native_listing("taobao", (100,), (item_id,))
    listing["items"][0]["url"] = url
    edge.results[("taobao", "listing")] = listing
    edge.results[("taobao", "card")] = {"error": "bad_request", "message": PRIVATE_PATH}
    edge.costs[("taobao", "card")] = 0  # Native Tmall URL refusal happens before navigation.
    assert edge.run("--only", "taobao") == 0
    assert edge.calls[1] == ("taobao", "card", {"item_id_or_url": url})
    row = edge.rows()[0]
    assert row["card"]["requests"] == 0
    assert row["state"] == "inconclusive" and row["error_code"] == "bad_request"
    assert row["listing_card_delta"] is None
    ids = json.loads((edge.root / ".live-ids.json").read_text(encoding="utf-8"))
    assert ids["taobao"] == [item_id]
    assert url not in json.dumps(edge.rows())


@pytest.mark.req("R-LIVE-MATRIX")
@pytest.mark.req("R-LIVE-MATRIX.probes")
def test_fixed_queries_match_registry_and_accept_real_tool_signatures() -> None:
    matrix = importlib.import_module("live_matrix")
    data = json.loads((REPO_ROOT / "scripts" / "audit_cases.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    cases = matrix.load_cases()
    assert isinstance(cases, dict) and cases == data["fixed_queries"]
    assert set(cases) == set(SOURCE_NAMES) and len(cases) == 12
    expected_queries = {
        "wildberries": "наушники sony wh-1000xm5",
        "ozon": "iphone 16 128gb",
        "yandex_market": "dyson v15 detect",
        "avito": "playstation 5 slim",
        "megamarket": "xiaomi redmi note 14 256gb",
        "lamoda": "кроссовки nike air force 1",
        "dns": "ssd samsung 990 pro 1tb",
        "citilink": "ssd samsung 990 pro 1tb",
        "aliexpress": "xiaomi smart band 9",
        "taobao": "小米手环9",
    }
    specs = {spec.name: spec for spec in SOURCES}
    for source, case in cases.items():
        spec = specs[source]
        assert case["module"] == spec.module
        module = importlib.import_module(case["module"])  # Import/introspection only; never call a tool.
        listing = getattr(module, case["listing"]["tool"])
        card = getattr(module, case["card"]["tool"])
        assert callable(listing) and callable(card)
        tools = {tool.name for tool in registered_tools(module.mcp)}
        assert case["listing"]["tool"] in tools
        assert case["card"]["tool"] in tools
        if spec.card_tool is not None:
            assert case["card"]["tool"] == spec.card_tool
        inspect.signature(listing).bind(**case["listing"]["kwargs"])
        argument = case["card"]["id_argument"]
        value: Any = [101] if source == "wildberries" else "101"
        inspect.signature(card).bind(**{argument: value})
        if source in expected_queries:
            assert expected_queries[source] in case["listing"]["kwargs"].values()
        elif source == "detsky_mir":
            assert case["listing"]["tool"] == "detmir_category"
            alias = case["listing"]["kwargs"]["alias"]
            assert alias == "podguzniki"
            assert module._ALIAS_RE.fullmatch(alias) is not None
            assert case["target_query"] == "pampers premium care 4"
        elif source == "cian":
            kwargs = case["listing"]["kwargs"]
            assert kwargs["deal"] == "rent"
            assert kwargs["offer_type"] == "flat"
            assert str(kwargs["region"]) == "1"
            assert kwargs["rooms"] == [1]


# END_BLOCK_HISTORY_SELECTION_AND_PROBES
