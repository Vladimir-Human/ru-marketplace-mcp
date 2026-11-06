"""open_owned_popup: attribution by openerId and bounded owned-only cleanup (fake raw CDP)."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp_core.transport import chrome_cdp as c

ITEM = "https://aliexpress.ru/item/123.html"


@pytest.mark.parametrize(
    "failure", [None, "body", "attach", "cancel_open", "late_cancel_open", "cancel_body", "redirect"]
)
def test_owned_popup_cleanup_never_closes_foreign_same_url(monkeypatch, failure):
    closed = []
    opened = False
    discoveries = 0

    @asynccontextmanager
    async def connect(url, **kwargs):
        if "/page/" in url and failure == "attach":
            raise OSError("attach failed")
        yield object()

    async def send(self, method, params=None, timeout=30):
        nonlocal opened, discoveries
        if method == "Runtime.evaluate" and self._target_id == "landing":
            assert params["userGesture"] is True
            opened = True
            if failure in {"cancel_open", "late_cancel_open"}:
                raise asyncio.CancelledError
            return {"result": {"value": True}}
        if method == "Target.getTargets":
            discoveries += 1
            return {
                "targetInfos": [
                    # The operator's own tab with the very same URL is never a candidate.
                    {"targetId": "foreign", "openerId": "operator", "url": ITEM},
                    *(
                        [{"targetId": "popup", "openerId": "landing"}]
                        if opened and not (failure == "late_cancel_open" and discoveries == 1)
                        else []
                    ),
                ]
            }
        if method == "Target.closeTarget":
            closed.append(params["targetId"])
        return {}

    async def evaluate(self, expression, arg=None):
        return "complete"

    async def current_url(page):
        page._url = "https://evil.invalid/" if failure == "redirect" else ITEM
        return page._url

    monkeypatch.setattr(c, "_browser_ws_url", lambda: "ws://127.0.0.1:9222/devtools/browser/test")
    monkeypatch.setattr(c, "_websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(c._RawCdpPage, "_send", send)
    monkeypatch.setattr(c._RawCdpPage, "evaluate", evaluate)
    monkeypatch.setattr(c, "current_page_url", current_url)

    async def run():
        async with c.open_owned_popup(
            c._RawCdpPage(object(), "landing"), ITEM, allowed_hosts={"aliexpress.ru"}
        ) as page:
            assert page._target_id == "popup"
            if failure == "body":
                raise ValueError("body failed")
            if failure == "cancel_body":
                raise asyncio.CancelledError

    if failure:
        expected = {
            "body": ValueError,
            "attach": OSError,
            "cancel_open": asyncio.CancelledError,
            "late_cancel_open": asyncio.CancelledError,
            "cancel_body": asyncio.CancelledError,
            "redirect": c.NavigationPolicyError,
        }[failure]
        with pytest.raises(expected):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert closed == ["popup"]


def test_owned_popup_refuses_a_foreign_host_before_opening(monkeypatch):
    sent = []

    async def send(self, method, params=None, timeout=30):
        sent.append(method)
        return {}

    monkeypatch.setattr(c._RawCdpPage, "_send", send)

    async def run():
        async with c.open_owned_popup(
            c._RawCdpPage(object(), "landing"), "https://evil.invalid/item/1.html", allowed_hosts={"aliexpress.ru"}
        ):
            pass

    with pytest.raises(c.NavigationPolicyError):
        asyncio.run(run())
    assert sent == []


def test_owned_popup_needs_an_owned_raw_page():
    async def run():
        async with c.open_owned_popup(object(), ITEM, allowed_hosts={"aliexpress.ru"}):
            pass

    with pytest.raises(TypeError):
        asyncio.run(run())


class _Budget:
    """Records the permit lifecycle open_owned_popup drives (review #195 P2)."""

    def __init__(self, *, refusing=False):
        self.events = []
        self.refusing = refusing

    async def acquire(self, host):
        if self.refusing:
            from mcp_core.transport.cdp_budget import HostRefusingError

            raise HostRefusingError(host, 30.0, 10)
        self.events.append(("acquire", host))
        budget = self

        class _Slot:
            def ok(self):
                budget.events.append(("ok",))

            def refused(self, status=None):
                budget.events.append(("refused", status))

            def neutral(self):
                budget.events.append(("neutral",))

            def release(self):
                if ("release",) not in budget.events:
                    budget.events.append(("release",))

        return _Slot()


def _fake_cdp(
    monkeypatch,
    *,
    urls,
    get_targets_delay=0.0,
    late_second=False,
    status_event=None,
    timing=None,
    cancel_open=False,
    budget=None,
):
    """A fake raw CDP: the landing opens one popup whose URL follows ``urls``."""
    state = SimpleNamespace(closed=[], sent=[], opened=False, discoveries=0, body_ran=False, url_reads=0)
    budget = budget or _Budget()

    @asynccontextmanager
    async def connect(url, **kwargs):
        yield object()

    async def send(self, method, params=None, timeout=30):
        state.sent.append((self._target_id, method))
        if method == "Runtime.evaluate" and self._target_id == "landing":
            state.budget_at_open = tuple(budget.events)
            state.opened = True
            if cancel_open:
                raise asyncio.CancelledError
            return {"result": {"value": True}}
        if method == "Target.getTargets":
            state.discoveries += 1
            if get_targets_delay:
                await asyncio.sleep(get_targets_delay)
            infos = [{"targetId": "foreign", "openerId": "operator", "url": ITEM}]
            if state.opened:
                infos.append({"targetId": "popup", "openerId": "landing"})
            if late_second and state.discoveries > 1:
                infos.append({"targetId": "popup2", "openerId": "landing"})
            return {"targetInfos": infos}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main"}}}
        if method == "Runtime.enable" and status_event is not None:
            # A Network.responseReceived event read while this command waits.
            self._note_event(
                {
                    "method": "Network.responseReceived",
                    "params": {
                        "type": "Document",
                        "frameId": status_event[0],
                        "response": {"status": status_event[1]},
                    },
                }
            )
        if method == "Target.closeTarget":
            state.closed.append(params["targetId"])
        return {}

    async def evaluate(self, expression, arg=None):
        if "readyState" in expression:
            return "complete"
        if "responseStatus" in expression:
            return timing
        raise AssertionError(expression)

    async def current_url(page):
        state.url_reads += 1
        page._url = urls[min(state.url_reads, len(urls)) - 1]
        return page._url

    monkeypatch.setattr(c, "_browser_ws_url", lambda: "ws://127.0.0.1:9222/devtools/browser/test")
    monkeypatch.setattr(c, "_websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(c._RawCdpPage, "_send", send)
    monkeypatch.setattr(c._RawCdpPage, "evaluate", evaluate)
    monkeypatch.setattr(c, "current_page_url", current_url)
    monkeypatch.setattr(c, "navigation_budget", lambda: budget)
    state.budget = budget
    state.budget_at_open = None
    return state


async def _open(state):
    async with c.open_owned_popup(c._RawCdpPage(object(), "landing"), ITEM, allowed_hosts={"aliexpress.ru"}):
        state.body_ran = True


def test_a_redirect_while_ready_state_is_awaited_never_reaches_the_caller(monkeypatch):
    """Review #195 P1: the host was checked before readyState == complete and not
    again before the yield, so an off-host redirect in between reached extraction."""
    state = _fake_cdp(monkeypatch, urls=[ITEM, "https://evil.invalid/item/123.html"])

    with pytest.raises(c.NavigationPolicyError):
        asyncio.run(_open(state))

    assert not state.body_ran
    assert state.closed == ["popup"]


def test_cleanup_waits_for_a_slow_get_targets_after_cancellation(monkeypatch):
    """Review #195 P2: a 1.2 s Target.getTargets after a cancelled open was cut by
    a 1 s inner timeout, owned stayed empty and the popup leaked."""
    state = _fake_cdp(monkeypatch, urls=[ITEM], get_targets_delay=1.2, cancel_open=True)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_open(state))

    assert state.closed == ["popup"]


def test_cleanup_always_rediscovers_and_closes_a_popup_that_arrived_later(monkeypatch):
    """Review #195 P2: cleanup skipped discovery once one popup was owned."""
    state = _fake_cdp(monkeypatch, urls=[ITEM], late_second=True)

    asyncio.run(_open(state))

    assert sorted(state.closed) == ["popup", "popup2"]


def test_the_popup_open_takes_and_returns_a_navigation_permit(monkeypatch):
    """Review #195 P2: open_page releases its permit before yielding, so the popup
    open went past the global/host navigation limits and the breaker."""
    state = _fake_cdp(monkeypatch, urls=[ITEM])
    seen_in_body = []

    async def run():
        async with c.open_owned_popup(c._RawCdpPage(object(), "landing"), ITEM, allowed_hosts={"aliexpress.ru"}):
            seen_in_body.extend(state.budget.events)

    asyncio.run(run())

    assert state.budget_at_open == (("acquire", "aliexpress.ru"),)
    # Released once the document is up, before the caller holds the page.
    assert seen_in_body == [("acquire", "aliexpress.ru"), ("ok",), ("release",)]


def test_a_failed_popup_open_still_returns_the_permit(monkeypatch):
    state = _fake_cdp(monkeypatch, urls=[ITEM], cancel_open=True)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_open(state))

    assert state.budget.events[0] == ("acquire", "aliexpress.ru")
    assert state.budget.events[-1] == ("release",)


