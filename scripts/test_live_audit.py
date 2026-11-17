"""Offline requirement checks for the P1-4 outside-in harness.

START_MODULE_CONTRACT
PURPOSE: Prove wrapped-child accounting, plans, audit checks, captures, timing, private output and the compare recorder.
SCOPE: Real child wrapper and MCP protocol with fake server targets; fake CDP only; no sockets to sites.
DEPENDS: live_audit, audit_server, live_budget, live_check, compare_recording; pytest; httpx; MCP; registry; stdlib.
LINKS: M-LIVE-AUDIT, M-COMPARE-RECORDING, R-LIVE-AUDIT, R-COMPARE-BENCHMARK-REPLAY.
START_MODULE_MAP
  audit: Import the new implementation inside each test, retaining individual pre-implementation failures.
  recorder: The recorder module, imported inside each test the same way.
  test_*: Requirement-derived acceptance checks with isolated temporary files and I/O edges.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import base64
import importlib
import json
import subprocess
import sys
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).parent))
import live_budget
import live_check
from mcp_core.output_schema import registered_tools
from mcp_core.resources import render_error_reference
from mcp_core.sources import SOURCES

pytestmark = pytest.mark.req("R-LIVE-AUDIT")
ROOT = Path(__file__).resolve().parents[1]

# START_BLOCK_PLANS


@pytest.fixture
def audit() -> Any:
    return importlib.import_module("live_audit")


def inputs(audit: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    cases = audit.load_cases()
    ids = {source: ["1234567890", "1234567891", "1234567892"] for source in cases["fixed_queries"]}
    return cases, ids


@pytest.fixture
def measured_costs(audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], int]:
    """Replay the reviewer's measured request counts through the real cost-table reader."""
    cases, _ = inputs(audit)
    overrides = {
        "wildberries": {"listing": 3, "card": 3},
        "ozon": {"listing": 3, "card": 3},
        "avito": {"listing": 3},
        "lamoda": {"card": 3},
        "aliexpress": {"card": 2},
    }
    history = [
        {
            "source": source,
            **{kind: {"requests": overrides.get(source, {}).get(kind, 1)} for kind in ("listing", "card")},
        }
        for source in cases["fixed_queries"]
    ]
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    (tmp_path / ".live-history.jsonl").write_text("".join(json.dumps(row) + "\n" for row in history), encoding="utf-8")
    return audit._cost_table(cases, [])


@pytest.mark.req("R-LIVE-AUDIT.plans")
@pytest.mark.parametrize(
    ("phase", "run"), [(p, n) for p, count in (("A", 2), ("B", 3), ("C", 8)) for n in range(1, count + 1)]
)
def test_each_run_fits_measured_costs(
    audit: Any, measured_costs: dict[tuple[str, str], int], phase: str, run: int
) -> None:
    cases, ids = inputs(audit)
    plan = audit.plan_run(cases, ids, measured_costs, phase, run)
    assert plan
    assert sum(sum(call["costs"].values()) for call in plan) <= 36
    known: Counter[str] = Counter()
    for call in plan:
        known.update({source: cost for source, cost in call["costs"].items() if source != "$winner"})
    assert all(cost <= 7 for cost in known.values())
    if phase == "C":
        assert [call["kind"] for call in plan] == ["compare", "verify", "verify", "capture"]
        assert plan[0]["args"]["query"] == cases["compare_queries"]["1"][run - 1]


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_wb_uses_longest_cost_guarded_prefix(audit: Any, measured_costs: dict[tuple[str, str], int]) -> None:
    cases, ids = inputs(audit)
    plans = [audit.plan_run(cases, ids, measured_costs, "A", n) for n in (1, 2)]
    wb = [[call for call in plan if call["source"] == "wildberries"] for plan in plans]
    spent = [sum(call["costs"]["wildberries"] for call in chunk) for chunk in wb]
    assert all(cost <= 7 for cost in spent)
    assert spent[0] + wb[1][0]["costs"]["wildberries"] > 7
    assert [call["tool"] for chunk in wb for call in chunk] == [
        tool["tool"] for tool in cases["phase_a_tools"]["wildberries"]
    ]
    for source in set(cases["fixed_queries"]) - {"wildberries"}:
        assert sum(any(call["source"] == source for call in plan) for plan in plans) == 1
    measured_costs[("wildberries", "other")] = 2
    with pytest.raises(ValueError, match="phase A inventory cannot fit two guarded runs"):
        audit.plan_run(cases, ids, measured_costs, "A", 1)


