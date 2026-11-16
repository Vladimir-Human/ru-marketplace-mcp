"""The operator entry point proves its dispatch offline: a fake doctor round.

R-LIVE-PREFLIGHT: temp, driver and bare CDP checks precede budget installation.
Playwright and tempfile failures are edge doubles; main and budget are real.

START_MODULE_CONTRACT
PURPOSE: Verify offline dispatch and fail-before-budget live preflight through main.
SCOPE: Temp repair/failure, driver repair/failure, CDP refusal, doctor budget and usage exits.
DEPENDS: live_check, live_budget; scripts/conftest.py Playwright edges; pytest; stdlib.
LINKS: M-LIVE-CHECK, R-LIVE-PREFLIGHT, R-LIVE-BUDGET, R-DOCTOR-EXIT-CODES.
START_MODULE_MAP
  _clean_installation  restore budget hooks around each test
  fake_doctor          offline doctor dispatch edge
  test_*               entry-level budget, preflight and exit checks
END_MODULE_MAP
END_MODULE_CONTRACT

R-LIVE-BUDGET: the release checklist's doctor round runs through
``scripts/live_check.py``, so it must run under the same budget as the other
live commands, on a ledger of its own, and hand the doctor's own exit code
back to the caller.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import live_budget
import live_check

pytestmark = pytest.mark.usefixtures("offline_playwright")

# START_BLOCK_DISPATCH_TESTS


@pytest.fixture(autouse=True)
def _clean_installation() -> Any:
    live_budget.uninstall()
    yield
    live_budget.uninstall()


@pytest.fixture
def fake_doctor(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], bool, Any]]:
    """Replace the CLI's doctor with one that records how it was called."""
    calls: list[tuple[list[str], bool, Any]] = []

    def fake_main(argv: list[str]) -> int:
        budget = live_budget.installed_budget()
        calls.append((list(argv), budget is not None, budget.ledger.path if budget else None))
        return 2

    monkeypatch.setattr("marketplace_connector.cli.main", fake_main)
    return calls


def test_the_doctor_command_runs_under_the_budget(
    tmp_path: Path,
    fake_doctor: list[tuple[list[str], bool, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The budget is installed before the doctor starts, and the ledger is ours."""
    ledger = tmp_path / ".live-budget.json"
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(ledger))

    exit_code = live_check.main(["doctor"])

    assert exit_code == 2
    assert fake_doctor == [(["doctor"], True, ledger)]
    assert "total 0/40" in capsys.readouterr().out


def test_the_doctor_command_passes_its_arguments_through(
    tmp_path: Path,
    fake_doctor: list[tuple[list[str], bool, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))

    exit_code = live_check.main(["doctor", "ozon"])

    assert exit_code == 2
    assert fake_doctor[0][0] == ["doctor", "ozon"]


def test_the_ledger_summary_follows_the_doctor_round(
    tmp_path: Path,
    fake_doctor: list[tuple[list[str], bool, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What the run spent is reported after the doctor, not before."""
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))

    live_check.main(["doctor"])

    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1].startswith("total 0/40")