def test_an_open_host_breaker_refuses_the_popup_before_it_opens(monkeypatch):
    from fastmcp.exceptions import ToolError

    state = _fake_cdp(monkeypatch, urls=[ITEM], budget=_Budget(refusing=True))

    with pytest.raises(ToolError) as excinfo:
        asyncio.run(_open(state))

    assert "rate_limited" in str(excinfo.value)
    assert not state.opened


@pytest.mark.parametrize(
    ("status_event", "timing", "status"),
    [
        (("main", 403), None, 403),
        (("main", 429), None, 429),
        (None, 403, 403),
    ],
)
def test_a_refused_popup_document_is_nav_blocked_not_content(monkeypatch, status_event, timing, status):
    """Review #195 P2: the popup's HTTP status was not observed, so a 403 page was
    polled 12 times and reported as parser_drift instead of a transport refusal."""
    state = _fake_cdp(monkeypatch, urls=[ITEM], status_event=status_event, timing=timing)

    with pytest.raises(c.NavBlocked) as excinfo:
        asyncio.run(_open(state))

    assert excinfo.value.status == status
    assert not state.body_ran
    assert state.closed == ["popup"]
    assert ("refused", status) in state.budget.events
    assert ("ok",) not in state.budget.events


def test_an_iframe_document_status_does_not_block_the_popup(monkeypatch):
    """Only the main frame's document decides; a 403 iframe is not a refusal."""
    state = _fake_cdp(monkeypatch, urls=[ITEM], status_event=("child-frame", 403), timing=200)

    asyncio.run(_open(state))

    assert state.body_ran