@pytest.mark.req("R-LIVE-AUDIT.plans")
@pytest.mark.parametrize("costly", [False, True])
@pytest.mark.parametrize(("phase", "runs"), [("A", 2), ("B", 3), ("C", 8)])
def test_all_run_plans_fit_and_never_repeat(audit: Any, costly: bool, phase: str, runs: int) -> None:
    cases, ids = inputs(audit)
    costs = {("ozon", "listing"): 3}
    if costly:
        costs.update({(source, "card"): 2 for source in cases["fixed_queries"]})
    seen: set[str] = set()
    for n in range(1, runs + 1):
        plan = audit.plan_run(cases, ids, costs, phase, n)
        assert plan
        assert sum(sum(call["costs"].values()) for call in plan) <= 36
        per_source: Counter[str] = Counter()
        for call in plan:
            assert call["key"] not in seen
            seen.add(call["key"])
            per_source.update({source: cost for source, cost in call["costs"].items() if source != "$winner"})
        assert all(cost <= 7 for cost in per_source.values())
        if phase == "B":
            assert len({call["source"] for call in plan}) == 4
            assert len(plan) == (16 if costly else 24)
        if phase == "C":
            assert sum(call["tool"] == "compare_prices" for call in plan) == 1
            assert sum(call["tool"] == "compare_verify_offer" for call in plan) == 2


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_phase_a_registry_coverage_and_wb_split(audit: Any, measured_costs: dict[tuple[str, str], int]) -> None:
    cases, ids = inputs(audit)
    expected = {
        tool.name
        for spec in SOURCES
        if spec.name in cases["fixed_queries"]
        for tool in registered_tools(importlib.import_module(spec.module).mcp)
    }
    plans = [audit.plan_run(cases, ids, measured_costs, "A", n) for n in (1, 2)]
    names = [call["tool"] for plan in plans for call in plan]
    assert len(expected) == len(names) == 33
    assert set(names) == expected
    assert [sum(call["source"] == "wildberries" for call in plan) for plan in plans] == [3, 5]
    assert all(
        call["args"] == cases["fixed_queries"][call["source"]]["listing"]["kwargs"]
        for plan in plans
        for call in plan
        if call["kind"] == "listing"
    )


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_card_args_use_native_types_and_paths(audit: Any) -> None:
    cases, ids = inputs(audit)
    calls = [c for n in (1, 2, 3) for c in audit.plan_run(cases, ids, {}, "B", n) if c["kind"] == "card"]
    for call in calls:
        module = importlib.import_module(cases["fixed_queries"][call["source"]]["module"])
        tool = next(t for t in registered_tools(module.mcp) if t.name == call["tool"])
        from jsonschema import validate

        validate(call["args"], tool.parameters)
    assert next(c for c in calls if c["source"] == "detsky_mir")["args"] == {"product_id": 1234567890}
    assert next(c for c in calls if c["source"] == "dns")["args"]["product_url"].startswith(
        "https://www.dns-shop.ru/product/"
    )


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_invalid_runs_and_later_phase_a_refused(audit: Any) -> None:
    cases, ids = inputs(audit)
    for phase, run, cycle in [("A", 3, 1), ("B", 0, 1), ("C", 9, 1), ("A", 1, 2), ("C", 1, 999)]:
        with pytest.raises(ValueError):
            audit.plan_run(cases, ids, {}, phase, run, cycle=cycle)


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_compare_queries_precede_winner_spending(audit: Any) -> None:
    cases, ids = inputs(audit)
    plan = audit.plan_run(cases, ids, {("ozon", "listing"): 3}, "C", 1)
    assert [call["tool"] for call in plan] == [
        "compare_prices",
        "compare_verify_offer",
        "compare_verify_offer",
        "capture",
    ]


@pytest.mark.req("R-LIVE-AUDIT.child")
def test_full_profile_environment_uses_registry_selection(audit: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_core.source_selection import selected

    monkeypatch.setenv("MARKETPLACE_SOURCES", "ozon")
    env = audit.child_environment(Path("ledger.json"), Path("marker.json"), "nonce")
    monkeypatch.setenv("MARKETPLACE_SOURCES", env["MARKETPLACE_SOURCES"])
    assert selected() is None


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_compare_uses_text_search_sources_only(audit: Any) -> None:
    from compare_connector.server import SEARCHABLE
    from mcp_core.sources import BY_NAME, COMPARE_SOURCES

    cases, ids = inputs(audit)
    expected = {s for s in COMPARE_SOURCES if BY_NAME[s].text_search}
    for n in range(1, 9):
        for call in audit.plan_run(cases, ids, {}, "C", n):
            if call["kind"] == "compare":
                assert set(call["args"]["sources"]) == expected
                assert set(call["args"]["sources"]) <= set(SEARCHABLE)


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_duplicate_saved_ids_do_not_repeat_card_calls(audit: Any) -> None:
    cases, ids = inputs(audit)
    ids["ozon"] = [123, "123", 124]
    plan = [c for c in audit.plan_run(cases, ids, {}, "B", 1) if c["source"] == "ozon" and c["kind"] == "card"]
    assert len(plan) == 2
    assert len({json.dumps(c["args"], sort_keys=True) for c in plan}) == 2


# END_BLOCK_PLANS
# START_BLOCK_CHILD


@pytest.mark.req("R-LIVE-AUDIT.child")
def test_real_wrapped_child_request_reaches_shared_ledger(audit: Any, tmp_path: Path) -> None:
    target = tmp_path / "fake_server.py"
    target.write_text(
        "import httpx\n"
        "with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:\n"
        "    assert client.get('https://www.ozon.ru/product/1234567890/').status_code == 200\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.json"
    marker = tmp_path / "hook.json"
    env = audit.child_environment(ledger, marker, "proof")
    with (tmp_path / "child.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/audit_server.py"), "--target", str(target)],
            env=env,
            stdout=log,
            stderr=log,
            timeout=60,
            check=False,
        )
    assert result.returncode == 0, (tmp_path / "child.log").read_text(encoding="utf-8")
    audit.require_hook(marker, ledger, "proof")
    entries = live_budget.Ledger(ledger).load()
    assert len(entries) == 1
    assert entries[0]["group"] == "ozon"
    assert entries[0]["kind"] == "httpx"
    assert entries[0]["url"] == "https://www.ozon.ru/product/1234567890/"
    assert env["MCP_TRANSPORT"] == "stdio"
    # The registry selector uses blank/unset, not the invalid literal "all".
    assert env["MARKETPLACE_SOURCES"] == ""
    assert env["CHROME_CHALLENGE_HANDOFF_S"] == "0"


@pytest.mark.req("R-LIVE-AUDIT.child")
@pytest.mark.parametrize(
    "report", [None, {"installed": False}, {"installed": True, "nonce": "stale", "ledger": "wrong"}]
)
def test_missing_or_false_hook_refuses_before_calls(audit: Any, tmp_path: Path, report: Any) -> None:
    marker = tmp_path / "hook.json"
    if report is not None:
        marker.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="hook"):
        audit.require_hook(marker, tmp_path / "ledger.json", "fresh")


@pytest.mark.req("R-LIVE-AUDIT.child")
async def test_real_stdio_client_with_wrapped_fake_server(audit: Any, tmp_path: Path) -> None:
    target = tmp_path / "fake_mcp.py"
    target.write_text(
        "from fastmcp import FastMCP\n"
        "mcp=FastMCP('offline')\n"
        "@mcp.tool\n"
        "def audit_echo(value: int) -> dict:\n"
        "    return {'value': value}\n"
        "mcp.run(transport='stdio', show_banner=False)\n",
        encoding="utf-8",
    )
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))
    async with audit.open_session(budget, tmp_path, target=target) as session:
        result = await session.call_tool("audit_echo", {"value": 17})
        assert result.structured_content == {"value": 17}
    assert budget.ledger.load() == []


