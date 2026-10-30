"""The live budget proves its counting offline: fake transports, fake clock.

R-LIVE-BUDGET: 40 total, 8 per marketplace, 3 s between requests to one
marketplace, loopback excluded, both caps raise before sending, the Ozon
subprocess hand-off is counted, a renamed hook point fails loudly, and two
installs share one ledger file.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import live_budget


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    async def async_sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_installation() -> Any:
    live_budget.uninstall()
    yield
    live_budget.uninstall()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _budget(
    tmp_path: Path,
    clock: FakeClock,
    *,
    total: int = 40,
    per_source: int = 8,
    gap_s: float = 3.0,
    ledger_path: Path | None = None,
) -> live_budget.Budget:
    path = ledger_path if ledger_path is not None else tmp_path / "live-budget.json"
    return live_budget.Budget(
        total,
        per_source,
        gap_s,
        live_budget.Ledger(path),
        clock=clock,
        sleep_sync=clock.sleep,
        sleep_async=clock.async_sleep,
    )


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    def fake_send(self: Any, request: httpx.Request) -> str:
        sent.append(str(request.url))
        return "sent"

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    return sent


def test_counting_per_group_and_loopback_is_free(
    tmp_path: Path,
    clock: FakeClock,
    fake_http: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    live_budget.install(_budget(tmp_path, clock))
    client = httpx.Client()

    client.send(httpx.Request("GET", "https://www.ozon.ru/product/1"))
    client.send(httpx.Request("GET", "https://card.wb.ru/cards/v4/detail"))
    client.send(httpx.Request("GET", "http://127.0.0.1:9222/json/list"))
    client.send(httpx.Request("GET", "http://localhost:9222/json/version"))

    entries = live_budget.Ledger(tmp_path / "live-budget.json").load()
    assert [entry["group"] for entry in entries] == ["ozon", "wildberries"]
    assert [entry["kind"] for entry in entries] == ["httpx", "httpx"]
    assert len(fake_http) == 4
    assert clock.sleeps == []

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("[req 1/40] ozon 1/8 httpx https://www.ozon.ru/")
    assert lines[1].startswith("[req 2/40] wildberries 1/8 httpx https://card.wb.ru/")


def test_the_per_source_cap_raises_before_sending(tmp_path: Path, clock: FakeClock, fake_http: list[str]) -> None:
    live_budget.install(_budget(tmp_path, clock, per_source=1))
    client = httpx.Client()

    client.send(httpx.Request("GET", "https://www.ozon.ru/one"))
    with pytest.raises(live_budget.BudgetExhausted, match="ozon: 1/1"):
        client.send(httpx.Request("GET", "https://www.ozon.ru/two"))

    assert len(fake_http) == 1
    assert len(live_budget.Ledger(tmp_path / "live-budget.json").load()) == 1


def test_the_total_cap_raises_before_sending(tmp_path: Path, clock: FakeClock, fake_http: list[str]) -> None:
    live_budget.install(_budget(tmp_path, clock, total=1))
    client = httpx.Client()

    client.send(httpx.Request("GET", "https://www.ozon.ru/one"))
    with pytest.raises(live_budget.BudgetExhausted, match="1/1 total"):
        client.send(httpx.Request("GET", "https://card.wb.ru/two"))

    assert len(fake_http) == 1


def test_a_request_too_soon_waits_out_the_gap(tmp_path: Path, clock: FakeClock, fake_http: list[str]) -> None:
    live_budget.install(_budget(tmp_path, clock))
    client = httpx.Client()

    client.send(httpx.Request("GET", "https://www.ozon.ru/one"))
    client.send(httpx.Request("GET", "https://www.ozon.ru/two"))
    client.send(httpx.Request("GET", "https://www.ozon.ru/three"))

    assert clock.sleeps == [3.0, 3.0]
    assert len(fake_http) == 3


def test_the_ozon_subprocess_hook_is_counted(tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    import ozon_connector.server as ozon_server

    calls: list[Any] = []

    async def fake_bounded(func: Any, *args: Any, **kwargs: Any) -> str:
        calls.append((func, args, kwargs))
        return "from-child"

    monkeypatch.setattr(ozon_server, "_run_sync_bounded", fake_bounded)
    live_budget.install(_budget(tmp_path, clock))

    result = asyncio.run(
        ozon_server._run_sync_bounded(lambda: None, "https://api-seller.ozon.ru/v1/search", timeout_s=1)
    )

    assert result == "from-child"
    assert len(calls) == 1
    entries = live_budget.Ledger(tmp_path / "live-budget.json").load()
    assert [(entry["group"], entry["kind"]) for entry in entries] == [("ozon", "curl(subprocess)")]


def test_an_in_page_fetch_is_counted(tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.async_api import Page

    async def fake_evaluate(self: Any, expression: str, arg: Any = None, *args: Any, **kwargs: Any) -> str:
        return "evaluated"

    monkeypatch.setattr(Page, "evaluate", fake_evaluate)
    live_budget.install(_budget(tmp_path, clock))

    plain = asyncio.run(Page.evaluate(object(), "document.title"))
    counted = asyncio.run(Page.evaluate(object(), "await fetch(args.url, {})", {"url": "https://www.ozon.ru/api"}))

    assert plain == "evaluated"
    assert counted == "evaluated"
    entries = live_budget.Ledger(tmp_path / "live-budget.json").load()
    assert [(entry["group"], entry["kind"]) for entry in entries] == [("ozon", "in-page fetch")]


def test_a_missing_hook_point_raises(tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(httpx.AsyncClient, "send", raising=False)

    with pytest.raises(AttributeError, match=r"httpx\.AsyncClient\.send"):
        live_budget.install(_budget(tmp_path, clock))

    assert live_budget.installed_budget() is None


def test_two_installs_share_one_ledger_file(tmp_path: Path, clock: FakeClock, fake_http: list[str]) -> None:
    path = tmp_path / "shared.json"
    client = httpx.Client()

    live_budget.install(_budget(tmp_path, clock, ledger_path=path))
    client.send(httpx.Request("GET", "https://www.ozon.ru/one"))
    live_budget.uninstall()

    live_budget.install(_budget(tmp_path, clock, ledger_path=path))
    client.send(httpx.Request("GET", "https://card.wb.ru/two"))

    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert [(entry["group"], entry["kind"]) for entry in entries] == [("ozon", "httpx"), ("wildberries", "httpx")]
    assert len(fake_http) == 2


def test_limits_and_ledger_path_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_BUDGET_TOTAL", "7")
    monkeypatch.setenv("LIVE_BUDGET_PER_SOURCE", "3")
    monkeypatch.setenv("LIVE_BUDGET_GAP_S", "1.5")
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / "env.json"))

    budget = live_budget.Budget.from_env()

    assert (budget.total, budget.per_source, budget.gap_s) == (7, 3, 1.5)
    assert budget.ledger.path == tmp_path / "env.json"


def test_a_bad_environment_limit_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_BUDGET_TOTAL", "forty")

    with pytest.raises(ValueError, match="LIVE_BUDGET_TOTAL"):
        live_budget.Budget.from_env()


def test_reset_archives_the_ledger_and_the_next_request_starts_a_new_run(
    tmp_path: Path, clock: FakeClock, fake_http: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    import live_check

    ledger_path = tmp_path / ".live-budget.json"
    budget = _budget(tmp_path, clock, per_source=1, ledger_path=ledger_path)
    live_budget.install(budget)
    httpx.Client().send(httpx.Request("GET", "https://www.ozon.ru/a"))
    with pytest.raises(live_budget.BudgetExhausted, match=r"live_check.py reset"):
        httpx.Client().send(httpx.Request("GET", "https://www.ozon.ru/b"))
    before = ledger_path.read_bytes()

    assert live_check._reset(budget) == 0

    assert not ledger_path.exists()
    archived = list(tmp_path.glob(".live-budget-*.json"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == before
    httpx.Client().send(httpx.Request("GET", "https://www.ozon.ru/c"))
    assert [entry["url"] for entry in budget.ledger.load()] == ["https://www.ozon.ru/c"]
    assert "archived the previous run" in capsys.readouterr().out