def test_the_ledger_summary_names_the_requests_the_budget_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A doctor row that read transport_down because of the cap must be explainable from the summary."""
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))
    monkeypatch.setenv("LIVE_BUDGET_PER_SOURCE", "1")

    def fake_main(argv: list[str]) -> int:
        budget = live_budget.installed_budget()
        assert budget is not None
        budget.reserve("chrome-load", "https://www.ozon.ru/")
        for _ in range(2):
            with pytest.raises(live_budget.BudgetExhausted):
                budget.reserve("in-page fetch", "https://www.ozon.ru/api/x")
        return 2

    monkeypatch.setattr("marketplace_connector.cli.main", fake_main)

    assert live_check.main(["doctor", "ozon"]) == 2

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-2] == "total 1/40; ozon 1/1"
    assert lines[-1].startswith("refused before sending (over a cap): ozon 2;")


@pytest.mark.req("R-NIGHTLY-DRIFT-ALARM")
@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no-arguments"),
        pytest.param(["nonsense"], id="unknown-mode"),
        pytest.param(["selfcheck"], id="selfcheck-without-a-source"),
        pytest.param(["selfcheck", "not-a-source"], id="unknown-source"),
        pytest.param(["call", "mod", "tool"], id="call-without-kwargs"),
        pytest.param(["call", "mod", "tool", "{not json"], id="unparsable-kwargs"),
        pytest.param(["call", "mod", "tool", "[1, 2]"], id="non-object-kwargs"),
    ],
)
def test_a_usage_failure_exits_4_not_the_doctors_blocked_code(
    argv: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 4 for live_check's own usage errors.

    The CI drift alarm keeps the doctor's 2 ("blocked/inconclusive") green, so a
    mis-typed command line must not borrow that code and read as a blocked
    source. Nothing here reaches a marketplace.
    """
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))

    assert live_check.main(argv) == 4


def test_a_failed_budget_install_exits_4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setup failure is a broken alarm, not a blocked source: the hooks that
    count requests could not be installed, so no check ran at all."""
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))

    def broken_install() -> live_budget.Budget:
        raise AttributeError("live budget hook point httpx.AsyncClient.send is missing")

    monkeypatch.setattr(live_budget, "install", broken_install)

    assert live_check.main(["selfcheck", "wb"]) == 4


@pytest.mark.req("R-LIVE-PREFLIGHT")
@pytest.mark.parametrize("ready,code", [(True, 0), (False, 4)])
def test_preflight_alone_reports_its_result_and_installs_no_budget(
    ready: bool,
    code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`preflight` runs the setup checks, prints them as they are, and spends nothing."""
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(tmp_path / ".live-budget.json"))

    async def fake_preflight() -> bool:
        print("cdp: failed; run: marketplace-mcp browser start")
        return ready

    def no_install() -> live_budget.Budget:
        raise AssertionError("preflight must not install the budget")

    monkeypatch.setattr(live_check, "_preflight", fake_preflight)
    monkeypatch.setattr(live_budget, "install", no_install)

    assert live_check.main(["preflight"]) == code
    assert "marketplace-mcp browser start" in capsys.readouterr().out
    assert not (tmp_path / ".live-budget.json").exists()


# END_BLOCK_DISPATCH_TESTS
# START_BLOCK_PREFLIGHT_TESTS


@pytest.fixture
def preflight_call(tmp_path, monkeypatch, offline_playwright):
    """Tool I/O edge records ordering and books locally, never sends a request."""
    ledger = tmp_path / "ledger.json"
    ledger.write_text('{"entries": []}\n', encoding="utf-8")
    monkeypatch.setenv("LIVE_BUDGET_LEDGER", str(ledger))
    monkeypatch.delenv("PLAYWRIGHT_NODEJS_PATH", raising=False)
    # Ensure tempfile's process cache, as well as environment edits, is restored.
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    calls = []
    module = ModuleType("offline_preflight_tool")

    async def tool():
        calls.append(dict(os.environ))
        offline_playwright.events.append("tool")
        budget = live_budget.installed_budget()
        assert budget is not None
        budget.reserve("offline", "https://www.ozon.ru/")
        return {"ok": True}

    module.tool = tool
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return ["call", module.__name__, "tool", "{}"], ledger, calls