# END_BLOCK_CHILD
# START_BLOCK_CAPTURE_AND_CHECKS


class FakePage:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.commands: list[tuple[str, Any]] = []

    async def _send(self, method: str, params: Any = None, **kwargs: Any) -> dict[str, Any]:
        self.commands.append((method, params))
        if method == "Page.captureScreenshot":
            if self.fail:
                raise RuntimeError("capture failed")
            return {"data": base64.b64encode(b"fake PNG bytes").decode()}
        return {}

    async def evaluate(self, expression: str) -> Any:
        if "ld+json" in expression:
            return ['{"@context":"https://schema.org","@type":"Product","offers":{"@type":"Offer","price":"17.50"}}']
        if "visibilityState" in expression:
            return "hidden"
        return "https://www.ozon.ru/product/1234567890/"


@pytest.mark.req("R-LIVE-AUDIT.capture")
@pytest.mark.parametrize("fail", [False, True])
async def test_capture_dimensions_scale_and_finally_close(audit: Any, tmp_path: Path, fail: bool) -> None:
    page = FakePage(fail)
    events: list[str] = []

    @asynccontextmanager
    async def opener(url: str, **kwargs: Any) -> Any:
        events.append("open")
        try:
            yield page
        finally:
            events.append("close")

    if fail:
        with pytest.raises(RuntimeError, match="capture failed"):
            await audit.capture_card(
                "https://www.ozon.ru/product/1234567890/", ("ozon.ru",), tmp_path, "card", opener=opener
            )
    else:
        result = await audit.capture_card(
            "https://www.ozon.ru/product/1234567890/", ("ozon.ru",), tmp_path, "card", opener=opener
        )
        metrics = next(params for method, params in page.commands if method == "Emulation.setDeviceMetricsOverride")
        assert (metrics["width"], metrics["height"]) == (1280, 1600)
        shots = [params for method, params in page.commands if method == "Page.captureScreenshot"]
        assert [p["clip"]["scale"] for p in shots] == [1, 0.75]
        assert all((p["clip"]["width"], p["clip"]["height"]) == (1280, 1600) for p in shots)
        assert len(result["capture_files"]) == 2
        assert all((tmp_path / name).is_file() for name in result["capture_files"])
        assert result["json_ld_prices"] == [{"type": "Offer", "price": 17.5, "currency": None}]
        assert result["screenshot_price"] is None
    assert events == ["open", "close"]


@pytest.mark.req("R-LIVE-AUDIT.capture")
def test_json_ld_product_offer_not_aggregate_low_price(audit: Any) -> None:
    blocks = [
        json.dumps(
            {
                "@graph": [
                    {
                        "@context": "https://schema.org",
                        "@type": "Product",
                        "price": "20",
                        "offers": {"@type": "Offer", "price": "21", "priceCurrency": "RUB"},
                    },
                    {"@context": "https://schema.org", "@type": "AggregateOffer", "lowPrice": "1"},
                ]
            }
        ),
        "not JSON",
    ]
    result = audit.json_ld_evidence(blocks)
    assert result["product_or_offer_exists"] is True
    assert [p["price"] for p in result["json_ld_prices"]] == [20, 21]
    assert audit.json_ld_evidence(['{"@type":"AggregateOffer","lowPrice":1}'])["product_or_offer_exists"] is False


@pytest.mark.req("R-LIVE-AUDIT.checks")
def test_phase_a_schema_catalogue_guide_pii_and_hosts(audit: Any) -> None:
    cases, _ = inputs(audit)
    tool = SimpleNamespace(
        name="ozon_card",
        output_schema={"type": "object", "required": ["price"], "properties": {"price": {"type": "number"}}},
    )
    payload = {"price": 17, "nested": [{"author": "PRIVATE"}], "url": "https://www.ozon.ru/product/123/"}
    checks = audit.check_answer(
        payload, payload, tool, {"guide://ozon/ozon_card"}, render_error_reference(), cases, "ozon"
    )
    assert checks["schema"] is True
    assert checks["guide"] is True
    assert checks["personal_data"] is False
    assert checks["hosts"] is True
    assert checks["final_hosts"] == ["www.ozon.ru"]
    broken = {"price": "wrong", "email": None, "url": "https://ozon.ru.evil.invalid/product/1/", "error": "new_code"}
    checks = audit.check_answer(broken, broken, tool, set(), render_error_reference(), cases, "ozon")
    assert checks["schema"] is False
    assert checks["guide"] is False
    assert checks["personal_data"] is True
    assert checks["hosts"] is False
    assert checks["error_catalogue"] is False
    # An image on the source's CDN host is not a page link; a plain-http image still fails.
    image = {
        "price": 17,
        "url": "https://aliexpress.ru/item/1.html",
        "image_url": "https://ae-pic-a1.aliexpress-media.com/kf/a.jpg",
    }
    assert audit.check_answer(image, image, tool, set(), render_error_reference(), cases, "aliexpress")["hosts"] is True
    image["image_url"] = "http://ae-pic-a1.aliexpress-media.com/kf/a.jpg"
    assert (
        audit.check_answer(image, image, tool, set(), render_error_reference(), cases, "aliexpress")["hosts"] is False
    )
    # Follow-up requirement: errors use their own envelope, never the success schema.
    assert (
        audit.check_answer({"error": "timeout"}, None, tool, set(), render_error_reference(), cases, "ozon")["schema"]
        is False
    )
    from mcp_core.errors import UpstreamTimeoutError

    envelope = UpstreamTimeoutError().to_dict()
    assert audit.check_answer(envelope, None, tool, set(), render_error_reference(), cases, "ozon")["schema"] is True


