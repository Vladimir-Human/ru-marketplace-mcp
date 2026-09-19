"""The release probe must reject a stale or incomplete server, not just count tools."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import e2e_stdio_check as probe


def source_payload():
    return {
        "mounted": sorted(probe.EXPECTED_MOUNTS),
        "mounted_count": len(probe.EXPECTED_MOUNTS),
        "skipped": {},
        "skipped_count": 0,
        "server_version": probe.EXPECTED_VERSION,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"mounted": []},
        {"mounted_count": 0},
        {"mounted": [*sorted(probe.EXPECTED_MOUNTS), "wildberries"]},
        {"skipped": {"ozon": "import failed"}},
        {"skipped_count": 1},
        {"server_version": "0.0.0"},
    ],
)
def test_sources_reject_incomplete_or_stale_state(change):
    assert probe.validate_sources(source_payload() | change) is not None


@pytest.mark.parametrize("payload", [None, {}, [], {"mounted": [None]}])
def test_sources_reject_missing_or_malformed_payload(payload):
    assert probe.validate_sources(payload) is not None


def test_sources_accept_complete_current_state():
    assert probe.validate_sources(source_payload()) is None


@pytest.mark.parametrize("version", ["0.0.0", probe.EXPECTED_VERSION])
async def test_probe_checks_running_version(monkeypatch, version):
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
            return SimpleNamespace(serverInfo=SimpleNamespace(name="wb", version=version))

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=f"tool_{i}") for i in range(8)])

    monkeypatch.setattr(probe.shutil, "which", lambda _script: "fake-mcp")
    monkeypatch.setattr(probe, "stdio_client", stdio)
    monkeypatch.setattr(probe, "ClientSession", Session)
    _, ok, detail = await probe.probe("wb-mcp", 8)
    assert ok is (version == probe.EXPECTED_VERSION)
    if not ok:
        assert "expected version" in detail
