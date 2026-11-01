"""``marketplace_health`` answers transport health without spending a navigation.

The tool exists so an agent planning a fan-out can learn that Chrome is down or
that a host is cooling down *before* paying for a navigation. These tests pin,
offline, the three promises that make it worth calling: the CDP part only reads
(the two HTTP endpoints, never a tab, never an autostart, no budget spent),
every part survives its own failure inside the answer, and the static tables
(``needs_cdp``, ``unblock``) follow the mounted set instead of a copy.

Offline by construction: no socket is opened — the CDP endpoint is faked — and
no browser or marketplace is touched.
"""

from __future__ import annotations

import asyncio
import sys

from marketplace_connector import server
from marketplace_connector.unblock import UNBLOCK_PAGES
from mcp_core import transport as core_transport
from mcp_core.transport import browser_handoff, cdp_budget, chrome_cdp


def _dead_cdp(monkeypatch) -> None:
    """Make the CDP endpoint read fail immediately, so no socket is opened."""

    def dead_endpoint(path: str, *, timeout_s: float) -> object:
        raise OSError(f"nothing listens for {path}")

    monkeypatch.setattr(chrome_cdp, "_cdp_http_json", dead_endpoint)


def _forbid_browser(monkeypatch) -> None:
    """Fail the test if anything starts, attaches to or probes the browser.

    The health tool's hard rule is that the browser tier is only *read*: the
    autostart entry, the attach it lives behind, the spawn itself and the
    Playwright probe that would start Chrome must all stay untouched.
    """

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("marketplace_health must not start or attach to Chrome")

    monkeypatch.setattr(chrome_cdp, "_ensure_cdp_running", explode)
    monkeypatch.setattr(chrome_cdp, "_start_chrome_with_cdp", explode)
    monkeypatch.setattr(chrome_cdp, "get_browser", explode)
    monkeypatch.setattr(chrome_cdp, "probe_session", explode)


def test_health_reports_unreachable_cdp_without_autostart_or_navigation(monkeypatch):
    _forbid_browser(monkeypatch)
    _dead_cdp(monkeypatch)
    monkeypatch.setattr(browser_handoff, "_leases", {})
    monkeypatch.setattr(cdp_budget, "_BUILT", cdp_budget.NavigationBudget())
    budget_before = cdp_budget.budget_snapshot()
    handoffs_before = browser_handoff.handoff_diagnostics()

    result = asyncio.run(server.marketplace_health())

    assert result.cdp["reachable"] is False
    assert result.cdp["version"] is None
    assert result.cdp["host"] == chrome_cdp.CDP_HOST
    assert result.cdp["port"] == chrome_cdp.CDP_PORT
    assert "OSError" in str(result.cdp["reason"])
    # A navigation would move these: no permit was taken and no lease appeared.
    assert budget_before["in_flight"] == 0
    assert handoffs_before["count"] == 0
    assert cdp_budget.budget_snapshot() == budget_before
    assert browser_handoff.handoff_diagnostics() == handoffs_before


def test_health_reads_only_the_version_and_target_endpoints(monkeypatch):
    """The probe is two bounded GETs; the version is what Chrome answers with."""
    reads: list[tuple[str, float]] = []

    def live_endpoint(path: str, *, timeout_s: float) -> object:
        reads.append((path, timeout_s))
        if path == "/json/version":
            return {"Browser": "Chrome/141.0.7390.55"}
        return [
            {"type": "page", "id": "A"},
            {"type": "page", "id": "B"},
            {"type": "service_worker", "id": "C"},
        ]

    monkeypatch.setattr(chrome_cdp, "_cdp_http_json", live_endpoint)
    _forbid_browser(monkeypatch)

    result = asyncio.run(server.marketplace_health())

    assert result.cdp["reachable"] is True
    assert result.cdp["version"] == "Chrome/141.0.7390.55"
    assert result.cdp["page_targets"] == 2
    assert [path for path, _ in reads] == ["/json/version", "/json/list"]
    assert all(timeout_s <= 2.0 for _, timeout_s in reads)


def test_health_reports_an_open_breaker_with_its_remaining_pause(monkeypatch):
    _dead_cdp(monkeypatch)
    budget = cdp_budget.NavigationBudget(breaker_threshold=1, breaker_cooldown_s=60.0, clock=lambda: 100.0)
    monkeypatch.setattr(cdp_budget, "_BUILT", budget)

    async def refuse_once() -> None:
        slot = await budget.acquire("avito.ru")
        slot.refused(429)
        slot.release()

    asyncio.run(refuse_once())

    result = asyncio.run(server.marketplace_health())

    hosts = result.budget["hosts"]
    assert isinstance(hosts, dict)
    paused = hosts["avito.ru"]
    assert paused["open"] is True
    assert paused["refusals"] == 1
    assert paused["retry_after_s"] == 60.0
    assert result.budget["in_flight"] == 0