@pytest.mark.req("R-LIVE-AUDIT.checks")
@pytest.mark.parametrize("retryable", ["true", None, 1])
def test_error_envelope_rejects_wrong_types(audit: Any, retryable: Any) -> None:
    cases, _ = inputs(audit)
    tool = SimpleNamespace(name="ozon_card", output_schema={"type": "object", "required": ["price"]})
    value = {"error": "timeout", "message": "timeout", "retryable": retryable}
    assert audit.check_answer(value, None, tool, set(), render_error_reference(), cases, "ozon")["schema"] is False


@pytest.mark.req("R-LIVE-AUDIT.timing")
@pytest.mark.parametrize(
    ("times", "fired"), [([59] * 8, False), ([61] * 8, True), ([59] * 7 + [121], True), ([60] * 7 + [120], False)]
)
def test_compare_trigger_thresholds(audit: Any, times: list[int], fired: bool) -> None:
    records = [{"tool": "compare_prices", "latency_s": n, "key": f"C:{i}"} for i, n in enumerate(times)]
    records.append({"tool": "compare_verify_offer", "latency_s": 999})
    result = audit.compare_trigger(records)
    assert result["calls"] == 8
    assert result["complete"] is True
    assert result["fired"] is fired
    assert audit.compare_trigger(records[:2])["complete"] is False


@pytest.mark.req("R-LIVE-AUDIT.timing")
def test_trigger_waits_for_eight_compare_calls(audit: Any) -> None:
    result = audit.compare_trigger([{"tool": "compare_prices", "latency_s": 121}])
    assert result["complete"] is False
    assert result["fired"] is False


# END_BLOCK_CAPTURE_AND_CHECKS
# START_BLOCK_RUNNER


class FakeSession:
    def __init__(self, budget: live_budget.Budget) -> None:
        self.budget = budget
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name="ozon_search", output_schema={"type": "object"}),
                SimpleNamespace(name="ozon_card", output_schema={"type": "object"}),
            ],
            next_cursor=None,
        )

    async def list_resources(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            resources=[SimpleNamespace(uri="guide://ozon/ozon_search"), SimpleNamespace(uri="guide://ozon/ozon_card")],
            next_cursor=None,
        )

    async def read_resource(self, uri: str) -> Any:
        return SimpleNamespace(contents=[SimpleNamespace(text=render_error_reference())])

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        self.budget.reserve("httpx", "https://www.ozon.ru/")
        print("PRIVATE_TITLE https://www.ozon.ru/?email=PRIVATE_QUERY C:\\Users\\PRIVATE_USER")
        result = {"items": [{"sku": 1234567890, "price": 17, "url": "https://www.ozon.ru/product/1234567890/"}]}
        return SimpleNamespace(structured_content=result, content=[], is_error=False)


class PausedSourceSession(FakeSession):
    """MCP edge only: book local ledger entries and return explicit source challenges."""

    def __init__(self, budget: live_budget.Budget, challenged: list[str] | None = None) -> None:
        super().__init__(budget)
        self.challenged = challenged or []

    async def list_tools(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name=name, output_schema={"type": "object"})
                for name in ("compare_prices", "compare_verify_offer", "lamoda_search", "lamoda_card", "ozon_card")
            ],
            next_cursor=None,
        )

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        sources = arguments["sources"] if tool == "compare_prices" else ["lamoda"]
        outcomes = []
        for source in sources:
            self.budget.reserve("fake", f"https://www.{source}.ru/")
            outcomes.append(
                {"source": source, "error_code": "challenge_required", "retry_after_s": 1}
                if source in self.challenged
                else {"source": source, "status": "ok", "elapsed_ms": 1}
            )
        result = {"source_outcomes": outcomes, "cheapest": None, "cheapest_comparable": None}
        if self.challenged:
            result["cheapest"] = {"source": self.challenged[0], "product_id": "123", "price_rub": 17}
        return SimpleNamespace(structured_content=result, content=[], is_error=False)


@pytest.mark.req("R-LIVE-AUDIT.runner")
@pytest.mark.parametrize("all_paused", [False, True])
@pytest.mark.parametrize("origin", ["previous", "current"])
async def test_compare_drops_paused_sources_before_budget(
    audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, all_paused: bool, origin: str
) -> None:
    cases, ids = inputs(audit)
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)

    async def hidden() -> bool:
        return True

    monkeypatch.setattr(audit, "_window_hidden", hidden)
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))
    names = ["ozon", "lamoda"]
    paused = names if all_paused else ["lamoda"]
    session = PausedSourceSession(budget, challenged=paused if origin == "current" else None)
    first = audit.plan_run(cases, ids, {}, "C", 1)[0]
    first = {**first, "args": {**first["args"], "sources": names}, "costs": dict.fromkeys(names, 1)}
    target = {**first, "key": "C:later"}
    previous = (
        [
            {
                "tool": "lamoda_search",
                "source": name,
                "finished_epoch": 0,
                "result": {"error_code": "challenge_required", "retry_after_s": 1},
            }
            for name in paused
        ]
        if origin == "previous"
        else []
    )
    if origin == "previous":
        for _ in range(7):
            budget.reserve("setup", "https://www.lamoda.ru/")
    plan = [first, target] if origin == "current" else [target]
    if origin == "current":
        plan.append(audit.plan_run(cases, ids, {}, "C", 1)[1])
    rows = await audit.execute_plan(plan, session, budget, cases, tmp_path, "C", previous=previous)
    row = next(row for row in rows if row["key"] == target["key"])
    assert row["dropped_sources"] == sorted(paused)
    assert row["args"]["sources"] == [name for name in names if name not in paused]
    assert first["args"]["sources"] == names
    assert first["costs"] == dict.fromkeys(names, 1)
    if all_paused:
        assert row["state"] == "skipped"
        assert row["error_code"] == "source_paused"
        assert len(session.calls) == (1 if origin == "current" else 0)
    else:
        assert row["state"] == "healthy"
        assert row["requests"] == 1
        assert session.calls[-1] == ("compare_prices", {**target["args"], "sources": ["ozon"]})
    if origin == "current":
        assert rows[-1]["source"] == paused[0]
        assert rows[-1]["error_code"] == "source_paused"
        assert all(tool != "compare_verify_offer" for tool, _ in session.calls)
    persisted = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))["calls"]
    assert next(row for row in persisted if row["key"] == target["key"])["dropped_sources"] == sorted(paused)


