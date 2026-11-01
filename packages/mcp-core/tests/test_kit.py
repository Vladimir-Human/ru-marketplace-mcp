"""Tests for the shared connector kit.

Each helper here replaced a copy that lived in several connectors at once, so
the tests pin the behaviour those copies had: the pacing gap the connector
passes in, the refusal hint appended to the source's own message, the host
check that keeps a caller from steering the operator's Chrome to another
domain, the streamed body cap, and the extractor payload rules. The transport
and curl_cffi modules are faked at their import seam, so nothing here touches
a browser, the network or a real curl handle.
"""

from __future__ import annotations

import json
import re
import sys
from types import SimpleNamespace

import pytest
from mcp_core.errors import ErrorCode, ToolError
from mcp_core.kit import blocked_error, host_checked_id, polite_wait, render_json, sync_curl_get
from mcp_core.pacing import Pacer

_PRODUCT_ID_RE = re.compile(r"/product/([A-Za-z0-9_-]+)")
_BARE_ID_RE = re.compile(r"[A-Za-z0-9_-]{4,}")
_HOSTS = ("dns-shop.ru",)


def _extract(raw: str) -> str | None:
    """The dns/citilink shape: this site's host, this site's path pattern."""
    return host_checked_id(raw, hosts=_HOSTS, path_re=_PRODUCT_ID_RE, bare_re=_BARE_ID_RE)


async def _no_wait() -> None:
    return None


class _WaitRecorder:
    """Stands in for a Pacer: records the gap the caller asked for."""

    def __init__(self) -> None:
        self.gaps: list[float | None] = []

    async def wait(self, min_gap: float | None = None) -> None:
        self.gaps.append(min_gap)


class _OrderLock:
    """An async context manager that records entry, so call order is assertable."""

    def __init__(self, order: list[tuple]) -> None:
        self.order = order

    async def __aenter__(self) -> None:
        self.order.append(("locked",))

    async def __aexit__(self, *_exc) -> bool:
        return False


def _pacer_after_refusals(count: int) -> Pacer:
    pacer = Pacer(1.0)
    for _ in range(count):
        pacer.record_refusal()
    return pacer


class _FakeResponse:
    """A curl_cffi response as the streaming reader sees it."""

    def __init__(self, chunks: list[bytes], *, status_code: int = 200, encoding: str = "utf-8") -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.encoding = encoding
        self.closed = False

    def iter_content(self, chunk_size: int):
        assert chunk_size == 64 * 1024
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class _FakeCurl:
    """The bit of ``curl_cffi.requests`` the reader uses."""

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _fake_curl(response: _FakeResponse) -> _FakeCurl:
    return _FakeCurl(response)


class _FakePage:
    def __init__(self, payload: object, order: list[tuple]) -> None:
        self.payload = payload
        self.order = order

    async def evaluate(self, extract_js: str):
        self.order.append(("evaluate", extract_js))
        return self.payload


def _install_fake_open_page(monkeypatch, payload: object) -> list[tuple]:
    """Replace the CDP transport at its import seam; record the call order."""
    order: list[tuple] = []

    class _FakeOpenPage:
        def __init__(self, url: str, wait_ms: int) -> None:
            order.append(("open", url, wait_ms))
            self.page = _FakePage(payload, order)

        async def __aenter__(self):
            return self.page

        async def __aexit__(self, *_exc) -> bool:
            order.append(("close",))
            return False

    monkeypatch.setitem(
        sys.modules,
        "mcp_core.transport.chrome_cdp",
        SimpleNamespace(open_page=_FakeOpenPage),
    )
    return order


# ------------------------------------------------------------------- pacing ----


async def test_polite_wait_passes_the_callers_live_gap():
    """A connector retunes the pace by patching its own ``_min_gap``."""
    recorder = _WaitRecorder()

    await polite_wait(recorder, 7.5)

    assert recorder.gaps == [7.5]


# ------------------------------------------------------------------ refusals ----


def test_blocked_error_appends_the_rotation_hint_and_bounds_the_detail():
    pacer = _pacer_after_refusals(5)

    error = blocked_error("DNS refused the read.", pacer=pacer, detail="x" * 300)

    assert error.code is ErrorCode.TRANSPORT_DOWN
    assert error.status_code == 403
    assert str(error) == f"DNS refused the read. {pacer.rotation_hint()} Detail: {'x' * 160}"


def test_blocked_error_is_the_bare_message_until_refusals_add_up():
    pacer = _pacer_after_refusals(1)

    error = blocked_error("DNS refused the read.", pacer=pacer)

    assert str(error) == "DNS refused the read."
    assert pacer.rotation_hint() == ""


# --------------------------------------------------------------- id checking ----


def test_host_checked_id_accepts_bare_ids_and_our_own_hosts():
    assert _extract("B7A1667F9B19ED20") == "b7a1667f9b19ed20"
    assert _extract("https://www.dns-shop.ru/product/B7A1667F9B19ED20/x/") == "b7a1667f9b19ed20"
    assert _extract("dns-shop.ru/product/abc123/") == "abc123"


def test_host_checked_id_refuses_off_host_and_odd_shapes():
    assert _extract("") is None
    assert _extract("abc") is None
    assert _extract("//www.dns-shop.ru/product/abc123/") is None
    assert _extract("ftp://www.dns-shop.ru/product/abc123/") is None
    assert _extract("https://evil.example/product/abc123/") is None
    assert _extract("https://dns-shop.ru.evil.example/product/abc123/") is None
    assert _extract("https://notdns-shop.ru/product/abc123/") is None


# ------------------------------------------------------------ capped reading ----


