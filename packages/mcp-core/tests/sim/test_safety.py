"""Fail-closed launch checks and the simulator server's offline contract.

START_MODULE_CONTRACT
PURPOSE: Prove that missing isolation flags never spawn Chrome and only registered committed fixture bytes are served.
SCOPE: Real HTTPS on loopback, hostile/unregistered routes and launch-argument mutations with a fail-only spawn spy.
DEPENDS: pytest; mcp_core.transport.chrome_cdp; sibling chrome,fixture_server; stdlib http.client,ssl,types,pathlib.
LINKS: M-CDP-SIM, R-CDP-SIM-INFRA.flags, R-CDP-SIM-INFRA.isolation, R-CDP-SIM-INFRA.fixtures.
END_MODULE_CONTRACT
START_MODULE_MAP
  get: Read the local HTTPS server without DNS resolution or a proxy.
  test_fixture_routes: Assert exact captured bytes, empty 404s off-route and loopback peers.
  test_reply_sequence: Assert that a scripted 401 Refresh becomes a sticky 200 without altering fixture bytes.
  test_signal_reply: A real held HTTPS reply needs its signal or cancels on reset, without changing fixture bytes.
  test_missing_launch_guard: Delete/override an isolation switch; the real launcher must fail before Popen.
  test_extra_flags_default: The empty-default keyword-only hook leaves normal launch arguments unchanged.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
import http.client
import inspect
import re
import ssl
import threading
import tomllib
from types import SimpleNamespace

import pytest
from mcp_core.transport import chrome_cdp

from .chrome import check_launch, simulator_flags
from .fixture_server import FIXTURES, ROOT, FixtureServer, Reply, fixture_bytes

pytestmark = [pytest.mark.cdp, pytest.mark.cdp_sim]

# START_BLOCK_SERVER_CONTRACT


def get(server, host, path):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Test certificate at 127.0.0.1 only.
    connection = http.client.HTTPSConnection("127.0.0.1", server.port, context=context, timeout=3)
    try:
        connection.request("GET", path, headers={"Host": host})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


@pytest.mark.req("R-CDP-SIM-INFRA.fixtures")
def test_fixture_routes():
    with FixtureServer() as server:
        assert server.address[0] == "127.0.0.1"
        server.route("www.dns-shop.ru", "/search/", Reply("dns_search"))
        status, headers, body = get(server, "www.dns-shop.ru", "/search/?q=fixture")
        assert status == 200 and headers["Content-Type"].startswith("text/html")
        assert body == fixture_bytes("dns_search")
        for host, path in [
            ("foreign.invalid", "/search/"),
            ("www.dns-shop.ru", "/unregistered"),
            ("www.dns-shop.ru", "/../pyproject.toml"),
        ]:
            status, _, body = get(server, host, path)
            assert status == 404 and body == b""
        assert all(entry.peer == "127.0.0.1" for entry in server.requests)
        for path, _ in FIXTURES.values():
            assert "/tests/fixtures/" in path and (ROOT / path).is_file()
        with pytest.raises(KeyError):
            server.route("www.dns-shop.ru", "/", Reply("../../pyproject.toml"))


@pytest.mark.req("R-CDP-SIM-INFRA.fixtures")
def test_reply_sequence():
    with FixtureServer() as server:
        server.route("www.dns-shop.ru", "/search/", Reply("dns_search", 401, (("Refresh", "1"),)), Reply("dns_search"))
        first, headers, body = get(server, "www.dns-shop.ru", "/search/")
        assert first == 401 and headers["Refresh"] == "1" and body == fixture_bytes("dns_search")
        for _ in range(2):
            status, _, body = get(server, "www.dns-shop.ru", "/search/")
            assert status == 200 and body == fixture_bytes("dns_search")
        assert [entry.status for entry in server.requests] == [401, 200, 200]


@pytest.mark.req("R-CDP-SIM-INFRA.fixtures")
@pytest.mark.parametrize("finish", ["signal", "reset"])
async def test_signal_reply(finish):
    """A held reply serves exact committed bytes only after signalling, or cancels on reset."""
    arrived, clear = threading.Event(), threading.Event()
    with FixtureServer() as server:
        server.route("www.dns-shop.ru", "/search/", Reply("dns_search", wait_for=clear, arrived=arrived))
        call = asyncio.create_task(asyncio.to_thread(get, server, "www.dns-shop.ru", "/search/"))
        try:
            async with asyncio.timeout(5):
                assert await asyncio.to_thread(arrived.wait, 2)
                assert not call.done(), "the server must not emit its success fixture before the signal"
                if finish == "signal":
                    clear.set()
                    status, _, body = await call
                    assert status == 200 and body == fixture_bytes("dns_search")
                else:
                    server.reset()
                    with pytest.raises(http.client.RemoteDisconnected):
                        await call
        finally:
            clear.set()
            await asyncio.gather(call, return_exceptions=True)


# END_BLOCK_SERVER_CONTRACT
# START_BLOCK_PRESPAWN_GUARDS


@pytest.mark.req("R-CDP-SIM-INFRA.flags", "R-CDP-SIM-INFRA.isolation")
@pytest.mark.parametrize("missing", ["mapping", "certificate", "profile", "port"])
def test_missing_launch_guard(monkeypatch, tmp_path, missing):
    profile = (tmp_path / "new-profile").resolve()
    port, server_port = 19321, 19322
    calls = []

    def checked_spawn(args, **kwargs):
        # Mutate the actual outgoing argument list, not an expected-value assertion.
        if missing == "mapping":
            args = [arg for arg in args if not arg.startswith("--host-resolver-rules=")]
        elif missing == "certificate":
            args = [arg for arg in args if arg != "--ignore-certificate-errors"]
        elif missing == "profile":
            args = [arg for arg in args if not arg.startswith("--user-data-dir=")]
        else:
            args = [arg for arg in args if not arg.startswith("--remote-debugging-port=")]
            args.append("--remote-debugging-port=9222")
        check_launch(args, profile=profile, port=port, server_port=server_port)
        calls.append(args)
        pytest.fail("unsafe arguments reached the process-spawn boundary")

    monkeypatch.setattr(chrome_cdp, "_find_chrome", lambda: "unused-test-chrome")
    monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", str(profile))
    monkeypatch.setattr(chrome_cdp, "CDP_PORT", port)
    monkeypatch.setattr(chrome_cdp, "HEADLESS", True)
    monkeypatch.setattr(chrome_cdp, "_last_spawn", None)
    monkeypatch.setattr(chrome_cdp, "subprocess", SimpleNamespace(Popen=checked_spawn, DEVNULL=-3))
    ok, detail = chrome_cdp._start_chrome_with_cdp(stealth=False, extra_flags=simulator_flags(server_port))
    assert not ok and ("unsafe simulator launch" in detail or "missing simulator isolation flag" in detail)
    assert calls == [] and chrome_cdp._last_spawn is None


@pytest.mark.req("R-CDP-SIM-INFRA.flags")
def test_extra_flags_default(monkeypatch, tmp_path):
    parameter = inspect.signature(chrome_cdp._start_chrome_with_cdp).parameters["extra_flags"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default == ()
    calls = []
    monkeypatch.setattr(chrome_cdp, "_find_chrome", lambda: "unused-test-chrome")
    monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", str(tmp_path))
    monkeypatch.setattr(chrome_cdp, "CDP_PORT", 19321)
    monkeypatch.setattr(chrome_cdp, "HEADLESS", False)
    monkeypatch.setattr(chrome_cdp, "_last_spawn", None)
    monkeypatch.setattr(
        chrome_cdp, "subprocess", SimpleNamespace(Popen=lambda args, **kwargs: calls.append(args), DEVNULL=-3)
    )
    ok, _ = chrome_cdp._start_chrome_with_cdp(stealth=False)
    assert ok and len(calls) == 1
    assert calls[0][-1] == "about:blank"
    assert not any(arg.startswith("--host-resolver-rules=") for arg in calls[0])
    assert "--ignore-certificate-errors" not in calls[0]


# END_BLOCK_PRESPAWN_GUARDS
# START_BLOCK_TEST_CONFIGURATION


@pytest.mark.req("R-CDP-SIM-INFRA.ci")
def test_marker_and_ci_configuration():
    from . import test_scenarios, test_scenarios_more

    for marks in (pytestmark, test_scenarios.pytestmark, test_scenarios_more.pytestmark):
        assert {mark.name for mark in marks} == {"cdp", "cdp_sim"}
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = [marker.split(":", 1)[0] for marker in config["tool"]["pytest"]["ini_options"]["markers"]]
    assert markers[markers.index("cdp") + 1] == "cdp_sim"
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert workflow.count("\n  cdp-sim:\n") == 1
    job = workflow.split("\n  cdp-sim:\n")[1]
    assert not re.search(r"^  [a-z][a-z0-9-]*:", job, re.MULTILINE), "simulator job must be last"
    assert "continue-on-error: true" in job
    assert "timeout-minutes: 20" in job
    assert "--with-deps chrome" not in job
    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in job
    assert "uv run pytest -q packages/mcp-core/tests/sim -m cdp_sim" in job


# END_BLOCK_TEST_CONFIGURATION