@pytest.mark.req("R-LIVE-AUDIT.runner")
async def test_skip_lamoda_reaches_runner_and_compare(
    audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, ids = inputs(audit)
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    (tmp_path / ".live-ids.json").write_text(json.dumps(ids), encoding="utf-8")
    sessions = []

    async def hidden() -> bool:
        return True

    @asynccontextmanager
    async def opener(budget: live_budget.Budget, directory: Path, **kwargs: Any) -> Any:
        session = PausedSourceSession(budget)
        sessions.append(session)
        yield session

    monkeypatch.setattr(audit, "_window_hidden", hidden)
    monkeypatch.setattr(audit, "open_session", opener)
    for phase, run in [("A", n) for n in (1, 2)] + [("C", 1)]:
        budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / f"ledger-{phase}{run}.json"))
        assert await audit.run(["--phase", phase, "--run", str(run), "--skip", "lamoda"], budget) == 0
    records = audit._cycle_records(1)
    lamoda = [row for row in records if row["source"] == "lamoda"]
    assert len(lamoda) == len(cases["phase_a_tools"]["lamoda"])
    assert all(row["state"] == "skipped" and row["error_code"] == "source_paused" for row in lamoda)
    compare = next(row for row in records if row["tool"] == "compare_prices")
    assert compare["state"] == "healthy"
    assert compare["dropped_sources"] == ["lamoda"]
    assert "lamoda" not in compare["args"]["sources"]
    calls = [call for session in sessions for call in session.calls]
    assert all(not tool.startswith("lamoda_") for tool, _ in calls)
    assert all("lamoda" not in args["sources"] for tool, args in calls if tool == "compare_prices")


async def _run_phase_a(audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runs: list[int]) -> list[str]:
    """Run phase A through ``run`` with a fake MCP edge that books one request per call."""
    _, ids = inputs(audit)
    (tmp_path / ".live-ids.json").write_text(json.dumps(ids), encoding="utf-8")

    async def hidden() -> bool:
        return True

    @asynccontextmanager
    async def opener(budget: live_budget.Budget, directory: Path, **kwargs: Any) -> Any:
        yield PausedSourceSession(budget)

    monkeypatch.setattr(audit, "_window_hidden", hidden)
    monkeypatch.setattr(audit, "open_session", opener)
    for run in runs:
        budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / f"ledger-A{run}.json"))
        assert await audit.run(["--phase", "A", "--run", str(run)], budget) == 0
    return [row["key"] for row in audit._cycle_records(1)]


@pytest.mark.req("R-LIVE-AUDIT.plans", "R-LIVE-AUDIT.runner")
async def test_phase_a_runs_keep_their_split_when_run_one_measures_other_costs(
    audit: Any, measured_costs: dict[tuple[str, str], int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History says WB search and card cost 3; the fake books 1. Run 2 must still take exactly the rest."""
    cases, _ = inputs(audit)
    keys = await _run_phase_a(audit, tmp_path, monkeypatch, [1, 2])
    inventory = ["A:" + spec["tool"] for tools in cases["phase_a_tools"].values() for spec in tools]
    assert sorted(keys) == sorted(inventory)


@pytest.mark.req("R-LIVE-AUDIT.runner")
async def test_phase_a_refuses_a_split_that_moved_between_runs(
    audit: Any, measured_costs: dict[tuple[str, str], int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matrix run between the two phase A runs changes the history; run 2 refuses instead of guessing."""
    await _run_phase_a(audit, tmp_path, monkeypatch, [1])
    history = tmp_path / ".live-history.jsonl"
    rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    rows.append({"source": "wildberries", "listing": {"requests": 1}, "card": {"requests": 1}})
    history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="phase A split changed"):
        await _run_phase_a(audit, tmp_path, monkeypatch, [2])


@pytest.mark.req("R-LIVE-AUDIT.runner")
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--phase", "C", "--run", "8", "--skip", "lamoda,cian"], {"lamoda", "cian"}),
        (["--phase", "A", "--run", "1", "--skip", " lamoda , cian "], {"lamoda", "cian"}),
        (["--phase", "A", "--run", "1", "--skip", "wb,yandex"], {"wildberries", "yandex_market"}),
        (["--phase", "A", "--run", "1", "--skip", "unknown"], None),
        (["--phase", "A", "--run", "1", "--skip", "lamoda,"], None),
        (["--phase", "A", "--run", "1", "--skip", ""], None),
        (["--summary", "--skip", "x"], None),
        (["--summary", "--skip", "lamoda"], None),
        (["--summary", "--skip", ""], None),
    ],
)
def test_skip_options_are_validated(audit: Any, argv: list[str], expected: set[str] | None) -> None:
    if expected is None:
        with pytest.raises(ValueError, match=r"^invalid audit options$"):
            audit.select_options(argv)
    else:
        assert audit.select_options(argv).skip == expected


@pytest.mark.req("R-LIVE-AUDIT.runner")
async def test_runner_ledger_delta_refresh_ids_and_whitelist(
    audit: Any, tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, ids = inputs(audit)
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))
    session = FakeSession(budget)
    call = next(c for n in (1, 2) for c in audit.plan_run(cases, ids, {}, "A", n) if c["tool"] == "ozon_search")
    rows = await audit.execute_plan([call], session, budget, cases, tmp_path, "A")
    assert len(rows) == 1
    assert rows[0]["requests"] == 1
    assert rows[0]["latency_s"] >= 0
    assert rows[0]["state"] == "healthy"
    assert json.loads((tmp_path / ".live-ids.json").read_text())["ozon"] == [1234567890]
    output = capsys.readouterr().out
    assert "ozon_search" in output
    assert all(
        secret not in output for secret in ("PRIVATE_TITLE", "PRIVATE_QUERY", "PRIVATE_USER", "https://", "C:\\")
    )
    assert (tmp_path / "audit.json").is_file()
    before = len(session.calls)
    for _ in range(6):
        budget.reserve("fake", "https://www.ozon.ru/")
    rows = await audit.execute_plan([call], session, budget, cases, tmp_path / "blocked", "A")
    assert len(session.calls) == before
    assert rows[0]["error_code"] == "budget_skipped"
    assert rows[0]["requests"] == 0