def test_health_reports_a_failing_part_inside_the_answer(monkeypatch):
    """A broken part must not take the whole pre-flight answer down with it."""

    secret = r"C:\Users\someone\AppData\Local\Chrome-Scraping diagnostics broke"

    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(chrome_cdp, "cdp_endpoint_status", boom)
    monkeypatch.setattr(cdp_budget, "budget_snapshot", boom)
    monkeypatch.setattr(browser_handoff, "handoff_diagnostics", boom)

    result = asyncio.run(server.marketplace_health())

    assert result.cdp["reachable"] is False
    assert "RuntimeError" in str(result.cdp["error"])
    assert "RuntimeError" in str(result.budget["error"])
    assert "RuntimeError" in str(result.handoffs["error"])
    answer = result.model_dump_json()
    assert "someone" not in answer and "diagnostics broke" not in answer
    assert result.unblock, "the static part must survive a failing browser tier"
    assert result.server_version == server.SERVER_VERSION


def test_health_survives_a_missing_optional_browser_tier(monkeypatch):
    """With no Playwright installed the cdp extra is absent, not a crash."""
    monkeypatch.delattr(core_transport, "chrome_cdp", raising=False)
    monkeypatch.setitem(sys.modules, "mcp_core.transport.chrome_cdp", None)

    result = asyncio.run(server.marketplace_health())

    assert result.cdp["reachable"] is False
    assert "error" in result.cdp
    assert isinstance(result.budget, dict)
    assert isinstance(result.handoffs, dict)


def test_health_names_the_page_that_unblocks_a_mounted_source(monkeypatch):
    _dead_cdp(monkeypatch)
    monkeypatch.setattr(server, "_MOUNTED", ["ozon", "cian", "wildberries", "compare"])

    result = asyncio.run(server.marketplace_health())

    assert result.unblock == {"cian": UNBLOCK_PAGES["cian"], "ozon": UNBLOCK_PAGES["ozon"]}


def test_health_is_advertised_as_read_only():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}

    annotations = tools["marketplace_health"].to_mcp_tool().annotations

    assert annotations is not None
    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is True
    assert annotations.open_world_hint is False


def test_needs_cdp_lists_exactly_the_browser_backed_mounted_sources():
    result = asyncio.run(server.marketplace_sources())

    assert result.needs_cdp == [
        "aliexpress",
        "avito",
        "cian",
        "citilink",
        "dns",
        "lamoda",
        "megamarket",
        "ozon",
        "taobao",
    ]
    # Additive field: the rest of the mounted view is unchanged.
    assert result.mounted_count == 13
    assert result.capabilities["ozon"]["access"] == "http_or_cdp"


def test_needs_cdp_follows_the_mounted_set(monkeypatch):
    """Derived from what mounted, not a copy of the capability table."""
    monkeypatch.setattr(server, "_MOUNTED", ["wildberries", "cian", "compare", "yandex"])
    monkeypatch.setattr(server, "_SKIPPED", {})

    result = asyncio.run(server.marketplace_sources())

    assert result.needs_cdp == ["cian"]


def test_needs_cdp_reads_a_mount_name_through_its_canonical_source(monkeypatch):
    """A mount name that differs from its capability key still finds its row.

    "yandex" mounts the source whose capability key is "yandex_market"; with
    that row made browser-backed here, only the alias lookup can list it.
    """
    monkeypatch.setattr(server, "_MOUNTED", ["wildberries", "yandex"])
    monkeypatch.setattr(server, "_SKIPPED", {})
    monkeypatch.setitem(server.CAPABILITIES, "yandex_market", {**server.CAPABILITIES["yandex_market"], "access": "cdp"})

    result = asyncio.run(server.marketplace_sources())

    assert result.needs_cdp == ["yandex"]


def test_health_keeps_the_profile_path_and_url_tokens_out_of_the_answer(monkeypatch):
    """A retained lease is reported without the OS user name or a URL token."""
    lease = {
        "scope": "s",
        "operation": "avito_card",
        "url": "https://www.avito.ru/item_1?challenge_token=abc123#frag",
        "endpoint": "http://127.0.0.1:9222",
        "profile": r"C:\Users\someone\AppData\Local\Chrome-Scraping",
        "handoff_id": "h1",
    }
    monkeypatch.setattr(browser_handoff, "handoff_diagnostics", lambda: {"count": 1, "leases": [dict(lease)]})
    _dead_cdp(monkeypatch)

    result = asyncio.run(server.marketplace_health())

    reported = result.handoffs["leases"][0]
    assert reported["profile"] == "Chrome-Scraping"
    assert reported["url"] == "https://www.avito.ru/item_1"
    assert reported["handoff_id"] == "h1"
    answer = result.model_dump_json()
    assert "someone" not in answer and "abc123" not in answer
