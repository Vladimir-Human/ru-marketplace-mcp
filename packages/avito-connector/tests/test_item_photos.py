"""avito_card(photos=N): opt-in, paced, host-pinned listing photos as MCP image content.

The photo download is replaced in every test: no test touches Avito's CDN. The
fixture is a trimmed, de-identified capture whose gallery keeps the captured
entries with fictional photo URLs; videos and off-host URLs are tested on plain
dicts.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from avito_connector import item_state, server
from fastmcp.tools import ToolResult

FIXTURES = Path(__file__).parent / "fixtures"
PHOTOS = "item_photos_active_9000000020.html"
JPEG = b"\xff\xd8\xff\xe0fixture-jpeg\xff\xd9"
URL_1 = "https://70.img.avito.st/image/1/1.fixture-photo-1.640x480"
URL_2 = "https://00.img.avito.st/image/1/1.fixture-photo-2.640x480"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    """Every photo fetch waits on the source's own pacer; the waits are counted, not slept."""
    server._cache._data.clear()
    waits: list[int] = []

    async def counted_wait():
        waits.append(1)

    monkeypatch.setattr(server, "_polite_wait", counted_wait)
    yield waits
    server._cache._data.clear()


def _serve(monkeypatch, html: str, responses):
    async def fake_page(url, ctx):
        return 200, html, "cdp"

    fetched: list[str] = []
    queue = list(responses)

    def fake_get(url):
        fetched.append(url)
        response = queue.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(server, "_fetch_item_page", fake_page)
    monkeypatch.setattr(server, "_sync_photo_get", fake_get)
    return fetched


def _gallery(media) -> str:
    bi = {"item": {"id": 1, "isActive": True, "deliveryInfo": {"isReserved": False}}, "galleryInfo": {"media": media}}
    state = json.dumps(json.dumps({"loaderData": {"x": {"buyerItem": bi}}}))
    return f"<script>window.__staticRouterHydrationData = JSON.parse({state});</script>"


def test_photo_urls_come_from_the_captured_gallery():
    assert item_state.photo_urls(_page(PHOTOS), 9000000020) == [URL_1, URL_2]


def test_photo_urls_skip_videos_foreign_hosts_and_duplicates():
    media = [
        {"isVideo": True, "urls": {"640x480": "https://00.img.avito.st/video.640x480"}},
        {"isVideo": False, "urls": {"640x480": "https://evil.invalid/image/1/x.640x480"}},
        {"isVideo": False, "urls": {"1280x960": "https://01.img.avito.st/image/1/big.1280x960"}},
        {"isVideo": False, "urls": {"640x480": URL_1}},
        {"isVideo": False, "urls": {"640x480": URL_1}},
        {"urls": {"640x480": URL_2}},
    ]

    assert item_state.photo_urls(_gallery(media), 1) == ["https://01.img.avito.st/image/1/big.1280x960", URL_1]


@pytest.mark.parametrize(
    "url",
    [
        "http://00.img.avito.st/x.jpg",
        "https://img.avito.st.evil.invalid/x.jpg",
        "https://user@00.img.avito.st/x.jpg",
        "https://00.img.avito.st:8443/x.jpg",
        "https://avito.ru/x.jpg",
    ],
)
def test_photo_host_allowlist(url):
    assert item_state.photo_url_allowed(url) is False


async def test_photos_zero_makes_no_extra_request_and_returns_the_plain_card(monkeypatch):
    """Review #199: photos=0 (the default) must not fetch anything beyond the card page."""
    pages: list[str] = []

    async def fake_page(url, ctx):
        pages.append(url)
        return 200, _page(PHOTOS), "cdp"

    def no_photo_fetch(url):
        raise AssertionError(f"photo fetched with photos=0: {url}")

    monkeypatch.setattr(server, "_fetch_item_page", fake_page)
    monkeypatch.setattr(server, "_sync_photo_get", no_photo_fetch)
    monkeypatch.setattr(server.cffi, "get", no_photo_fetch)

    default = await server.avito_card("9000000020")
    server._cache._data.clear()
    explicit = await server.avito_card("9000000020", photos=0)

    for result in (default, explicit):
        assert not isinstance(result, ToolResult)
        assert result.photos == [] and result.photos_status is None
    assert len(pages) == 2


async def test_photos_arrive_as_image_content_after_the_json(monkeypatch, _setup):
    fetched = _serve(monkeypatch, _page(PHOTOS), [(200, "image/jpeg", JPEG), (200, "image/jpeg", JPEG)])

    result = await server.avito_card("9000000020", photos=2)

    assert isinstance(result, ToolResult)
    text, *images = result.content
    payload = json.loads(text.text)
    assert payload == result.structured_content
    assert payload["photos_status"] == "ok"
    assert [p["delivered"] for p in payload["photos"]] == [True, True]
    assert [image.mimeType for image in images] == ["image/jpeg", "image/jpeg"]
    assert base64.b64decode(images[0].data) == JPEG
    assert fetched == [URL_1, URL_2]
    assert _setup == [1, 1]


async def test_fewer_photos_on_the_page_than_asked_is_said(monkeypatch):
    """Review #199: the agent sees why fewer photos arrived than it asked for; v2.9.0 said ok."""
    _serve(monkeypatch, _page(PHOTOS), [(200, "image/jpeg", JPEG), (200, "image/jpeg", JPEG)])

    result = await server.avito_card("9000000020", photos=3)

    assert result.structured_content["photos_status"] == "fewer_on_page"
    assert [p["delivered"] for p in result.structured_content["photos"]] == [True, True]


