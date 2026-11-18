"""Offline unit tests for Playwright S4 stalled-429 fail-fast and shielded target close."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from mcp_core.transport import chrome_cdp
from mcp_core.transport.cdp_budget import NavigationBudget


class _FakePageCdpSession:
    def __init__(self, target_id: str) -> None:
        self._target_id = target_id
        self.detached = False

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert method == "Target.getTargetInfo"
        assert not params
        return {"targetInfo": {"targetId": self._target_id, "type": "page"}}

    async def detach(self) -> None:
        self.detached = True


class _FakeBrowserCdpSession:
    """Simulates Chrome answering the first Target.closeTarget with success while keeping the tab."""

    def __init__(self, target_id: str, *, keep_open_closes: int = 1) -> None:
        self._target_id = target_id
        self._remaining_ignores = keep_open_closes
        self._alive = True
        self.close_attempts: list[str] = []
        self.get_targets_calls = 0
        self.detached = False

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = params or {}
        if method == "Target.closeTarget":
            tid = str(payload["targetId"])
            self.close_attempts.append(tid)
            if tid == self._target_id:
                if self._remaining_ignores > 0:
                    self._remaining_ignores -= 1
                else:
                    self._alive = False
            return {"success": True}
        if method == "Target.getTargets":
            self.get_targets_calls += 1
            infos = [{"targetId": self._target_id, "type": "page"}] if self._alive else []
            return {"targetInfos": infos}
        raise AssertionError(f"unexpected CDP method: {method}")

    async def detach(self) -> None:
        self.detached = True


class _FakeBrowser:
    def __init__(self, browser_cdp: _FakeBrowserCdpSession) -> None:
        self._browser_cdp = browser_cdp

    async def new_browser_cdp_session(self) -> _FakeBrowserCdpSession:
        return self._browser_cdp


class _FakeContext:
    def __init__(self, page_cdp: _FakePageCdpSession, browser_cdp: _FakeBrowserCdpSession) -> None:
        self._page_cdp = page_cdp
        self.browser = _FakeBrowser(browser_cdp)

    async def new_cdp_session(self, _page: Any) -> _FakePageCdpSession:
        return self._page_cdp


class _FakeRequest:
    def __init__(self, *, nav: bool) -> None:
        self._nav = nav

    def is_navigation_request(self) -> bool:
        return self._nav


class _FakeStalledPage:
    def __init__(self, *, status: int = 429, final_url: str = "https://www.citilink.ru/search/?text=laptop") -> None:
        self.main_frame = object()
        self.url = final_url
        self._status = status
        self.listeners: list[tuple[str, Any]] = []
        self.goto_entered = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.goto_cancelled = False
        self.close_calls = 0

    def on(self, event: str, callback: Any) -> None:
        self.listeners.append((event, callback))

    def remove_listener(self, event: str, callback: Any) -> None:
        self.listeners.remove((event, callback))

    def _emit_response(self, *, frame: object, nav: bool, status: int, url: str) -> None:
        resp = SimpleNamespace(
            frame=frame,
            request=_FakeRequest(nav=nav),
            status=status,
            url=url,
        )
        for event, callback in list(self.listeners):
            if event == "response":
                callback(resp)

    async def goto(self, url: str, **_kwargs: Any) -> Any:
        self.goto_entered.set()
        # Subresource and subframe 429s must never abort main-document navigation.
        self._emit_response(frame=self.main_frame, nav=False, status=429, url=f"{url}&sub=1")
        self._emit_response(frame=object(), nav=True, status=429, url="https://www.citilink.ru/frame")
        if self._status > 0:
            self._emit_response(frame=self.main_frame, nav=True, status=self._status, url=self.url)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.goto_cancelled = True
            raise

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def close(self) -> None:
        self.close_entered.set()
        await asyncio.sleep(0.05)
        self.close_calls += 1


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    fake_page: Any,
    *,
    target_id: str,
) -> tuple[_FakePageCdpSession, _FakeBrowserCdpSession]:
    budget = NavigationBudget()
    monkeypatch.setattr(chrome_cdp, "navigation_budget", lambda: budget)
    monkeypatch.setattr(chrome_cdp, "STEALTH", False)

    page_cdp = _FakePageCdpSession(target_id)
    browser_cdp = _FakeBrowserCdpSession(target_id, keep_open_closes=1)
    fake_ctx = _FakeContext(page_cdp, browser_cdp)

    @asynccontextmanager
    async def fake_get_context():
        yield fake_ctx

    async def fake_new_tab(_ctx: Any) -> Any:
        return fake_page

    monkeypatch.setattr(chrome_cdp, "get_context", fake_get_context)
    monkeypatch.setattr(chrome_cdp, "_new_tab", fake_new_tab)
    return page_cdp, browser_cdp


@pytest.mark.req("R-CDP-SIM-S4", "R-CDP-NAV-TIMEOUT.qrator-429")
async def test_playwright_stalled_429_fails_fast_before_commit_and_confirms_target_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_page = _FakeStalledPage(status=429)
    page_cdp, browser_cdp = _install_fake_playwright(monkeypatch, fake_page, target_id="tab-s4")

    async with asyncio.timeout(3.0):
        with pytest.raises(chrome_cdp.NavBlocked) as caught:
            async with chrome_cdp.open_page(
                "https://www.citilink.ru/search/?text=laptop",
                wait_ms=0,
                allowed_hosts={"www.citilink.ru"},
                navigation_timeout_ms=20_000,
                wait_for_dom_parsed=True,
            ):
                pytest.fail("stalled 429 navigation must not yield a page")

    assert caught.value.status == 429
    assert caught.value.final_url == "https://www.citilink.ru/search/?text=laptop"
    assert fake_page.listeners == []
    assert fake_page.goto_cancelled is True
    assert fake_page.close_calls == 1
    assert page_cdp.detached is True
    assert browser_cdp.detached is True
    assert browser_cdp.close_attempts == ["tab-s4", "tab-s4"]


@pytest.mark.req("R-CDP-SIM-S4", "R-CDP-NAV-TIMEOUT.qrator-429")
async def test_playwright_shielded_cleanup_completes_target_close_when_caller_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_page = _FakeStalledPage(status=0)
    page_cdp, browser_cdp = _install_fake_playwright(monkeypatch, fake_page, target_id="tab-cancel")

    async def _run_nav() -> None:
        async with chrome_cdp.open_page(
            "https://www.citilink.ru/search/?text=hang",
            wait_ms=0,
            allowed_hosts={"www.citilink.ru"},
            navigation_timeout_ms=20_000,
            wait_for_dom_parsed=True,
        ):
            pass

    nav_task = asyncio.create_task(_run_nav())
    await fake_page.goto_entered.wait()
    nav_task.cancel()
    await fake_page.close_entered.wait()
    nav_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await nav_task

    assert fake_page.listeners == []
    assert fake_page.goto_cancelled is True
    assert fake_page.close_calls == 1
    assert page_cdp.detached is True
    assert browser_cdp.detached is True
    assert browser_cdp.close_attempts == ["tab-cancel", "tab-cancel"]


@pytest.mark.req("R-CDP-SIM-S4", "R-CDP-NAV-TIMEOUT.antibot-recheck")
async def test_playwright_403_response_waits_for_commit_and_antibot_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRecheckPage(_FakeStalledPage):
        async def goto(self, _url: str, **_kwargs: Any) -> Any:
            self._emit_response(frame=self.main_frame, nav=True, status=403, url=self.url)
            return SimpleNamespace(status=403)

        def expect_navigation(self, **_kwargs: Any) -> Any:
            page = self

            class _Nav:
                async def __aenter__(self) -> _Nav:
                    page.url = "https://www.citilink.ru/search/?text=ok"
                    return self

                async def __aexit__(self, *_exc: object) -> bool:
                    return False

                @property
                async def value(self) -> Any:
                    return SimpleNamespace(status=200)

            return _Nav()

        async def wait_for_function(self, _expr: str, **_kwargs: Any) -> None:
            return None

    fake_page = _FakeRecheckPage(status=403)
    _, browser_cdp = _install_fake_playwright(monkeypatch, fake_page, target_id="tab-403")

    async with chrome_cdp.open_page(
        "https://www.citilink.ru/search/?text=laptop",
        wait_ms=0,
        allowed_hosts={"www.citilink.ru"},
        navigation_timeout_ms=5_000,
        wait_for_dom_parsed=True,
    ) as yielded:
        assert yielded.url == "https://www.citilink.ru/search/?text=ok"

    assert fake_page.listeners == []
    assert browser_cdp.close_attempts == ["tab-403", "tab-403"]
