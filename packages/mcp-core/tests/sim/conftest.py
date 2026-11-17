"""Real-CDP fixtures with no access to the operator's browser or the internet.

START_MODULE_CONTRACT
PURPOSE: Own one disposable Chrome/HTTPS session and keep each scenario's state isolated.
SCOPE: Existing process settings, real launch/identity/cleanup, transport selection and anonymous-tier refusals.
DEPENDS: pytest; mcp_core cache,pacing,sources,transport; sibling chrome,fixture_server; stdlib.
LINKS: M-CDP-SIM, R-CDP-SIM-INFRA, R-CDP-SIM-S1, R-CDP-SIM-S2, R-CDP-SIM-S4, R-CDP-SIM-S9.
END_MODULE_CONTRACT
START_MODULE_MAP
  simulator_chrome: Start only fresh-profile headless Chrome on a free port and delete its temporary profile on exit.
  cdp_sim: Exercise Playwright and raw CDP separately, reset the real budget and assert owned-tab cleanup.
  connector: Load a registered source, isolate its real cache/pacer/lock, and refuse its anonymous HTTP tier offline.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp_core.cache import TTLCache
from mcp_core.pacing import Pacer
from mcp_core.sources import BY_NAME
from mcp_core.transport import cdp_budget, chrome_cdp

from .chrome import SimulatorChrome, assert_targets_settle, free_port, simulator_flags
from .fixture_server import FixtureServer

# START_BLOCK_SESSION_OWNERSHIP


@pytest.fixture(scope="session")
def simulator_chrome(tmp_path_factory):
    base = tmp_path_factory.mktemp("cdp-sim").resolve()
    with tempfile.TemporaryDirectory(prefix="session-", dir=base) as directory:
        root = Path(directory).resolve()
        assert root.parent == base  # This newly created subtree is the only cleanup target.
        with FixtureServer() as server, pytest.MonkeyPatch.context() as patch:
            chrome = SimulatorChrome(server, root / "profile", free_port())
            assert chrome.port != server.port
            for name, value in {
                "CDP_HOST": "127.0.0.1",
                "CDP_PORT": chrome.port,
                "CDP_URL": f"http://127.0.0.1:{chrome.port}",
                "CDP_IS_LOOPBACK": True,
                "SCRAPING_PROFILE": str(chrome.profile),
                "HEADLESS": True,
                "STEALTH": False,
            }.items():
                patch.setattr(chrome_cdp, name, value)
            # The pre-#233 red-proof module did not keep this diagnostic state.
            patch.setattr(chrome_cdp, "_last_spawn", None, raising=False)
            for name, value in {
                "CHROME_CDP_HOST": "127.0.0.1",
                "CHROME_CDP_PORT": str(chrome.port),
                "CHROME_SCRAPING_PROFILE": str(chrome.profile),
                "CHROME_HEADLESS": "1",
                "CHROME_STEALTH": "0",
                "CHROME_CHALLENGE_HANDOFF_S": "0",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "TEMP": str(root),
                "TMP": str(root),
                "TMPDIR": str(root),
            }.items():
                patch.setenv(name, value)
            patch.delenv("CHROME_NAV_TIMEOUT_MS", raising=False)

            async def no_autostart(*args, **kwargs):
                pytest.fail("simulator Chrome is down; autostart without the isolation flags is forbidden")

            patch.setattr(chrome_cdp, "_ensure_cdp_running", no_autostart)
            # Only the Chrome launch is wrapped. Playwright's real Node subprocess
            # and the project's process-tree reaper keep their original subprocess module.
            patch.setattr(chrome_cdp, "subprocess", SimpleNamespace(Popen=chrome.spawn, DEVNULL=subprocess.DEVNULL))
            try:
                ok, detail = chrome_cdp._start_chrome_with_cdp(stealth=False, extra_flags=simulator_flags(server.port))
                assert ok, f"isolated simulator Chrome could not start: {detail}"
                assert asyncio.run(chrome_cdp._wait_for_cdp_port(15)), "isolated simulator Chrome did not bind CDP"
                asyncio.run(chrome.verify_identity())
                yield chrome
            finally:
                chrome.close()
    assert not root.exists(), "simulator session/profile was not deleted"


# END_BLOCK_SESSION_OWNERSHIP
# START_BLOCK_SCENARIO_ISOLATION


@pytest.fixture(params=["playwright", "raw"])
async def cdp_sim(simulator_chrome, monkeypatch, request):
    chrome = simulator_chrome
    chrome.server.reset()
    monkeypatch.setattr(cdp_budget, "_BUILT", cdp_budget.NavigationBudget())
    before = await chrome.targets()
    assert before, "the simulator's about:blank anchor must survive between calls"
    if request.param == "raw":

        @asynccontextmanager
        async def select_raw():
            # Select the real fallback without waiting 10 seconds for an artificial
            # attach timeout. No browser, socket, response, navigation or clock is faked.
            raise chrome_cdp._CdpConnectTimeout("raw CDP selected by the simulator test")
            yield

        monkeypatch.setattr(chrome_cdp, "get_context", select_raw)
    else:

        def no_raw(*args, **kwargs):
            pytest.fail("Playwright attach failed; raw CDP is tested separately, not accepted as Playwright evidence")

        monkeypatch.setattr(chrome_cdp, "_raw_cdp_page", no_raw)
    chrome.before, chrome.mode = before, request.param
    chrome.targets_checked_by_test = False
    try:
        yield chrome
    finally:
        if not chrome.targets_checked_by_test:
            await assert_targets_settle(chrome, before)
        assert cdp_budget.navigation_budget().snapshot()["in_flight"] == 0
        assert all(entry.peer == "127.0.0.1" for entry in chrome.server.requests)
        chrome.server.reset()


@pytest.fixture
def connector(monkeypatch):
    def load(source):
        module = importlib.import_module(BY_NAME[source].module)
        monkeypatch.setattr(module, "_cache", TTLCache(ttl_s=0))
        monkeypatch.setattr(module, "_pacer", Pacer(module._min_gap))
        monkeypatch.setattr(module, "_cdp_lock", asyncio.Lock())
        anonymous_calls = []
        if source in {"ozon", "avito"}:

            def refused_http(url, proxy=None):
                anonymous_calls.append(url)
                return 403, "offline anonymous-tier refusal"

            monkeypatch.setattr(module, "_sync_curl_get", refused_http)
            if source == "ozon":
                # An inherited compatibility opt-in must not spoof identity in
                # this simulator; the original setting is restored after the case.
                monkeypatch.setattr(module._settings, "chrome_compat", "native")

                async def refused_worker(func, url, proxy, *, timeout_s):
                    assert func is refused_http
                    return refused_http(url, proxy)

                # An actual curl worker does not use Chrome's DNS map. Refuse it
                # in this process, then let the unmodified CDP fetch/parser/tool run.
                monkeypatch.setattr(module, "_run_sync_bounded", refused_worker)
        return SimpleNamespace(module=module, anonymous_calls=anonymous_calls)

    return load


# END_BLOCK_SCENARIO_ISOLATION
