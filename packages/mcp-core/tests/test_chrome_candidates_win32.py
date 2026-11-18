"""Regression tests for Windows 11 Chrome and x86 Edge binary candidate ordering."""

from __future__ import annotations

import pytest
from mcp_core.transport import chrome_cdp


@pytest.mark.req("R-CHROME-LAUNCHER")
def test_windows_candidates_rank_localappdata_chrome_before_edge_and_include_x86_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHROME_BINARY", raising=False)
    monkeypatch.setattr(chrome_cdp.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\LocalAppData")

    candidates = chrome_cdp._chrome_candidates()

    user_chrome = r"C:\LocalAppData\Google\Chrome\Application\chrome.exe"
    pf_edge = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    x86_edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

    assert user_chrome in candidates
    assert pf_edge in candidates
    assert x86_edge in candidates
    assert candidates.index(user_chrome) < candidates.index(pf_edge)
    assert candidates.index(user_chrome) < candidates.index(x86_edge)
