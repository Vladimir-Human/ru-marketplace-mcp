"""Offline Playwright edges for live_check entry tests; never contact Chrome.

START_MODULE_CONTRACT
PURPOSE: Supply controllable driver/CDP lifecycle doubles without replacing preflight logic.
SCOPE: Opt-in pytest fixture for live_check and doctor entry tests.
DEPENDS: pytest; Playwright async API; stdlib os,tempfile,types; restore process environment/cache after each test.
LINKS: M-LIVE-CHECK, R-LIVE-PREFLIGHT, R-DOCTOR-EXIT-CODES.
START_MODULE_MAP
  OfflinePlaywright     lifecycle events, injected edge failures and environment snapshots
  offline_playwright    replace only the Playwright factory at its public edge
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

# START_BLOCK_PLAYWRIGHT_EDGES


class OfflinePlaywright:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.start_errors: list[Exception] = []
        self.connect_errors: list[Exception] = []
        self.environments: list[dict[str, str]] = []
        self.endpoints: list[tuple[str, float]] = []

    async def start(self) -> Any:
        self.events.append("driver.start")
        self.environments.append(dict(os.environ))
        if self.start_errors:
            raise self.start_errors.pop(0)
        return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=self.connect), stop=self.stop)

    async def stop(self) -> None:
        self.events.append("driver.stop")

    async def connect(self, endpoint: str, *, timeout: float) -> Any:
        self.events.append("cdp.connect")
        self.endpoints.append((endpoint, timeout))
        if self.connect_errors:
            raise self.connect_errors.pop(0)
        return SimpleNamespace(close=self.close)

    async def close(self) -> None:
        self.events.append("cdp.disconnect")


@pytest.fixture
def offline_playwright(monkeypatch: pytest.MonkeyPatch) -> OfflinePlaywright:
    for name in ("TEMP", "TMP", "TMPDIR", "PLAYWRIGHT_NODEJS_PATH"):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    edge = OfflinePlaywright()
    monkeypatch.setattr(
        "playwright.async_api.async_playwright", lambda: SimpleNamespace(start=edge.start, __aexit__=edge.stop)
    )
    return edge


# END_BLOCK_PLAYWRIGHT_EDGES