def test_redirect_during_navigation_timing_check_is_caught_before_yield(monkeypatch):
    """Host check must run immediately before yield, after _NAVIGATION_STATUS_JS."""
    state = _fake_cdp(monkeypatch, urls=[ITEM])
    timing_ran = False
    orig_eval = c._RawCdpPage.evaluate

    async def eval_with_redirect(self, expression, arg=None):
        nonlocal timing_ran
        res = await orig_eval(self, expression, arg)
        if "responseStatus" in expression:
            timing_ran = True
        return res

    async def url_after_timing(page):
        page._url = "https://evil.invalid/item/123.html" if timing_ran else ITEM
        return page._url

    monkeypatch.setattr(c._RawCdpPage, "evaluate", eval_with_redirect)
    monkeypatch.setattr(c, "current_page_url", url_after_timing)

    with pytest.raises(c.NavigationPolicyError):
        asyncio.run(_open(state))

    assert not state.body_ran
    assert state.closed == ["popup"]
    assert ("neutral",) in state.budget.events


def test_status_event_arriving_during_navigation_timing_blocks_popup(monkeypatch):
    """A Network.responseReceived event arriving while _NAVIGATION_STATUS_JS runs is not lost."""
    state = _fake_cdp(monkeypatch, urls=[ITEM], timing=0)
    orig_eval = c._RawCdpPage.evaluate

    async def eval_with_late_event(self, expression, arg=None):
        if "responseStatus" in expression:
            self._note_event(
                {
                    "method": "Network.responseReceived",
                    "params": {
                        "type": "Document",
                        "frameId": "main",
                        "response": {"status": 403},
                    },
                }
            )
        return await orig_eval(self, expression, arg)

    monkeypatch.setattr(c._RawCdpPage, "evaluate", eval_with_late_event)

    with pytest.raises(c.NavBlocked) as excinfo:
        asyncio.run(_open(state))

    assert excinfo.value.status == 403
    assert not state.body_ran
    assert ("refused", 403) in state.budget.events


def test_cleanup_discovery_shares_cleanup_deadline_beyond_open_discover_cap(monkeypatch):
    """cleanup() lets Target.getTargets use the cleanup budget minus close reserve, not the 3s open cap."""
    state = _fake_cdp(monkeypatch, urls=[ITEM])
    cleanup_timeouts = []
    orig_send = c._RawCdpPage._send

    async def record_timeout(self, method, params=None, timeout=30):
        if method == "Target.getTargets" and state.body_ran:
            cleanup_timeouts.append(timeout)
        return await orig_send(self, method, params=params, timeout=timeout)

    monkeypatch.setattr(c._RawCdpPage, "_send", record_timeout)

    asyncio.run(_open(state))

    assert cleanup_timeouts and cleanup_timeouts[0] > c._POPUP_DISCOVER_TIMEOUT_S