class RefusedMidCallSession(FakeSession):
    """MCP edge only: the child books what is left of Ozon's budget, prints the budget's
    refusal line to its stderr log (child.log, as open_session wires it) and answers with
    the transport error a connector makes of a refused request."""

    def __init__(self, budget: live_budget.Budget, log: Path) -> None:
        super().__init__(budget)
        self.log = log

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        while len([e for e in self.budget.ledger.load() if e.get("group") == "ozon"]) < self.budget.per_source:
            self.budget.reserve("httpx", "https://www.ozon.ru/")
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{live_budget.REFUSED_PREFIX}ozon in-page fetch https://www.ozon.ru/api/: "
                "live budget exhausted for ozon: 8/8 requests\n"
            )
        error = {"error": "transport_down", "message": "Tier-2 (CDP) unreachable, start Chrome", "retryable": True}
        return SimpleNamespace(
            structured_content=None, content=[SimpleNamespace(text=json.dumps(error))], is_error=True
        )


@pytest.mark.req("R-LIVE-AUDIT.runner")
async def test_a_budget_refusal_inside_the_child_is_a_budget_skip_not_a_dead_source(
    audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call our own budget cut short is skipped: budget, pauses nothing, and costs one request more next time."""
    cases, ids = inputs(audit)
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))
    call = next(c for n in (1, 2) for c in audit.plan_run(cases, ids, {}, "A", n) if c["tool"] == "ozon_search")
    for _ in range(7 - call["costs"]["ozon"]):
        budget.reserve("fake", "https://www.ozon.ru/")
    booked = len(budget.ledger.load())
    run = tmp_path / "run"
    run.mkdir()
    (run / "child.log").write_text("[11/16/26 19:02:40] earlier child output\n", encoding="utf-8")
    session = RefusedMidCallSession(budget, run / "child.log")

    rows = await audit.execute_plan([call], session, budget, cases, run, "A")

    assert len(session.calls) == 1
    assert rows[0]["error_code"] == "budget_skipped"
    assert rows[0]["state"] == "skipped"
    assert rows[0]["tool_error_code"] == "transport_down"
    assert rows[0]["requests"] == 8 - booked
    measured: dict[tuple[str, str], int] = {}
    audit._observe_costs(call, rows[0], cases, measured)
    assert measured[("ozon", "ozon_search")] == 8 - booked + 1

    # The next run of the cycle does not treat Ozon as down.
    budget.ledger.save([])
    again = await audit.execute_plan(
        [{**call, "key": "A:again"}], FakeSession(budget), budget, cases, tmp_path / "next", "A", previous=rows
    )
    assert again[0]["state"] == "healthy"


@pytest.mark.req("R-LIVE-AUDIT.runner")
async def test_a_tool_error_without_a_refusal_line_keeps_its_own_code(audit: Any, tmp_path: Path) -> None:
    """Only the budget's own line relabels an error: a refusal printed before the call does not count."""
    cases, ids = inputs(audit)
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))
    call = next(c for n in (1, 2) for c in audit.plan_run(cases, ids, {}, "A", n) if c["tool"] == "ozon_search")
    run = tmp_path / "run"
    run.mkdir()
    log = run / "child.log"
    log.write_text(f"{live_budget.REFUSED_PREFIX}ozon earlier refusal from another call\n", encoding="utf-8")

    class DownSession(FakeSession):
        async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
            self.calls.append((tool, arguments))
            error = {"error": "transport_down", "message": "Tier-2 (CDP) unreachable, start Chrome", "retryable": True}
            return SimpleNamespace(
                structured_content=None, content=[SimpleNamespace(text=json.dumps(error))], is_error=True
            )

    rows = await audit.execute_plan([call], DownSession(budget), budget, cases, run, "A")

    assert rows[0]["error_code"] == "transport_down"
    assert "tool_error_code" not in rows[0]


@pytest.mark.req("R-LIVE-AUDIT.runner")
def test_summary_dispatch_makes_no_preflight_or_install(
    audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(live_check, "_preflight", lambda: pytest.fail("summary must not attach a browser"))
    monkeypatch.setattr(live_budget, "install", lambda: pytest.fail("summary must not install transports"))
    directory = tmp_path / ".live-audit/1/C1"
    directory.mkdir(parents=True)
    (directory / "audit.json").write_text(
        json.dumps(
            {
                "phase": "C",
                "cycle": 1,
                "calls": [
                    {"tool": "compare_prices", "key": "C:0", "latency_s": 61, "error_code": None, "title": "PRIVATE"}
                ],
            }
        )
    )
    assert live_check.main(["audit", "--summary", "--cycle", "1"]) == 0
    output = capsys.readouterr().out
    assert "calls 1" in output
    assert "PRIVATE" not in output


@pytest.mark.req("R-LIVE-AUDIT.runner")
async def test_new_measured_card_cost_is_used_before_next_call(
    audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, ids = inputs(audit)
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))
    for _ in range(2):
        budget.reserve("setup", "https://www.ozon.ru/")

    class TwoRequestCard(FakeSession):
        async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
            budget.reserve("extra", "https://www.ozon.ru/")
            return await super().call_tool(tool, arguments)

    session = TwoRequestCard(budget)
    plan = [
        c
        for n in (1, 2, 3)
        for c in audit.plan_run(cases, ids, {}, "B", n)
        if c["source"] == "ozon" and c["kind"] == "card"
    ]
    rows = await audit.execute_plan(plan, session, budget, cases, tmp_path, "B")
    assert len(session.calls) == 2
    assert len(budget.ledger.load()) == 6
    assert rows[-1]["error_code"] == "budget_skipped"


