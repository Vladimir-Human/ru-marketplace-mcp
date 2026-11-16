"""The release probe must reject a stale or incomplete server, not just count tools."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import METHOD_NOT_FOUND

sys.path.insert(0, str(Path(__file__).parent))
import e2e_stdio_check as probe


def source_payload():
    skipped = {"mpstats": "auth_missing: MPSTATS_MP_AUTH not set"}
    return {
        "mounted": sorted(probe.EXPECTED_MOUNTS),
        "mounted_count": len(probe.EXPECTED_MOUNTS),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "server_version": probe.EXPECTED_VERSION,
    }


@pytest.mark.req("R-STDIO-PROBE.version")
@pytest.mark.parametrize(
    "change",
    [
        {"mounted": []},
        {"mounted_count": 0},
        {"mounted": [*sorted(probe.EXPECTED_MOUNTS), "wildberries"]},
        {"skipped": {"ozon": "import failed"}},
        {"skipped": {"mpstats": "ModuleNotFoundError: No module named 'mpstats_connector'"}},
        {"skipped_count": 2},
        {"server_version": "0.0.0"},
    ],
)
def test_sources_reject_incomplete_or_stale_state(change):
    assert probe.validate_sources(source_payload() | change) is not None


@pytest.mark.req("R-STDIO-PROBE.version")
@pytest.mark.parametrize("payload", [None, {}, [], {"mounted": [None]}])
def test_sources_reject_missing_or_malformed_payload(payload):
    assert probe.validate_sources(payload) is not None


@pytest.mark.req("R-STDIO-PROBE.version")
def test_sources_accept_complete_current_state():
    assert probe.validate_sources(source_payload()) is None


@pytest.mark.req("R-STDIO-PROBE.version", "R-STDIO-PROBE.eras")
@pytest.mark.parametrize("era", probe.ERAS)
@pytest.mark.parametrize("version", ["0.0.0", probe.EXPECTED_VERSION])
async def test_probe_checks_running_version(monkeypatch, version, era):
    @asynccontextmanager
    async def stdio(_params):
        yield None, None

    class Session:
        def __init__(self, *_streams):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            pass

        async def initialize(self):
            self.opened = "handshake"

        async def discover(self):
            self.opened = "sessionless"

        @property
        def protocol_version(self):
            return {"handshake": "2025-11-25", "sessionless": "2026-07-28"}[self.opened]

        @property
        def server_info(self):
            assert self.opened == era  # the probe must open the era it reports
            return SimpleNamespace(name="wb", version=version)

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=f"tool_{i}") for i in range(8)])

    monkeypatch.setattr(probe.shutil, "which", lambda _script: "fake-mcp")
    monkeypatch.setattr(probe, "stdio_client", stdio)
    monkeypatch.setattr(probe, "ClientSession", Session)
    _, ok, detail = await probe.probe("wb-mcp", 8, era)
    assert ok is (version == probe.EXPECTED_VERSION)
    if not ok:
        assert "expected version" in detail
    else:
        assert detail.startswith(era)


@pytest.mark.req("R-STDIO-PROBE.eras")
async def test_probe_fails_a_server_that_does_not_identify_itself(monkeypatch):
    @asynccontextmanager
    async def stdio(_params):
        yield None, None

    class Session:
        server_info = None

        def __init__(self, *_streams):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            pass

        async def discover(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=f"tool_{i}") for i in range(8)])

    monkeypatch.setattr(probe.shutil, "which", lambda _script: "fake-mcp")
    monkeypatch.setattr(probe, "stdio_client", stdio)
    monkeypatch.setattr(probe, "ClientSession", Session)
    _, ok, detail = await probe.probe("wb-mcp", 8, "sessionless")
    assert not ok and "did not identify itself" in detail


@pytest.mark.req("R-STDIO-PROBE.eras")
@pytest.mark.parametrize("broken", ["error", "hang"])
async def test_probe_fails_a_broken_discover(monkeypatch, broken):
    """A server whose ``server/discover`` errors or never answers fails the sessionless probe."""

    @asynccontextmanager
    async def stdio(_params):
        yield None, None

    class Session:
        server_info = SimpleNamespace(name="wb", version=probe.EXPECTED_VERSION)
        protocol_version = "2026-07-28"

        def __init__(self, *_streams):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            pass

        async def discover(self):
            if broken == "error":
                raise MCPError(METHOD_NOT_FOUND, "Method not found")
            await asyncio.sleep(1)

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=f"tool_{i}") for i in range(8)])

    monkeypatch.setattr(probe.shutil, "which", lambda _script: "fake-mcp")
    monkeypatch.setattr(probe, "stdio_client", stdio)
    monkeypatch.setattr(probe, "ClientSession", Session)
    monkeypatch.setattr(probe, "TIMEOUT_S", 0.01)
    _, ok, detail = await probe.probe("wb-mcp", 8, "sessionless")
    assert not ok
    assert ("Method not found" in detail) if broken == "error" else detail.startswith("timed out")
