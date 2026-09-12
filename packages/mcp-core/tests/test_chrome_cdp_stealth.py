"""Stealth on macOS: find the scraping Chrome, hide it, open tabs in the background.

Everything here runs offline. Platform branches are driven by patching
``sys.platform`` and ``subprocess.run``; the Playwright side is a duck-typed
fake, because the contract is only "which CDP command was sent and what came
back", never a real browser.
"""

from __future__ import annotations

import subprocess

import pytest
from mcp_core.transport import chrome_cdp

PROFILE = "/Users/op/Library/Application Support/Chrome-Scraping"

# What `ps -axo pid=,command=` prints on a Mac with the operator's daily Chrome,
# the scraping Chrome, one of its helpers, and a sibling profile with a longer
# name that a naive substring match would swallow.
_PS_OUTPUT = f"""\
  100 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --restore-last-session
  200 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222 --user-data-dir={PROFILE} --no-first-run about:blank
  201 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) --type=renderer --user-data-dir={PROFILE} --remote-debugging-port=9222
  300 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9333 --user-data-dir={PROFILE}-2 --no-first-run
"""


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# --------------------------------------------------------- profile PIDs ----


def test_macos_profile_pids_pick_the_main_process_of_our_profile_only(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")
    monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", PROFILE)
    monkeypatch.setattr(chrome_cdp.subprocess, "run", lambda *a, **k: _completed(_PS_OUTPUT))

    assert chrome_cdp._scraping_profile_pids() == {200}


def test_macos_profile_pids_empty_when_ps_fails(monkeypatch):
    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")
    monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", PROFILE)
    monkeypatch.setattr(chrome_cdp.subprocess, "run", lambda *a, **k: _completed("", returncode=1))

    assert chrome_cdp._scraping_profile_pids() == set()


def test_macos_profile_pids_empty_when_ps_raises(monkeypatch):
    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="ps", timeout=5)

    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")
    monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", PROFILE)
    monkeypatch.setattr(chrome_cdp.subprocess, "run", boom)

    assert chrome_cdp._scraping_profile_pids() == set()