@pytest.mark.req("R-LIVE-PREFLIGHT.temp")
def test_unwritable_temp_is_repaired_before_tool_code(preflight_call, monkeypatch, offline_playwright, capsys):
    """R-LIVE-PREFLIGHT.temp: the repo fallback takes effect before driver/tool I/O."""
    argv, ledger, calls = preflight_call
    real_mkdtemp = tempfile.mkdtemp
    fallback = live_check.REPO_ROOT / ".tmp" / "live"
    attempts = []

    def unwritable_default(*args, **kwargs):
        attempts.append(os.environ["TEMP"])
        if os.environ["TEMP"] != str(fallback):
            raise PermissionError("No usable temporary directory")
        assert tempfile.tempdir is None
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkdtemp", unwritable_default)
    assert live_check.main(argv) == 0
    assert len(attempts) == 2
    assert calls[0]["TEMP"] == calls[0]["TMP"] == str(fallback)
    assert offline_playwright.environments[0]["TEMP"] == str(fallback)
    assert offline_playwright.events.index("cdp.disconnect") < offline_playwright.events.index("tool")
    assert '"group": "ozon"' in ledger.read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert "preflight temp: fixed" in err


@pytest.mark.req("R-LIVE-PREFLIGHT.temp")
@pytest.mark.parametrize("mode", ["call", "doctor"])
def test_failed_temp_repair_stops_before_budget(mode, preflight_call, monkeypatch, offline_playwright, capsys):
    """R-LIVE-PREFLIGHT.temp: both unwritable locations leave the ledger untouched."""
    argv, ledger, calls = preflight_call
    before = ledger.read_bytes()

    def unwritable(*args, **kwargs):
        raise PermissionError("No usable temporary directory")

    monkeypatch.setattr(tempfile, "mkdtemp", unwritable)
    assert live_check.main(argv if mode == "call" else ["doctor"]) == 4
    assert ledger.read_bytes() == before
    assert live_budget.installed_budget() is None
    assert not calls and not offline_playwright.events
    err = capsys.readouterr().err
    assert "preflight temp: failed" in err
    assert str(live_check.REPO_ROOT / ".tmp" / "live") in err
    assert ("New-Item" if sys.platform == "win32" else "mkdir -p") in err


@pytest.mark.req("R-LIVE-PREFLIGHT.driver")
def test_failed_driver_uses_system_node_before_tool(preflight_call, monkeypatch, offline_playwright, capsys, tmp_path):
    """R-LIVE-PREFLIGHT.driver: retry the real preflight with PATH's system node."""
    argv, _ledger, calls = preflight_call
    node = str(tmp_path / "system node.exe")
    monkeypatch.setattr("shutil.which", lambda name: node if name == "node" else None)
    offline_playwright.start_errors.append(OSError("bundled node failed"))
    assert live_check.main(argv) == 0
    assert calls[0]["PLAYWRIGHT_NODEJS_PATH"] == node
    assert offline_playwright.environments[1]["PLAYWRIGHT_NODEJS_PATH"] == node
    assert offline_playwright.events.index("cdp.disconnect") < offline_playwright.events.index("tool")
    assert "preflight driver: fixed" in capsys.readouterr().err


@pytest.mark.req("R-LIVE-PREFLIGHT.driver")
def test_failed_system_driver_stops_before_budget(preflight_call, monkeypatch, offline_playwright, capsys, tmp_path):
    """R-LIVE-PREFLIGHT.driver: a failed retry is setup failure, not a spent request."""
    argv, ledger, calls = preflight_call
    before = ledger.read_bytes()
    node = str(tmp_path / "node.exe")
    monkeypatch.setattr("shutil.which", lambda name: node)
    offline_playwright.start_errors.extend([OSError("bundled failed"), OSError("system failed")])
    assert live_check.main(argv) == 4
    assert ledger.read_bytes() == before
    assert live_budget.installed_budget() is None and not calls
    err = capsys.readouterr().err
    assert "preflight driver: failed" in err
    assert "PLAYWRIGHT_NODEJS_PATH" in err and node in err


