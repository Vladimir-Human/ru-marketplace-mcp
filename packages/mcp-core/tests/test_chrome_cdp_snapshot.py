"""Viewport capture validation and bounded CDP lifetime, without a browser."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp_core.transport import chrome_cdp as cdp


def jpeg(width=1440, height=810, extra=b""):
    # Minimal SOF0 envelope: decoding actual pixels is a separate live probe.
    frame = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    data = b"\xff\xd8\xff\xc0\x00\x0b" + frame + extra + b"\xff\xd9"
    return base64.b64encode(data).decode()


JPEG = jpeg()


@pytest.fixture(params=["raw", "playwright"])
def capture(request):
    send = AsyncMock()
    session = SimpleNamespace(send=send, detach=AsyncMock())
    if request.param == "raw":
        page = cdp._RawCdpPage(AsyncMock(), "owned-only")
        page._send = send
    else:
        page = Mock(spec=cdp.Page)
        page.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))
    return page, send, session, request.param


def metrics(**changes):
    return {"cssVisualViewport": {"clientWidth": 1920, "clientHeight": 1080, "pageX": 23.5, "pageY": 1700, **changes}}


async def test_scrolled_viewport_scale_and_transport_cleanup(capture):
    page, send, session, kind = capture
    send.side_effect = [metrics(), {"data": JPEG}]
    result = await cdp.capture_owned_viewport(page)
    assert result == {"image_data": JPEG, "mime_type": "image/jpeg", "width": 1440, "height": 810}
    assert send.await_args_list[0].args == ("Page.getLayoutMetrics",)
    assert send.await_args_list[1].args == (
        "Page.captureScreenshot",
        {
            "format": "jpeg",
            "quality": 70,
            "fromSurface": True,
            "captureBeyondViewport": False,
            "clip": {"x": 23.5, "y": 1700, "width": 1920, "height": 1080, "scale": 0.75},
        },
    )
    if kind == "playwright":
        page.context.new_cdp_session.assert_awaited_once_with(page)
        session.detach.assert_awaited_once()
    else:
        session.detach.assert_not_called()


async def test_layout_fallback_small_viewport_is_not_upscaled(capture):
    page, send, _, _ = capture
    send.side_effect = [
        {"cssLayoutViewport": {"clientWidth": 320, "clientHeight": 200, "pageY": 500}},
        {"data": jpeg(320, 200)},
    ]
    result = await cdp.capture_owned_viewport(page)
    assert (result["width"], result["height"]) == (320, 200)
    assert send.await_args_list[1].args[1]["clip"] == {"x": 0, "y": 500, "width": 320, "height": 200, "scale": 1}


@pytest.mark.parametrize(
    "change",
    [
        {"clientWidth": 0},
        {"clientHeight": -1},
        {"clientWidth": 10001},
        {"clientHeight": None},
        {"clientWidth": float("nan")},
        {"clientHeight": float("inf")},
        {"clientWidth": "1920"},
        {"clientWidth": True},
        {"pageX": float("nan")},
        {"pageY": -1},
        {"pageX": "12"},
    ],
)
async def test_invalid_geometry_never_captures(capture, change):
    page, send, session, kind = capture
    send.return_value = metrics(**change)
    with pytest.raises(RuntimeError, match="viewport geometry"):
        await cdp.capture_owned_viewport(page)
    assert send.await_count == 1
    if kind == "playwright":
        session.detach.assert_awaited_once()


@pytest.mark.parametrize(
    "data, message",
    [
        (None, "no image data"),
        (42, "no image data"),
        ("%%%", "invalid base64"),
        ("абв", "invalid base64"),
        ("", "bounded image size"),
        (base64.b64encode(b"not jpeg").decode(), "invalid JPEG"),
        (base64.b64encode(b"\xff\xd8\xfftruncated").decode(), "invalid JPEG"),
    ],
)
async def test_bad_image_payload_is_rejected(capture, data, message):
    page, send, session, kind = capture
    send.side_effect = [metrics(), {"data": data}]
    with pytest.raises(RuntimeError, match=message):
        await cdp.capture_owned_viewport(page)
    if kind == "playwright":
        session.detach.assert_awaited_once()


@pytest.mark.parametrize("size, accepted", [(20, True), (21, False), (30, False)])
async def test_encoded_and_decoded_byte_limits(capture, monkeypatch, size, accepted):
    page, send, _, _ = capture
    monkeypatch.setattr(cdp, "_MAX_HANDOFF_IMAGE_BYTES", 20)
    encoded = jpeg(extra=b"x" * (size - 17))
    send.side_effect = [metrics(), {"data": encoded}]
    if accepted:
        assert (await cdp.capture_owned_viewport(page))["image_data"] == encoded
    else:
        with pytest.raises(RuntimeError, match="size"):
            await cdp.capture_owned_viewport(page)


async def test_metrics_and_capture_share_one_deadline(capture, monkeypatch):
    page, send, session, kind = capture
    original_timeout = asyncio.timeout
    budgets = []

    def record_timeout(seconds):
        timeout = original_timeout(seconds)
        budgets.append(timeout)
        return timeout

    async def delayed(method, params=None):
        if method == "Page.getLayoutMetrics":
            return metrics()
        # Trigger the actual enclosing budget after metrics have completed.
        budgets[0]._on_timeout()
        await asyncio.Event().wait()

    monkeypatch.setattr(cdp.asyncio, "timeout", record_timeout)
    send.side_effect = delayed
    with pytest.raises(TimeoutError):
        await cdp.capture_owned_viewport(page)
    assert len(budgets) == 1
    if kind == "playwright":
        session.detach.assert_awaited_once()


async def test_playwright_attach_is_inside_deadline(monkeypatch):
    page = Mock(spec=cdp.Page)
    cancelled = asyncio.Event()

    async def attach(page):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    page.context = SimpleNamespace(new_cdp_session=AsyncMock(side_effect=attach))
    monkeypatch.setattr(cdp, "_RAW_CONNECT_TIMEOUT_S", 0.01)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(cdp.capture_owned_viewport(page), timeout=1)
    assert cancelled.is_set()


async def test_capture_cancellation_still_detaches(capture):
    page, send, session, kind = capture
    entered = asyncio.Event()

    async def pending(*args):
        entered.set()
        await asyncio.Event().wait()

    send.side_effect = pending
    task = asyncio.create_task(cdp.capture_owned_viewport(page))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if kind == "playwright":
        session.detach.assert_awaited_once()


async def test_playwright_detach_has_its_own_bounded_cleanup(monkeypatch):
    page = Mock(spec=cdp.Page)
    detached = asyncio.Event()

    async def detach():
        try:
            await asyncio.Event().wait()
        finally:
            detached.set()

    session = SimpleNamespace(send=AsyncMock(side_effect=[metrics(), {"data": JPEG}]), detach=detach)
    page.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))
    monkeypatch.setattr(cdp, "_RAW_CONNECT_TIMEOUT_S", 0.01)
    assert (await asyncio.wait_for(cdp.capture_owned_viewport(page), timeout=1))["image_data"] == JPEG
    assert detached.is_set()


async def test_jpeg_dimensions_are_authoritative_and_dpr_is_corrected_once(capture):
    page, send, _, _ = capture
    send.side_effect = [
        metrics(clientWidth=1253, clientHeight=628),
        {"data": jpeg(1565, 785)},
        {"data": jpeg(1440, 722)},
    ]
    result = await cdp.capture_owned_viewport(page)
    assert (result["width"], result["height"]) == (1440, 722)
    first = send.await_args_list[1].args[1]["clip"]
    second = send.await_args_list[2].args[1]["clip"]
    assert first == {"x": 23.5, "y": 1700, "width": 1253, "height": 628, "scale": 1}
    assert second == {**first, "scale": 1440 / 1565}


async def test_actual_pixels_remaining_oversize_are_rejected_after_one_retry(capture):
    page, send, session, kind = capture
    send.side_effect = [metrics(), {"data": jpeg(1920, 1080)}, {"data": jpeg(1920, 1080)}]
    with pytest.raises(RuntimeError, match="bounded pixel"):
        await cdp.capture_owned_viewport(page)
    assert send.await_count == 3
    if kind == "playwright":
        session.detach.assert_awaited_once()


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8\xff\xd9",  # Missing frame header.
        b"\xff\xd8\xff\xe0\x00\x01\xff\xd9",  # Invalid segment size.
        b"\xff\xd8\xff\xc0\xff\xffshort\xff\xd9",  # Truncated segment.
        b"\xff\xd8\xff\xc0\x00\x07short\xff\xd9",  # Short frame.
        b"\xff\xd8\xff\xff\xd9",  # Marker padding, no frame.
        base64.b64decode(jpeg(0, 400)),
        base64.b64decode(jpeg(500, 0)),
    ],
)
def test_invalid_jpeg_frame_dimensions(data):
    with pytest.raises(RuntimeError, match="JPEG"):
        cdp._handoff_jpeg(base64.b64encode(data).decode())


def test_jpeg_frame_after_app_segment_and_progressive_frame():
    raw = base64.b64decode(jpeg(601, 399))
    # A real JPEG commonly has APP/DQT/DHT segments before SOF.
    raw = raw[:2] + b"\xff\xe0\x00\x06meta" + raw[2:]
    raw = raw.replace(b"\xff\xc0", b"\xff\xc2")
    assert cdp._handoff_jpeg(base64.b64encode(raw).decode()) == (601, 399)


async def test_raw_snapshot_uses_matching_cdp_responses_without_navigation():
    ws = SimpleNamespace(
        send=AsyncMock(),
        recv=AsyncMock(
            side_effect=[
                json.dumps({"method": "Page.frameNavigated", "params": {"frame": {"url": "https://shop.test/card"}}}),
                json.dumps({"id": 1, "result": metrics()}),
                json.dumps({"id": 2, "result": {"data": JPEG}}),
            ]
        ),
    )
    page = cdp._RawCdpPage(ws, "owned")
    assert (await cdp.capture_owned_viewport(page))["image_data"] == JPEG
    assert [json.loads(call.args[0])["method"] for call in ws.send.await_args_list] == [
        "Page.getLayoutMetrics",
        "Page.captureScreenshot",
    ]
    assert page.url == "https://shop.test/card"