def test_sync_curl_get_streams_decodes_and_closes():
    response = _FakeResponse([b"hello ", b"world"], status_code=201)
    fake = _fake_curl(response)

    status, text = sync_curl_get(
        "https://example.test/x",
        get=fake.get,
        headers={"Accept": "application/json"},
        impersonate="chrome",
        timeout=5.0,
        max_body_bytes=64,
        proxy="http://127.0.0.1:8080",
    )

    assert (status, text) == (201, "hello world")
    assert response.closed is True
    url, kwargs = fake.calls[0]
    assert url == "https://example.test/x"
    assert kwargs["headers"] == {"Accept": "application/json"}
    assert kwargs["impersonate"] == "chrome"
    assert kwargs["timeout"] == 5.0
    assert kwargs["stream"] is True
    assert kwargs["proxies"] == {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"}


def test_sync_curl_get_aborts_over_the_cap_and_closes():
    response = _FakeResponse([b"12345", b"67890"])
    fake = _fake_curl(response)

    with pytest.raises(ValueError, match=r"body exceeds 8 bytes \(aborted at 10 during stream\)"):
        sync_curl_get(
            "https://example.test/x",
            get=fake.get,
            headers={},
            impersonate="chrome",
            timeout=5.0,
            max_body_bytes=8,
        )

    assert response.closed is True


def test_sync_curl_get_falls_back_to_utf8_on_an_unknown_encoding():
    response = _FakeResponse(["привет".encode()], encoding="not-a-real-codec")
    fake = _fake_curl(response)

    status, text = sync_curl_get(
        "https://example.test/x",
        get=fake.get,
        headers={},
        impersonate="chrome",
        timeout=5.0,
        max_body_bytes=64,
    )

    assert (status, text) == (200, "привет")


# -------------------------------------------------------------- cdp fetching ----


async def test_render_json_waits_navigates_and_parses(monkeypatch):
    order = _install_fake_open_page(monkeypatch, json.dumps({"items": [1]}))
    waited: list[str] = []

    async def wait() -> None:
        waited.append("waited")

    payload = await render_json(
        "https://example.test/search",
        "() => 1",
        8000,
        lock=_OrderLock(order),
        wait=wait,
        max_body_bytes=1024,
        evaluate_timeout=12.0,
    )

    assert payload == {"items": [1]}
    assert waited == ["waited"]
    assert order[:2] == [("locked",), ("open", "https://example.test/search", 8000)]
    assert order[-1] == ("close",)


async def test_render_json_rejects_a_missing_or_oversized_payload(monkeypatch):
    _install_fake_open_page(monkeypatch, None)

    with pytest.raises(ToolError, match="over the body cap"):
        await render_json(
            "https://example.test/search",
            "() => 1",
            8000,
            lock=_OrderLock([]),
            wait=_no_wait,
            max_body_bytes=1024,
        )

    _install_fake_open_page(monkeypatch, "x" * 2048)

    with pytest.raises(ToolError, match="over the body cap"):
        await render_json(
            "https://example.test/search",
            "() => 1",
            8000,
            lock=_OrderLock([]),
            wait=_no_wait,
            max_body_bytes=1024,
        )


async def test_render_json_rejects_a_non_object_or_non_json_payload(monkeypatch):
    _install_fake_open_page(monkeypatch, json.dumps([1, 2]))

    with pytest.raises(ToolError, match="non-object payload"):
        await render_json(
            "https://example.test/search",
            "() => 1",
            8000,
            lock=_OrderLock([]),
            wait=_no_wait,
            max_body_bytes=1024,
        )

    _install_fake_open_page(monkeypatch, "{not json")

    # R-ERRORS: a non-JSON extract is refused through raise_tool_error as parser
    # drift (it used to escape as a bare json.JSONDecodeError).
    with pytest.raises(ToolError, match="non-JSON data"):
        await render_json(
            "https://example.test/search",
            "() => 1",
            8000,
            lock=_OrderLock([]),
            wait=_no_wait,
            max_body_bytes=1024,
        )


async def test_render_json_refusals_are_json_errors(monkeypatch):
    """Every render_json refusal must carry the shared error JSON the agent reads
    — the code and the retryable flag — not the bare message the ToolError used
    to hold. The oversized/missing extract is a transport outcome, the non-object
    payload is parser drift."""
    _install_fake_open_page(monkeypatch, "x" * 2048)

    with pytest.raises(ToolError) as excinfo:
        await render_json(
            "https://example.test/search",
            "() => 1",
            8000,
            lock=_OrderLock([]),
            wait=_no_wait,
            max_body_bytes=1024,
        )

    error = json.loads(str(excinfo.value))
    assert error["error"] == ErrorCode.TRANSPORT_DOWN
    assert error["retryable"] is True
    assert "over the body cap" in error["message"]

    _install_fake_open_page(monkeypatch, json.dumps([1, 2]))

    with pytest.raises(ToolError) as excinfo:
        await render_json(
            "https://example.test/search",
            "() => 1",
            8000,
            lock=_OrderLock([]),
            wait=_no_wait,
            max_body_bytes=1024,
        )

    error = json.loads(str(excinfo.value))
    assert error["error"] == ErrorCode.PARSER_DRIFT
    assert error["retryable"] is False
    assert error["message"] == "page extractor returned a non-object payload"

    _install_fake_open_page(monkeypatch, "{not json")

    with pytest.raises(ToolError) as excinfo:
        await render_json(
            "https://example.test/search",
            "() => 1",
            8000,
            lock=_OrderLock([]),
            wait=_no_wait,
            max_body_bytes=1024,
        )

    error = json.loads(str(excinfo.value))
    assert error["error"] == ErrorCode.PARSER_DRIFT
    assert error["message"] == "page extractor returned non-JSON data"