@pytest.mark.req("R-LIVE-PREFLIGHT.cdp")
@pytest.mark.parametrize("mode", ["call", "doctor"])
def test_unreachable_cdp_stops_before_budget(mode, preflight_call, offline_playwright, capsys):
    """R-LIVE-PREFLIGHT.cdp: attach only, no autostart or connector/budget code."""
    from mcp_core.transport import chrome_cdp

    argv, ledger, calls = preflight_call
    before = ledger.read_bytes()
    offline_playwright.connect_errors.append(ConnectionRefusedError("CDP unreachable"))
    assert live_check.main(argv if mode == "call" else ["doctor"]) == 4
    assert offline_playwright.endpoints == [(chrome_cdp.CDP_URL, 10_000)]
    assert offline_playwright.events[-1] == "driver.stop"
    assert ledger.read_bytes() == before
    assert live_budget.installed_budget() is None and not calls
    err = capsys.readouterr().err
    assert "preflight cdp: failed" in err
    assert "marketplace-mcp browser start" in err


ARTIFACT_EPERM = "EPERM: operation not permitted, mkdtemp 'C:\\Temp\\playwright-artifacts-XXXXXX'"


@pytest.mark.req("R-LIVE-PREFLIGHT.cdp")
def test_cdp_artifact_failure_moves_temp_into_the_repo_first(preflight_call, offline_playwright, capsys):
    """R-LIVE-PREFLIGHT.cdp: a writable-for-Python %TEMP% that node may not use
    (seen live 2026-11-15) is moved into the repo before any tool code runs."""
    argv, _ledger, calls = preflight_call
    offline_playwright.connect_errors.append(OSError(ARTIFACT_EPERM))
    assert live_check.main(argv) == 0
    fallback = str(live_check.REPO_ROOT / ".tmp" / "live")
    assert calls[0]["TEMP"] == calls[0]["TMP"] == fallback
    assert "PLAYWRIGHT_NODEJS_PATH" not in calls[0]
    assert len(offline_playwright.endpoints) == 2
    assert offline_playwright.events.index("cdp.disconnect") < offline_playwright.events.index("tool")
    assert "preflight cdp: fixed; TEMP=TMP=TMPDIR=" in capsys.readouterr().err


@pytest.mark.req("R-LIVE-PREFLIGHT.cdp")
def test_cdp_artifact_failure_then_tries_system_node(preflight_call, monkeypatch, offline_playwright, capsys, tmp_path):
    """R-LIVE-PREFLIGHT.cdp: when the repo temp alone does not help, system node is next."""
    argv, _ledger, calls = preflight_call
    node = str(tmp_path / "node.exe")
    monkeypatch.setattr("shutil.which", lambda name: node)
    offline_playwright.connect_errors += [OSError(ARTIFACT_EPERM), OSError(ARTIFACT_EPERM)]
    assert live_check.main(argv) == 0
    assert calls[0]["PLAYWRIGHT_NODEJS_PATH"] == node
    assert calls[0]["TEMP"] == str(live_check.REPO_ROOT / ".tmp" / "live")
    assert len(offline_playwright.endpoints) == 3
    assert "preflight cdp: fixed" in capsys.readouterr().err


@pytest.mark.req("R-LIVE-PREFLIGHT.cdp")
def test_cdp_artifact_failure_after_both_fixes_stops_before_budget(
    preflight_call, monkeypatch, offline_playwright, capsys, tmp_path
):
    """R-LIVE-PREFLIGHT.cdp: nothing left to try → both commands printed, ledger untouched."""
    argv, ledger, calls = preflight_call
    before = ledger.read_bytes()
    monkeypatch.setattr("shutil.which", lambda name: str(tmp_path / "node.exe"))
    offline_playwright.connect_errors += [OSError(ARTIFACT_EPERM)] * 3
    assert live_check.main(argv) == 4
    assert ledger.read_bytes() == before and not calls
    err = capsys.readouterr().err
    assert "PLAYWRIGHT_NODEJS_PATH" in err and ("New-Item" if sys.platform == "win32" else "mkdir -p") in err


# END_BLOCK_PREFLIGHT_TESTS
