"""Offline requirement checks for the P1-4 outside-in harness.

START_MODULE_CONTRACT
PURPOSE: Prove wrapped-child accounting, plans, audit checks, captures, timing and private output.
SCOPE: Real child wrapper and MCP protocol with fake server targets; fake CDP only; no sockets to sites.
DEPENDS: live_audit, audit_server, live_budget, live_check; pytest; httpx; MCP; registry; stdlib.
LINKS: M-LIVE-AUDIT, R-LIVE-AUDIT.
START_MODULE_MAP
  audit: Import the new implementation inside each test, retaining individual pre-implementation failures.
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


@pytest.mark.req("R-LIVE-AUDIT.plans")
@pytest.mark.parametrize("costly", [False, True])
@pytest.mark.parametrize(("phase", "runs"), [("A", 2), ("B", 3), ("C", 4)])
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
            assert sum(call["tool"] == "compare_prices" for call in plan) == 2
            assert sum(call["tool"] == "compare_verify_offer" for call in plan) == 4


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_phase_a_registry_coverage_and_wb_split(audit: Any) -> None:
    cases, ids = inputs(audit)
    expected = {
        tool.name
        for spec in SOURCES
        if spec.name in cases["fixed_queries"]
        for tool in registered_tools(importlib.import_module(spec.module).mcp)
    }
    plans = [audit.plan_run(cases, ids, {}, "A", n) for n in (1, 2)]
    names = [call["tool"] for plan in plans for call in plan]
    assert len(expected) == len(names) == 33
    assert set(names) == expected
    assert [sum(call["source"] == "wildberries" for call in plan) for plan in plans] == [5, 3]
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
    for phase, run, cycle in [("A", 3, 1), ("B", 0, 1), ("C", 5, 1), ("A", 1, 2), ("C", 1, 999)]:
        with pytest.raises(ValueError):
            audit.plan_run(cases, ids, {}, phase, run, cycle=cycle)


@pytest.mark.req("R-LIVE-AUDIT.plans")
def test_compare_queries_precede_winner_spending(audit: Any) -> None:
    cases, ids = inputs(audit)
    plan = audit.plan_run(cases, ids, {("ozon", "listing"): 3}, "C", 1)
    assert [call["tool"] for call in plan[:2]] == ["compare_prices", "compare_prices"]


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
    for n in range(1, 5):
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
