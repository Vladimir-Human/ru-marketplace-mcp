"""Disposable Chrome lifecycle with a fail-before-spawn isolation guard.

START_MODULE_CONTRACT
PURPOSE: Launch and identify only a fresh-profile, free-port Chrome mapped entirely to the fixture listener.
SCOPE: Test process settings, launch argument checks, bounded raw-CDP identity probe and exact spawned-tree cleanup.
DEPENDS: mcp_core.transport.chrome_cdp, mcp_core.process; pytest; stdlib asyncio,json,pathlib,socket,subprocess.
LINKS: M-CDP-SIM, M-CHROME-CDP, R-CDP-SIM-INFRA.isolation, R-CDP-SIM-INFRA.flags.
END_MODULE_CONTRACT
START_MODULE_MAP
  free_port: Reserve then release a loopback port, never 9222.
  simulator_flags: Map every hostname to the fixture server and turn off background traffic/proxies.
  check_launch: Refuse missing/overridden isolation switches before Popen or browser navigation.
  SimulatorChrome: Identify the real browser over CDP and reap only the process tree returned by its launch.
  assert_targets_settle: Poll the page targets until they match the scenario's start; fail after 12 seconds.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from pathlib import Path

from mcp_core.process import terminate_process_tree
from mcp_core.transport import chrome_cdp

from .fixture_server import FixtureServer

# START_BLOCK_LAUNCH_GUARDS


def free_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    assert port != 9222, "the simulator must never use the operator's CDP port"
    return port


def simulator_flags(server_port: int) -> tuple[str, ...]:
    return (
        f"--host-resolver-rules=MAP * 127.0.0.1:{server_port}",
        "--ignore-certificate-errors",
        "--enable-automation",  # Enables Browser.getBrowserCommandLine; no UA/fingerprint override.
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-pings",
        "--no-proxy-server",
        "--disable-quic",
    )


# START_CONTRACT: check_launch
# PURPOSE: Fail closed before process spawn or navigation, including under Python -O.
# INPUTS: Actual Chrome argv and the fresh session's exact profile/CDP/HTTPS endpoints.
# OUTPUTS: None for isolated arguments; AssertionError for missing or conflicting isolation switches.
# SIDE_EFFECTS: None; never starts Chrome or opens a socket.
# END_CONTRACT: check_launch
def check_launch(args: list[str], *, profile: Path, port: int, server_port: int) -> None:
    """Check actual Popen/CDP arguments, not merely the intended extra_flags tuple."""
    # These are launch safety checks, not optimizable test assertions.
    if port == 9222 or not profile.is_absolute():
        raise AssertionError("unsafe simulator endpoint/profile")
    switches = {
        "--remote-debugging-port=": str(port),
        "--remote-debugging-address=": "127.0.0.1",
        "--user-data-dir=": str(profile),
        "--host-resolver-rules=": f"MAP * 127.0.0.1:{server_port}",
    }
    for prefix, value in switches.items():
        if [arg for arg in args if arg.startswith(prefix)] != [prefix + value]:
            raise AssertionError(f"unsafe simulator launch: {prefix}")
    for flag in (*simulator_flags(server_port)[1:], "--headless=new"):
        if flag not in args:
            raise AssertionError(f"missing simulator isolation flag: {flag}")
    if any(arg.startswith(("http://", "https://")) for arg in args):
        raise AssertionError("Chrome must start on about:blank")


# END_BLOCK_LAUNCH_GUARDS
# START_BLOCK_CHROME_LIFECYCLE


class SimulatorChrome:
    def __init__(self, server: FixtureServer, profile: Path, port: int) -> None:
        self.server = server
        self.profile = profile.resolve()
        self.port = port
        self.process: subprocess.Popen | None = None
        if self.port == 9222 or self.profile.exists():
            raise AssertionError("simulator needs a new profile and a non-9222 port")

    def spawn(self, args: list[str], **kwargs: object) -> subprocess.Popen:
        check_launch(args, profile=self.profile, port=self.port, server_port=self.server.port)
        if self.process is not None:
            raise AssertionError("a simulator session launches exactly one Chrome")
        self.process = subprocess.Popen(args, **kwargs)
        return self.process

    async def verify_identity(self) -> None:
        assert self.process is not None and self.process.poll() is None, "simulator Chrome exited before CDP attached"
        assert chrome_cdp.CDP_PORT == self.port != 9222
        assert f"http://127.0.0.1:{self.port}" == chrome_cdp.CDP_URL
        browser_ws = await asyncio.to_thread(chrome_cdp._browser_ws_url)
        assert browser_ws, "simulator CDP returned no browser websocket"
        # A listener on the chosen port is not sufficient proof of ownership.
        # Verify Chrome's own argv before any marketplace-shaped navigation.
        async with asyncio.timeout(5):
            async with chrome_cdp._websockets.connect(browser_ws, open_timeout=3, proxy=None) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Browser.getBrowserCommandLine"}))
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("id") == 1:
                        assert "error" not in message, "Chrome could not report its simulator command line"
                        args = message["result"]["arguments"]
                        check_launch(args, profile=self.profile, port=self.port, server_port=self.server.port)
                        break

    async def targets(self) -> frozenset[str]:
        targets = await asyncio.to_thread(chrome_cdp._cdp_http_json, "/json/list", timeout_s=3)
        return frozenset(target["id"] for target in chrome_cdp._page_targets(targets))

    def close(self) -> None:
        if self.process is not None:
            check_launch(self.process.args, profile=self.profile, port=self.port, server_port=self.server.port)
            # The production launcher creates its own POSIX session / Windows
            # process group. Reuse the project's exact-PID tree reaper, never a
            # chrome.exe-name kill or a profile scan of the operator's browser.
            terminate_process_tree(self.process)
            self.process.wait(timeout=10)
            assert not chrome_cdp._cdp_port_open(), "simulator Chrome still owns its CDP port after cleanup"


async def assert_targets_settle(chrome, before: frozenset[str], within_s: float = 12.0) -> None:
    """The page targets return to ``before``: no scenario leaks or closes a tab.

    Target.closeTarget answers before /json/list drops the tab, so the list is polled.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within_s
    while (after := await chrome.targets()) != before and loop.time() < deadline:
        await asyncio.sleep(0.2)
    assert after == before, "scenario leaked or closed a browser target"


# END_BLOCK_CHROME_LIFECYCLE