@pytest.mark.req("R-LIVE-AUDIT.timing")
async def test_compare_records_wall_latency_sources_and_visibility(
    audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, ids = inputs(audit)
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    clock = {"seconds": 0.0}
    monkeypatch.setattr(audit.time, "monotonic", lambda: clock["seconds"])

    async def hidden() -> bool:
        clock["seconds"] += 5
        return True

    monkeypatch.setattr(audit, "_window_hidden", hidden)
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))

    class CompareSession(FakeSession):
        async def list_tools(self, **kwargs: Any) -> Any:
            return SimpleNamespace(
                tools=[SimpleNamespace(name="compare_prices", output_schema={"type": "object"})], next_cursor=None
            )

        async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
            self.calls.append((tool, arguments))
            clock["seconds"] += 61
            result = {
                "source_outcomes": [{"source": "ozon", "elapsed_ms": 61000}],
                "cheapest": None,
                "cheapest_comparable": None,
            }
            return SimpleNamespace(structured_content=result, content=[], is_error=False)

    session = CompareSession(budget)
    plan = [c for c in audit.plan_run(cases, ids, {}, "C", 1) if c["kind"] == "compare"][:1]
    rows = await audit.execute_plan(plan, session, budget, cases, tmp_path, "C")
    assert rows[0]["latency_s"] == 61
    assert rows[0]["source_latencies_ms"] == {"ozon": 61000}
    assert rows[0]["window_hidden"] is True


# END_BLOCK_RUNNER
# START_BLOCK_COMPARE_RECORDING


