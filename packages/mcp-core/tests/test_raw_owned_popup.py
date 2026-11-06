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
