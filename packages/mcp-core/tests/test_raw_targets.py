"""close_target confirms a raw-CDP tab is gone and closes again when Chrome kept it.

START_MODULE_CONTRACT
PURPOSE: Prove the close-and-confirm loop offline with a fake browser-session send.
SCOPE: First close ignored, immediate close, failing command, a list that never drops the id, bad answers.
DEPENDS: mcp_core.transport.raw_targets; pytest; stdlib asyncio.
LINKS: M-RAW-TARGETS, R-CDP-SIM-S4.
END_MODULE_CONTRACT
START_MODULE_MAP
  FakeBrowser: Records commands; the target survives a configured number of closes.
  test_*: Requirement-derived cases for close_target.
END_MODULE_MAP
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp_core.transport import raw_targets


class FakeBrowser:
    """A browser session whose target outlives the first ``ignored`` closes (Chrome, S4, 2026-11-17)."""

    def __init__(self, ignored: int, *, fail_on: str | None = None, answer: Any = None) -> None:
        self.ignored = ignored
        self.fail_on = fail_on
        self.answer = answer
        self.open = True
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    async def send(self, method: str, params: dict[str, Any], *, timeout: float) -> Any:
        self.calls.append((method, params, timeout))
        if method == self.fail_on:
            raise RuntimeError(f"CDP {method}: boom")
        if method == "Target.closeTarget":
            if self.ignored > 0:
                self.ignored -= 1
            else:
                self.open = False
            return {"success": True}
        if self.answer is not None:
            return self.answer
        infos = [{"targetId": "other", "type": "page"}]
        if self.open:
            infos.append({"targetId": "T1", "type": "page"})
        return {"targetInfos": infos}

    def methods(self) -> list[str]:
        return [method for method, _params, _timeout in self.calls]


def _close(browser: FakeBrowser) -> bool:
    return asyncio.run(raw_targets.close_target(browser.send, "T1", timeout_s=8.0, recheck_s=0))


@pytest.mark.req("R-CDP-SIM-S4")
def test_a_close_chrome_ignored_is_sent_again_until_the_tab_is_gone() -> None:
    browser = FakeBrowser(ignored=1)
    assert _close(browser) is True
    assert not browser.open
    assert browser.methods() == ["Target.closeTarget", "Target.getTargets"] * 2
    assert all(params == {"targetId": "T1"} for method, params, _ in browser.calls if method == "Target.closeTarget")
    assert {timeout for _method, _params, timeout in browser.calls} == {8.0}


@pytest.mark.req("R-CDP-SIM-S4")
def test_a_close_that_works_is_checked_once() -> None:
    browser = FakeBrowser(ignored=0)
    assert _close(browser) is True
    assert browser.methods() == ["Target.closeTarget", "Target.getTargets"]


@pytest.mark.req("R-CDP-SIM-S4")
def test_a_tab_that_never_goes_stops_after_the_attempts() -> None:
    browser = FakeBrowser(ignored=100)
    assert _close(browser) is False
    assert browser.methods().count("Target.closeTarget") == raw_targets.CLOSE_ATTEMPTS


@pytest.mark.req("R-CDP-SIM-S4")
@pytest.mark.parametrize("fail_on", ["Target.closeTarget", "Target.getTargets"])
def test_a_failing_command_ends_the_close_without_raising(fail_on: str) -> None:
    browser = FakeBrowser(ignored=1, fail_on=fail_on)
    assert _close(browser) is False
    assert len(browser.calls) <= 2


@pytest.mark.req("R-CDP-SIM-S4")
@pytest.mark.parametrize("answer", [{"targetInfos": None}, {}, [], "nonsense"])
def test_an_answer_without_a_target_list_is_not_taken_as_closed(answer: Any) -> None:
    browser = FakeBrowser(ignored=0, answer=answer)
    assert _close(browser) is False
    assert browser.methods() == ["Target.closeTarget", "Target.getTargets"]