def test_linux_profile_pids_never_shell_out(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("subprocess.run must not be called on linux")

    monkeypatch.setattr(chrome_cdp.sys, "platform", "linux")
    monkeypatch.setattr(chrome_cdp.subprocess, "run", forbidden)

    assert chrome_cdp._scraping_profile_pids() == set()


# --------------------------------------------------------- hiding the app ----


def test_macos_hide_sends_one_osascript_per_profile_pid(monkeypatch):
    calls: list[list[str]] = []

    def record(cmd, *_a, **_k):
        calls.append(cmd)
        return _completed()

    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")
    monkeypatch.setattr(chrome_cdp, "_scraping_profile_pids", lambda: {200})
    monkeypatch.setattr(chrome_cdp.subprocess, "run", record)

    chrome_cdp._hide_chrome_windows()

    assert len(calls) == 1
    assert calls[0][0] == "osascript"
    script = calls[0][-1]
    assert "unix id is 200" in script
    assert "set visible of" in script and "to false" in script


def test_macos_hide_does_nothing_without_a_confirmed_pid(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("no PID means no osascript")

    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")
    monkeypatch.setattr(chrome_cdp, "_scraping_profile_pids", set)
    monkeypatch.setattr(chrome_cdp.subprocess, "run", forbidden)

    chrome_cdp._hide_chrome_windows()


def test_macos_hide_survives_a_failing_osascript(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("osascript missing")

    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")
    monkeypatch.setattr(chrome_cdp, "_scraping_profile_pids", lambda: {200, 201})
    monkeypatch.setattr(chrome_cdp.subprocess, "run", boom)

    chrome_cdp._hide_chrome_windows()


def test_linux_hide_is_a_noop(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("nothing to hide on linux")

    monkeypatch.setattr(chrome_cdp.sys, "platform", "linux")
    monkeypatch.setattr(chrome_cdp, "_scraping_profile_pids", forbidden)
    monkeypatch.setattr(chrome_cdp.subprocess, "run", forbidden)

    chrome_cdp._hide_chrome_windows()


# ------------------------------------------------ background tab creation ----


class _FakeCdp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object] | None]] = []
        self.detached = False

    async def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.sent.append((method, params))
        return {"targetId": "T1"}

    async def detach(self) -> None:
        self.detached = True


class _FakePageCdp:
    def __init__(self, target_id: str) -> None:
        self.target_id = target_id

    async def send(self, method: str, params=None):
        assert method == "Target.getTargetInfo"
        return {"targetInfo": {"targetId": self.target_id}}

    async def detach(self) -> None:
        pass


class _FakeBrowser:
    def __init__(self, cdp: _FakeCdp | None) -> None:
        self._cdp = cdp

    async def new_browser_cdp_session(self) -> _FakeCdp:
        if self._cdp is None:
            raise RuntimeError("no browser-level CDP session here")
        return self._cdp


class _FakeEventInfo:
    def __init__(self, page: str) -> None:
        self._page = page

    @property
    def value(self):
        async def _resolve() -> str:
            return self._page

        return _resolve()


class _FakeContext:
    def __init__(self, browser: _FakeBrowser | None) -> None:
        self.browser = browser
        self.plain_pages = 0
        self.expect_timeouts: list[float | None] = []
        self.pages = ["background-page"]

    async def new_cdp_session(self, page: str) -> _FakePageCdp:
        return _FakePageCdp("T1")

    async def new_page(self) -> str:
        self.plain_pages += 1
        return "foreground-page"


async def test_new_tab_creates_the_target_in_the_background_when_stealth_is_on(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    cdp = _FakeCdp()
    ctx = _FakeContext(_FakeBrowser(cdp))

    page = await chrome_cdp._new_tab(ctx)  # type: ignore[arg-type]

    assert page == "background-page"
    assert cdp.sent == [("Target.createTarget", {"url": "about:blank", "background": True})]
    assert cdp.detached is True
    assert ctx.plain_pages == 0
    assert ctx.expect_timeouts == []


async def test_new_tab_uses_plain_new_page_when_stealth_is_off(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    cdp = _FakeCdp()
    ctx = _FakeContext(_FakeBrowser(cdp))

    page = await chrome_cdp._new_tab(ctx)  # type: ignore[arg-type]

    assert page == "foreground-page"
    assert cdp.sent == []
    assert ctx.plain_pages == 1


async def test_new_tab_falls_back_to_new_page_without_a_browser(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    ctx = _FakeContext(None)

    assert await chrome_cdp._new_tab(ctx) == "foreground-page"  # type: ignore[arg-type]
    assert ctx.plain_pages == 1


async def test_new_tab_falls_back_to_new_page_when_the_cdp_session_fails(monkeypatch):
    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    ctx = _FakeContext(_FakeBrowser(None))

    assert await chrome_cdp._new_tab(ctx) == "foreground-page"  # type: ignore[arg-type]
    assert ctx.plain_pages == 1


async def test_new_tab_detaches_the_session_even_when_no_page_arrives(monkeypatch):
    class _NoPageContext(_FakeContext):
        def __init__(self, browser):
            super().__init__(browser)
            self.pages = []

    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    cdp = _FakeCdp()
    ctx = _NoPageContext(_FakeBrowser(cdp))

    with pytest.raises(TimeoutError):
        await chrome_cdp._new_tab(ctx)  # type: ignore[arg-type]

    assert cdp.detached is True


async def test_new_tab_matches_distinct_targets_under_interleaved_creation(monkeypatch):
    """Broadcast pages arrive out of order while all six callers are pending."""
    import asyncio

    created = []
    all_created = asyncio.Event()

    class Cdp(_FakeCdp):
        def __init__(self, target_id):
            super().__init__()
            self.target_id = target_id

        async def send(self, method, params=None):
            self.sent.append((method, params))
            assert method == "Target.createTarget"
            created.append(self.target_id)
            if len(created) == 6:
                ctx.pages.extend(reversed(created))
                all_created.set()
            await all_created.wait()
            return {"targetId": self.target_id}

    class Browser:
        def __init__(self):
            self.sessions = []

        async def new_browser_cdp_session(self):
            cdp = Cdp(f"target-{len(self.sessions)}")
            self.sessions.append(cdp)
            return cdp

    class Context(_FakeContext):
        async def new_cdp_session(self, page):
            await asyncio.sleep(0)
            return _FakePageCdp(page)

    monkeypatch.setattr(chrome_cdp, "STEALTH", True)
    browser = Browser()
    ctx = Context(browser)
    ctx.pages = ["foreign-user-tab"]

    pages = await asyncio.wait_for(asyncio.gather(*(chrome_cdp._new_tab(ctx) for _ in range(6))), timeout=1)

    assert pages == [f"target-{index}" for index in range(6)]
    assert len(set(pages)) == 6
    assert ctx.pages == ["foreign-user-tab", *reversed(created)]
    assert all(session.detached for session in browser.sessions)
    assert ctx.plain_pages == 0