def recorder() -> Any:
    """The recorder module, imported inside the test so a missing one fails here."""
    return importlib.import_module("compare_recording")


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
def test_the_recorder_stores_only_the_fields_the_adapters_read() -> None:
    """The stored payload is the cut-down the adapters read, and nothing else."""
    recording = recorder()
    item = SimpleNamespace(
        nm_id=101,
        name="Чайник электрический 1.7 л",
        brand="Бренд",
        supplier="Магазин",
        price_rub=1990.0,
        price_kind="regular",
        wallet_price_rub=None,
        review_rating=4.8,
        feedbacks=12,
        in_stock=True,
        total_quantity=3,
        color="белый",
        price_original_rub=2490.0,
        delivery="11 октября",
        seller_legal_name="ООО Ромашка",
    )
    response = SimpleNamespace(
        items=[item],
        meta=SimpleNamespace(healthy=True, warnings=["info:wb_dom"], source="wb_search", extraction="api"),
    )

    cut = recording.cut_response("wildberries", response)

    assert set(cut["items"][0]) == {
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
    }
    assert cut["items"][0]["price_rub"] == 1990.0
    assert cut["items"][0]["price_kind"] == "regular"
    assert cut["meta"] == {"healthy": True, "warnings": ["info:wb_dom"]}
    # No reviewer, phone, e-mail, delivery or legal-entity field ever enters the file.
    stored = json.dumps(cut, ensure_ascii=False)
    assert "seller_legal_name" not in stored
    assert "delivery" not in stored
    assert "color" not in stored


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
def test_the_scrub_replaces_regions_with_placeholders_in_order_of_appearance() -> None:
    """Regions become R1, R2, ... in order of appearance, and equal regions stay equal."""
    recording = recorder()
    document = {
        "query": "чайник",
        "captured_at": "2026-11-16T10:00:00Z",
        "schema": 1,
        "sources": {
            "wildberries": {"response": {"items": [], "region": "Москва", "observed_at": "2026-11-16T10:00:00Z"}},
            "avito": {"response": {"items": [], "region": "Санкт-Петербург", "observed_at": "2026-11-16T10:00:00Z"}},
            "ozon": {"response": {"items": [], "region": "Москва", "observed_at": "2026-11-16T10:00:00Z"}},
        },
    }

    scrubbed = recording.scrub_regions(document)

    regions = [entry["response"]["region"] for entry in scrubbed["sources"].values()]
    assert regions == ["R1", "R2", "R1"]
    assert "Москва" not in json.dumps(scrubbed, ensure_ascii=False)
    assert "Санкт-Петербург" not in json.dumps(scrubbed, ensure_ascii=False)


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
def test_the_recording_holds_the_query_the_time_and_each_source() -> None:
    """One recording is one compare call: the query, the capture time, the sources."""
    recording = recorder()
    document = recording.build_recording(
        "чайник электрический",
        "2026-11-16T10:00:00Z",
        {
            "wildberries": {
                "response": {
                    "items": [{"nm_id": 101, "name": "Чайник", "price_rub": 1990.0}],
                    "meta": {"healthy": True, "warnings": []},
                    "region": "Москва",
                    "observed_at": "2026-11-16T10:00:00Z",
                }
            },
            "ozon": {"error": {"code": "rate_limited", "message": "ozon rate-limited"}},
        },
    )

    assert set(document) == {"query", "captured_at", "sources", "schema"}
    assert document["schema"] == 1
    assert document["query"] == "чайник электрический"
    assert document["captured_at"] == "2026-11-16T10:00:00Z"
    assert document["sources"]["ozon"] == {"error": {"code": "rate_limited", "message": "ozon rate-limited"}}
    assert document["sources"]["wildberries"]["response"]["region"] == "R1"


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
async def test_the_capture_hook_records_what_the_adapters_receive(tmp_path: Path) -> None:
    """The hook sits on the search tool the adapters call and stores its answer."""
    recording = recorder()
    seen: list[tuple[str, int]] = []

    class FakeWb:
        async def wb_search(self, query: str, page: int = 1) -> Any:
            seen.append((query, page))
            return SimpleNamespace(
                items=[SimpleNamespace(nm_id=101, name="Чайник", price_rub=1990.0, price_kind="regular")],
                meta=SimpleNamespace(healthy=True, warnings=[]),
            )

    module = FakeWb()
    recording.capture_sources(tmp_path, {"wildberries": module})

    await module.wb_search(query="чайник", page=1)

    assert seen == [("чайник", 1)]
    files = sorted(tmp_path.glob("*.json"))
    assert len(files) == 1
    captured = json.loads(files[0].read_text(encoding="utf-8"))
    assert captured["source"] == "wildberries"
    assert captured["query"] == "чайник"
    assert captured["response"]["items"][0]["nm_id"] == 101


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
async def test_a_failing_source_is_recorded_as_its_error_and_still_raises(tmp_path: Path) -> None:
    """An errored source is stored as its error, and the error still reaches the caller."""
    recording = recorder()

    class FailingWb:
        async def wb_search(self, query: str, page: int = 1) -> Any:
            raise ToolError(json.dumps({"error": "rate_limited", "message": "wb rate-limited", "retryable": True}))

    module = FailingWb()
    recording.capture_sources(tmp_path, {"wildberries": module})

    with pytest.raises(ToolError):
        await module.wb_search(query="чайник")

    captured = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text(encoding="utf-8"))
    assert captured["error"] == {"code": "rate_limited", "message": "wb rate-limited"}


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
async def test_the_audit_writes_one_recording_per_compare_call(audit: Any, tmp_path: Path) -> None:
    """The compare call the audit measured is the comparison that gets recorded."""
    cases, ids = inputs(audit)
    monkey_capture = tmp_path / "captures"
    record_dir = tmp_path / "records"
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))

    class CompareSession(FakeSession):
        async def list_tools(self, **kwargs: Any) -> Any:
            return SimpleNamespace(
                tools=[SimpleNamespace(name="compare_prices", output_schema={"type": "object"})], next_cursor=None
            )

        async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
            self.calls.append((tool, arguments))
            # The child writes what the adapters received; the parent turns it
            # into one recording per compare call.
            monkey_capture.mkdir(parents=True, exist_ok=True)
            (monkey_capture / "0001-wildberries.json").write_text(
                json.dumps(
                    {
                        "source": "wildberries",
                        "query": arguments["query"],
                        "captured_at": "2026-11-16T09:00:00Z",
                        "response": {
                            "items": [{"nm_id": 1, "name": "Чайник", "price_rub": 1990.0}],
                            "meta": {"healthy": True, "warnings": []},
                            "region": "Москва",
                            "observed_at": "2026-11-16T09:00:00Z",
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = {
                "source_outcomes": [
                    {"source": "wildberries", "status": "ok", "offers_returned": 1, "elapsed_ms": 10},
                    {
                        "source": "ozon",
                        "status": "blocked",
                        "error_code": "rate_limited",
                        "detail": "ozon rate-limited",
                    },
                ],
                "cheapest": None,
                "cheapest_comparable": None,
            }
            return SimpleNamespace(structured_content=result, content=[], is_error=False)

    session = CompareSession(budget)
    plan = [c for c in audit.plan_run(cases, ids, {}, "C", 1) if c["kind"] == "compare"][:1]
    await audit.execute_plan(
        plan,
        session,
        budget,
        cases,
        tmp_path,
        "C",
        record_dir=record_dir,
        capture_dir=monkey_capture,
        record_prefix="c1-",
    )

    files = sorted(record_dir.glob("*.json"))
    assert len(files) == 1
    document = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(document) == {"query", "captured_at", "sources", "schema"}
    assert document["schema"] == 1
    assert document["sources"]["wildberries"]["response"]["region"] == "R1"
    assert document["sources"]["ozon"] == {"error": {"code": "rate_limited", "message": "ozon rate-limited"}}
    # The capture files are private and unscrubbed: they do not survive the run.
    assert not list(monkey_capture.glob("*.json"))


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
async def test_a_lone_card_read_writes_no_recording(audit: Any, tmp_path: Path) -> None:
    """A compare call that only re-read an Ozon card (no search) is no comparison to record."""
    cases, ids = inputs(audit)
    monkey_capture = tmp_path / "captures"
    record_dir = tmp_path / "records"
    budget = live_budget.Budget(40, 8, 3, live_budget.Ledger(tmp_path / "ledger.json"))

    class CardOnlySession(FakeSession):
        async def list_tools(self, **kwargs: Any) -> Any:
            return SimpleNamespace(
                tools=[SimpleNamespace(name="compare_prices", output_schema={"type": "object"})], next_cursor=None
            )

        async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
            self.calls.append((tool, arguments))
            monkey_capture.mkdir(parents=True, exist_ok=True)
            card = {"source": "ozon", "query": "", "card": "42", "response": {"price": 990.0, "card_price": 900.0}}
            (monkey_capture / "0001-ozon.json").write_text(json.dumps(card), encoding="utf-8")
            return SimpleNamespace(structured_content={"source_outcomes": []}, content=[], is_error=False)

    plan = [c for c in audit.plan_run(cases, ids, {}, "C", 1) if c["kind"] == "compare"][:1]
    await audit.execute_plan(
        plan,
        CardOnlySession(budget),
        budget,
        cases,
        tmp_path,
        "C",
        record_dir=record_dir,
        capture_dir=monkey_capture,
        record_prefix="c1-",
    )

    assert not list(record_dir.glob("*.json"))
    assert not list(monkey_capture.glob("*.json"))


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
def test_recording_is_a_phase_c_option_only(audit: Any) -> None:
    """The recorder belongs to the compare phase; the other phases refuse it."""
    outside = str(audit.REPO_ROOT.parent / "compare-recordings")
    assert audit.select_options(["--phase", "C", "--run", "1", "--record", outside]).record is not None
    with pytest.raises(ValueError):
        audit.select_options(["--phase", "A", "--run", "1", "--record", outside])
    with pytest.raises(ValueError):
        audit.select_options(["--summary", "--record", outside])


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
def test_a_record_dir_inside_the_repository_must_be_the_ignored_one(audit: Any) -> None:
    """A recording is private material: inside the repository only .live-recordings/ (gitignored) takes it."""
    ignored = str(audit.REPO_ROOT / ".live-recordings" / "cycle1")
    assert audit.select_options(["--phase", "C", "--run", "1", "--record", ignored]).record is not None
    for tracked in (
        audit.REPO_ROOT,
        audit.REPO_ROOT / "packages" / "compare-connector" / "tests" / "benchmark" / "recordings",
        audit.REPO_ROOT / ".tmp" / "records",
    ):
        with pytest.raises(ValueError):
            audit.select_options(["--phase", "C", "--run", "1", "--record", str(tracked)])


# END_BLOCK_COMPARE_RECORDING