async def test_every_photo_fetch_waits_on_the_sources_pacer(monkeypatch, _setup):
    """No private sleep: the source's gap and its back-off after a refusal apply to every photo, the first included.

    v2.9.0 slept its own 1 s between photos and fetched the first one right after the card page.
    """
    _serve(monkeypatch, _page(PHOTOS), [(200, "image/jpeg", JPEG)])

    await server.avito_card("9000000020", photos=1)

    assert _setup == [1]
    assert not hasattr(server, "_PHOTO_GAP_S")


async def test_the_advertised_photos_argument_is_capped_at_three_and_off_by_default():
    """Review #199: the cap is 3, not 5; the description is short and points to the guide."""
    tool = next(tool for tool in await server.mcp.list_tools() if tool.name == "avito_card")
    photos = tool.parameters["properties"]["photos"]

    assert (photos["minimum"], photos["maximum"], photos["default"]) == (0, 3, 0)
    assert "guide://avito/avito_card" in photos["description"]
    assert len(photos["description"]) < 260


@pytest.mark.parametrize(
    ("response", "status", "refusal"),
    [
        ((403, "text/html", b""), "refused_403", True),
        ((429, "text/html", b""), "rate_limited", True),
        ((404, "text/html", b""), "refused_http", False),
        ((200, "text/html", b"<html>"), "bad_type", False),
        ((200, "image/jpeg", b""), "bad_type", False),
        ((200, "image/jpeg", b"<html>not-a-jpeg</html>"), "bad_type", False),
        (server._PhotoTooLarge(), "too_large", False),
        (TimeoutError(), "fetch_failed", False),
    ],
)
async def test_the_first_refusal_stops_the_photo_loop_with_its_status(monkeypatch, response, status, refusal):
    """Review #199: a photos_status for every outcome; the loop stops at the first one, no retry."""
    refusals = []
    monkeypatch.setattr(server._pacer, "record_refusal", lambda: refusals.append(1))
    fetched = _serve(monkeypatch, _page(PHOTOS), [response, (200, "image/jpeg", JPEG)])

    result = await server.avito_card("9000000020", photos=2)

    payload = result.structured_content
    assert payload["photos_status"] == status
    assert [p["delivered"] for p in payload["photos"]] == [False]
    assert len(result.content) == 1
    assert fetched == [URL_1]
    assert refusals == ([1] if refusal else [])


async def test_a_refusal_after_a_delivered_photo_keeps_that_photo(monkeypatch):
    _serve(monkeypatch, _page(PHOTOS), [(200, "image/jpeg", JPEG), (403, "text/html", b"")])

    result = await server.avito_card("9000000020", photos=3)

    assert result.structured_content["photos_status"] == "refused_403"
    assert [p["delivered"] for p in result.structured_content["photos"]] == [True, False]
    assert len(result.content) == 2


async def test_no_photo_is_fetched_for_a_reserved_ad(monkeypatch):
    fetched = _serve(monkeypatch, _page("item_state_reserved_9000000011.html"), [])

    result = await server.avito_card("9000000011", photos=3)

    assert result.photos_status == "skipped_inactive"
    assert fetched == []


async def test_a_page_without_gallery_reports_none_on_page(monkeypatch):
    fetched = _serve(monkeypatch, _page("item_state_active_9000000012.html"), [])

    result = await server.avito_card("9000000012", photos=1)

    assert result.structured_content["photos_status"] == "none_on_page"
    assert fetched == []


def test_a_streamed_photo_over_the_cap_is_aborted(monkeypatch):
    """The download itself is bounded: it stops reading past 2 MB."""
    read: list[int] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}  # noqa: RUF012

        def iter_content(self, chunk_size):
            for _ in range(100):
                read.append(chunk_size)
                yield b"x" * chunk_size

        def close(self):
            pass

    monkeypatch.setattr(server.cffi, "get", lambda *a, **k: FakeResponse())

    with pytest.raises(server._PhotoTooLarge):
        server._sync_photo_get(URL_1)
    assert sum(read) <= server._PHOTO_MAX_BYTES + 64 * 1024


def test_a_declared_oversized_photo_is_not_read(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg", "content-length": str(server._PHOTO_MAX_BYTES + 1)}  # noqa: RUF012

        def iter_content(self, chunk_size):
            raise AssertionError("body read despite the declared size")

        def close(self):
            pass

    monkeypatch.setattr(server.cffi, "get", lambda *a, **k: FakeResponse())

    with pytest.raises(server._PhotoTooLarge):
        server._sync_photo_get(URL_1)


def test_sync_photo_get_disables_redirects_and_rechecks_host(monkeypatch):
    captured_kwargs = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg"}  # noqa: RUF012

        def iter_content(self, chunk_size):
            yield JPEG

        def close(self):
            pass

    def fake_get(url, **kwargs):
        captured_kwargs.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(server.cffi, "get", fake_get)

    status, ctype, data = server._sync_photo_get(URL_1)
    assert (status, ctype, data) == (200, "image/jpeg", JPEG)
    assert captured_kwargs.get("allow_redirects") is False

    with pytest.raises(ValueError, match="disallowed photo URL"):
        server._sync_photo_get("https://evil.invalid/x.jpg")
