import asyncio
import json
import os
import time
import tomllib
from datetime import datetime
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from mcp_core import process
from ozon_connector import server


def test_server_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == server.SERVER_VERSION


def _run(coro):
    return asyncio.run(coro)


def _reviews_body(*, reviews=None, next_button=None):
    paging = {"total": 5}
    if next_button:
        paging["nextButton"] = next_button
    return json.dumps(
        {
            "widgetStates": {
                "webListReviews-1": json.dumps(
                    {
                        "paging": paging,
                        "reviews": reviews or [],
                    }
                ),
                "webReviewProductScore-1": json.dumps(
                    {
                        "totalScore": 4.7,
                        "reviewsCount": 5,
                        "score": [{"title": "5 звезд", "value": 4}],
                    }
                ),
            },
        }
    )


def test_sync_call_in_process_times_out_and_next_call_still_works():
    start = time.monotonic()
    try:
        server._sync_call_in_process(time.sleep, (5,), 0.05)
    except server._SyncCallTimeout:
        pass
    else:
        raise AssertionError("slow child must time out")

    assert time.monotonic() - start < 2
    assert server._sync_call_in_process(abs, (-3,), 2) == 3


def test_sync_call_in_process_scrubs_child_env(monkeypatch):
    monkeypatch.setenv("OZON_SENTINEL_SECRET", "leak")

    assert server._sync_call_in_process(os.getenv, ("OZON_SENTINEL_SECRET",), 2) is None


def test_sync_call_in_process_redacts_url_userinfo_from_child_errors():
    try:
        server._sync_call_in_process(
            exec,
            ("raise RuntimeError('https://user:secret@example.com/path?token=x')",),
            2,
        )
    except server._SyncCallError as exc:
        message = str(exc)
    else:
        raise AssertionError("child exception expected")

    assert "user:secret" not in message
    assert "token=x" not in message
    assert "<redacted-query>" in message


def test_safe_child_env_does_not_case_fold_on_posix(monkeypatch):
    """A lowercase 'path' must not slip through the POSIX allowlist.

    Case-folding is correct on Windows (env keys are case-insensitive there) and
    wrong on POSIX, where 'path' and 'PATH' are different variables.
    """
    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    monkeypatch.setitem(os.environ, "path", "lowercase-secret")

    assert process.safe_child_env().get("path") is None


def test_safe_child_env_case_folds_on_windows(monkeypatch):
    """Under the Windows rule, a differently-cased allowlist key still passes.

    Windows environment keys are case-insensitive, so 'LocalAppData' and
    'LOCALAPPDATA' are the same variable and both belong in a child environment.
    POSIX keys are matched verbatim (covered by the test above).

    The environment is injected as a plain mixed-case dict rather than through
    monkeypatch.setitem(os.environ, ...): real os.environ on Windows upper-cases
    keys on write, so a mixed-case probe set that way would be silently folded
    to LOCALAPPDATA before safe_child_env ever sees it, making the case-fold
    rule unobservable. The dict keeps the mixed case the function must handle.
    """
    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "nt")
    monkeypatch.setattr(
        process.os,
        "environ",
        {
            "LocalAppData": r"C:\Users\op\AppData\Local",
            "SystemRoot": r"C:\Windows",
        },
    )

    env = process.safe_child_env()

    assert env.get("LocalAppData") == r"C:\Users\op\AppData\Local"
    assert env.get("SystemRoot") == r"C:\Windows"


def test_safe_child_env_excludes_secrets_on_every_platform(monkeypatch):
    monkeypatch.setitem(os.environ, "OZON_SENTINEL_SECRET", "leak")
    monkeypatch.setitem(os.environ, "AWS_SECRET_ACCESS_KEY", "leak")

    env = process.safe_child_env()

    assert "OZON_SENTINEL_SECRET" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_worker_process_kwargs_per_platform(monkeypatch):
    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    assert process.worker_process_kwargs() == {"start_new_session": True}

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "nt")
    kwargs = process.worker_process_kwargs()
    assert "creationflags" in kwargs
    assert kwargs["creationflags"] == 0x00000200 | 0x08000000


def test_run_sync_bounded_rejects_local_callables():
    async def scenario():
        def local():
            return 1

        try:
            await server._run_sync_bounded(local, timeout_s=0.1)
        except server._SyncCallError:
            pass
        else:
            raise AssertionError("local callable must not fall back to to_thread")

    asyncio.run(scenario())


class _FakeProc:
    """Minimal Popen stand-in that records how it was torn down."""

    def __init__(self, captured: dict, pid: int = 12345) -> None:
        self.pid = pid
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self._captured = captured

    def poll(self):
        return None

    def kill(self):
        self._captured["killed"] = True

    def wait(self, timeout=None):
        self._captured["wait_timeout"] = timeout


def test_terminate_worker_tree_uses_absolute_taskkill_and_sanitized_env(monkeypatch):
    """On Windows: kill the whole tree via an absolute, un-hijackable taskkill.

    Runs on every platform via PLATFORM_OVERRIDE, so the Windows contract is
    covered by CI on Linux and macOS too.
    """
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "nt")
    monkeypatch.setenv("OZON_SENTINEL_SECRET", "leak")
    # A hijacked environment must not be able to redirect taskkill.
    monkeypatch.setenv("SystemRoot", r"C:\attacker")
    monkeypatch.setenv("WINDIR", r"C:\attacker")
    monkeypatch.setattr(process.subprocess, "run", fake_run)

    process.terminate_process_tree(_FakeProc(captured))

    assert captured["argv"][0].lower().endswith(r"\system32\taskkill.exe")
    assert not captured["argv"][0].lower().startswith(r"c:\attacker")
    assert captured["argv"][1:] == ["/PID", "12345", "/T", "/F"]
    assert "OZON_SENTINEL_SECRET" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["env"]["SystemRoot"].lower() != r"c:\attacker"
    assert captured["wait_timeout"] == 0.5


def test_terminate_worker_tree_kills_process_group_on_posix(monkeypatch):
    """On POSIX: SIGKILL the child's process group, never a bare kill()."""
    captured: dict = {}
    killed: dict = {}

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    # Patch the connector's own seam rather than os.getpgid/os.killpg: those do
    # not exist on Windows, so patching them made this test unrunnable there.
    monkeypatch.setattr(process, "kill_process_group", lambda pid: killed.update(pid=pid))

    process.terminate_process_tree(_FakeProc(captured))

    assert killed["pid"] == 12345
    assert "killed" not in captured  # the group kill succeeded, so no fallback
    assert captured["wait_timeout"] == 0.5


def test_terminate_worker_tree_falls_back_to_kill_when_killpg_fails(monkeypatch):
    """A failed killpg must degrade to proc.kill(), never propagate."""
    captured: dict = {}

    def boom(_pid):
        raise ProcessLookupError("gone")

    monkeypatch.setattr(process, "PLATFORM_OVERRIDE", "posix")
    monkeypatch.setattr(process, "kill_process_group", boom)

    process.terminate_process_tree(_FakeProc(captured))

    assert captured.get("killed") is True


def test_kill_process_group_uses_posix_signalling(monkeypatch):
    """The POSIX path signals the whole group, not just the child pid."""
    calls: dict = {}

    monkeypatch.setattr(process.os, "getpgid", lambda pid: pid + 1000, raising=False)
    monkeypatch.setattr(process.os, "killpg", lambda pgid, sig: calls.update(pgid=pgid, sig=sig), raising=False)
    monkeypatch.setattr(process.signal, "SIGKILL", 9, raising=False)

    process.kill_process_group(42)

    assert calls == {"pgid": 1042, "sig": 9}


def test_kill_process_group_refuses_where_process_groups_do_not_exist(monkeypatch):
    """On Windows os.killpg simply does not exist, so this must raise cleanly.

    terminate_process_tree turns that into a proc.kill() fallback; what matters
    here is that it raises AttributeError rather than crashing on a missing name.
    """
    monkeypatch.delattr(process.os, "killpg", raising=False)

    with pytest.raises(AttributeError):
        process.kill_process_group(42)


def test_canonical_composer_path_rejects_unsafe_search_query_keys():
    assert server._canonical_composer_path("/search/?text=abc&page=1") == "/search/?text=abc&page=1"

    for path in (
        "/search/?text=abc&redirect=https://attacker.example",
        "/search/?url=//attacker.example",
        "https://www.ozon.ru/search/?text=abc",
    ):
        try:
            server._canonical_composer_path(path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe path accepted: {path}")


def test_parse_search_tile_reads_textds_tile_name():
    item = {
        "sku": 1576326039,
        "action": {"link": "/product/example-1576326039/"},
        "mainState": [
            {"type": "priceV2", "priceV2": {"price": [{"text": "2 890 ₽"}]}},
            {
                "type": "textDS",
                "id": "name",
                "textDS": {
                    "text": "Network filter AGNI 5 sockets",
                    "testInfo": {"automatizationId": "tile-name"},
                },
            },
            {
                "type": "textDS",
                "textDS": {
                    "text": "1577 шт осталось",
                    "testInfo": {"automatizationId": "tile-stock"},
                },
            },
            {
                "type": "labelListV2",
                "labelListV2": {
                    "testInfo": {"automatizationId": "tile-list-rating"},
                    "items": [
                        {"type": "text", "text": {"text": "4.9"}},
                        {"type": "text", "text": {"text": "19189 reviews"}},
                    ],
                },
            },
        ],
    }

    parsed = server._parse_search_tile(item)

    assert parsed["title"] == "Network filter AGNI 5 sockets"
    assert parsed["price"] == "2 890 ₽"
    assert parsed["rating"] == "4.9"
    assert parsed["stock"] == "1577 шт осталось"


def test_parse_search_tile_exposes_canonical_card_input_for_slug_link():
    item = {
        "sku": 1576326039,
        "action": {"link": "/product/example-name-1576326039/"},
        "mainState": [
            {
                "type": "textDS",
                "id": "name",
                "textDS": {
                    "text": "Network filter AGNI 5 sockets",
                    "testInfo": {"automatizationId": "tile-name"},
                },
            },
        ],
    }

    parsed = server._parse_search_tile(item)

    assert parsed["url"] == "https://www.ozon.ru/product/example-name-1576326039/"
    assert parsed["canonical_path"] == "/product/1576326039/"
    assert parsed["card_input"] == "/product/1576326039/"


def test_canonical_product_path_ignores_relative_query_digits():
    canonical, error = server._canonical_product_path_from_input("/product/real-123?ref=-456")

    assert error is None
    assert canonical == "/product/123/"


def test_parse_search_tile_canonicalizes_absolute_ozon_product_link():
    parsed = server._parse_search_tile(
        {
            "sku": 1576326039,
            "action": {"link": "https://m.ozon.ru/product/example-name-1576326039/?from=search"},
            "mainState": [],
        }
    )

    assert parsed["url"] == "https://www.ozon.ru/product/1576326039/"
    assert parsed["canonical_path"] == "/product/1576326039/"
    assert parsed["card_input"] == "/product/1576326039/"


def test_parse_search_tile_omits_unsafe_or_non_product_links():
    for link in (
        "javascript:alert(1)",
        "https://example.com/product/example-name-1576326039/",
        "https://user:pass@www.ozon.ru/product/example-name-1576326039/",
        "/category/electronics/",
    ):
        parsed = server._parse_search_tile(
            {
                "sku": 1576326039,
                "action": {"link": link},
                "mainState": [],
            }
        )

        assert "url" not in parsed
        assert "canonical_path" not in parsed
        assert "card_input" not in parsed


def test_parse_search_tile_ignores_hostile_nested_shapes():
    item = {
        "sku": {"object": "not hashable"},
        "id": ["not", "scalar"],
        "action": {"link": {"not": "a string"}},
        "mainState": [
            "not an atom",
            {
                "type": "priceV2",
                "priceV2": {"price": [{"text": {"rich": "object"}}]},
            },
            {
                "type": "textDS",
                "id": "name",
                "textDS": {
                    "text": "safe title",
                    "testInfo": ["not", "a", "dict"],
                },
            },
            {
                "type": "labelList",
                "labelList": {"items": [{"title": {"rich": "object"}}]},
            },
            {
                "type": "labelListV2",
                "labelListV2": {
                    "testInfo": {"automatizationId": "tile-list-rating"},
                    "items": [{"type": "text", "text": {"text": {"rich": "object"}}}],
                },
            },
        ],
    }

    parsed = server._parse_search_tile(item)

    assert "sku" not in parsed
    assert "url" not in parsed
    assert parsed["title"] == "safe title"
    assert parsed["price"] is None
    assert parsed["rating"] is None
    assert parsed["stock"] is None


def test_parse_search_tile_non_dict_returns_empty():
    assert server._parse_search_tile(["not", "a", "tile"]) == {}


def test_parse_search_tile_non_list_main_state_no_crash():
    parsed = server._parse_search_tile({"sku": 1, "mainState": 123})

    assert parsed["sku"] == 1
    assert parsed["title"] is None


def test_safe_review_page_path_rejects_non_string_next_button():
    assert server._safe_review_page_path("123", {"page": 2}) is None
    assert server._safe_review_page_path("123", ["?page=2"]) is None


def test_sync_curl_get_closes_non_context_manager_response(monkeypatch):
    class FakeResponse:
        status_code = 200
        encoding = "utf-8"
        closed = False

        def iter_content(self, chunk_size):
            yield b'{"ok": true}'

        def close(self):
            self.closed = True

    fake = FakeResponse()

    def fake_get(*args, **kwargs):
        return fake

    monkeypatch.setattr(server.cffi, "get", fake_get)

    status, body = server._sync_curl_get("https://www.ozon.ru/api/test")

    assert status == 200
    assert body == '{"ok": true}'
    assert fake.closed is True


def test_cdp_fetch_json_times_out_open_page_and_releases_lock(monkeypatch):
    class HangingOpenPage:
        async def __aenter__(self):
            await asyncio.sleep(10)

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def scenario():
        lock = asyncio.Lock()
        monkeypatch.setattr(server, "_cdp_lock", lock)
        monkeypatch.setattr(server, "TIMEOUT", 0.01)
        monkeypatch.setattr(server, "open_page", lambda *args, **kwargs: HangingOpenPage())

        status, body = await asyncio.wait_for(
            server._cdp_fetch_json("https://www.ozon.ru/api/test", None),
            timeout=0.2,
        )

        assert status == 0
        assert "timeout" in body.lower()
        assert not lock.locked()

    _run(scenario())


def test_fetch_composer_times_out_blocking_curl_and_falls_back_to_cdp(monkeypatch):
    def blocking_get(url):
        time.sleep(0.3)
        return 200, '{"from": "curl"}'

    async def fake_cdp(api_url, ctx):
        return 200, '{"from": "cdp"}'

    async def scenario():
        monkeypatch.setattr(server, "TIMEOUT", 0.01)
        monkeypatch.setattr(server, "_min_gap", 0)
        monkeypatch.setattr(server, "_sync_curl_get", blocking_get)
        monkeypatch.setattr(server, "_cdp_fetch_json", fake_cdp)

        status, body, tier = await asyncio.wait_for(
            server._fetch_composer("/product/123/", None),
            timeout=0.2,
        )

        assert status == 200
        assert json.loads(body) == {"from": "cdp"}
        assert tier == "cdp"

    _run(scenario())


def test_fetch_composer_reports_cdp_navigation_block_as_blocked(monkeypatch):
    def blocked_get(url):
        return 403, "<html>challenge</html>"

    async def blocked_cdp(api_url, ctx):
        raise server.NavBlocked(403, "https://www.ozon.ru/")

    async def scenario():
        monkeypatch.setattr(server, "_min_gap", 0)
        monkeypatch.setattr(server, "_sync_curl_get", blocked_get)
        monkeypatch.setattr(server, "_cdp_fetch_json", blocked_cdp)

        status, body, tier = await server._fetch_composer("/product/123/", None)

        assert status == 403
        assert "navigation blocked" in body
        assert tier == "cdp_blocked"

    _run(scenario())


def test_ozon_search_reports_actionable_cdp_block(monkeypatch):
    async def blocked_fetch(path, ctx):
        return 403, "navigation blocked: HTTP 403", "cdp_blocked"

    async def scenario():
        monkeypatch.setattr(server, "_fetch_composer", blocked_fetch)

        with pytest.raises(ToolError) as excinfo:
            await server.ozon_search("сетевой фильтр")
        msg = str(excinfo.value)

        assert "transport_down" in msg
        assert "browser session is blocked" in msg
        assert "solve any captcha" in msg
        assert "cdp_blocked" in msg
        assert "403" in msg

    _run(scenario())


def test_ozon_search_skips_unhashable_sku_values(monkeypatch):
    widgets = {
        "tileGridDesktop-1": json.dumps(
            {
                "items": [
                    {"sku": [], "mainState": [{"type": "textDS", "id": "name", "textDS": {"text": "Bad list"}}]},
                    {"sku": {}, "mainState": [{"type": "textDS", "id": "name", "textDS": {"text": "Bad dict"}}]},
                    {"sku": False, "mainState": [{"type": "textDS", "id": "name", "textDS": {"text": "Bad bool"}}]},
                    {"sku": 123, "mainState": [{"type": "textDS", "id": "name", "textDS": {"text": "Good int"}}]},
                    {
                        "sku": "123",
                        "mainState": [{"type": "textDS", "id": "name", "textDS": {"text": "Duplicate string"}}],
                    },
                ],
            }
        )
    }

    async def fake_fetch(path, ctx):
        return 200, json.dumps({"widgetStates": widgets}), "fake"

    async def scenario():
        monkeypatch.setattr(server, "_fetch_composer", fake_fetch)
        result = await server.ozon_search("x")
        data = result.model_dump()
        assert data["status"] == "success"
        assert data["count"] == 1
        assert data["items"][0]["sku"] == 123
        assert data["items"][0]["title"] == "Good int"

    _run(scenario())


def test_ozon_selfcheck_includes_runtime_identity(monkeypatch):
    search_body = json.dumps(
        {
            "widgetStates": {
                "tileGridDesktop-1": json.dumps(
                    {
                        "items": [
                            {
                                "sku": 123,
                                "mainState": [
                                    {
                                        "type": "textDS",
                                        "id": "name",
                                        "textDS": {"text": "Good filter"},
                                    },
                                ],
                            },
                        ],
                    }
                ),
            },
        }
    )
    card_body = json.dumps(
        {
            "widgetStates": {
                "webPrice-1": json.dumps(
                    {
                        "price": "1 000 ₽",
                        "cardPrice": "900 ₽",
                        "isAvailable": True,
                    }
                ),
                "webReviewProductScore-1": json.dumps(
                    {
                        "totalScore": 4.7,
                        "reviewsCount": 5,
                    }
                ),
            },
        }
    )
    reviews_body = _reviews_body(reviews=[{"content": {"comment": "ok"}}])

    async def fake_fetch(path, ctx):
        if path.startswith("/search/"):
            return 200, search_body, "fake"
        if "/reviews/" in path:
            return 200, reviews_body, "fake"
        return 200, card_body, "fake"

    async def scenario():
        monkeypatch.setattr(server, "_fetch_composer", fake_fetch)
        result = await server.ozon_selfcheck()
        data = result.model_dump()
        assert data["status"] == "success"
        assert data["server_version"] == server.SERVER_VERSION
        assert isinstance(data["server_started_at"], str)
        assert data["server_started_at"].endswith("Z")
        datetime.fromisoformat(data["server_started_at"].replace("Z", "+00:00"))
        assert isinstance(data["process_id"], int)
        assert data["process_id"] > 0

    _run(scenario())


def test_card_and_reviews_accept_search_slug_product_url(monkeypatch):
    seen_paths = []

    async def fake_fetch(path, ctx):
        seen_paths.append(path)
        if path.endswith("/reviews/"):
            return 200, _reviews_body(), "fake"
        return 200, json.dumps({"widgetStates": {}}), "fake"

    async def scenario():
        monkeypatch.setattr(server, "_fetch_composer", fake_fetch)

        card = (await server.ozon_card("https://www.ozon.ru/product/example-name-1576326039/")).model_dump()
        reviews = (
            await server.ozon_reviews(
                "https://www.ozon.ru/product/example-name-1576326039/",
                limit=1,
            )
        ).model_dump()

        assert card["status"] == "success"
        assert card["url"] == "https://www.ozon.ru/product/1576326039/"
        assert reviews["status"] == "success"
        assert reviews["url"] == "https://www.ozon.ru/product/1576326039/reviews/"
        assert seen_paths == ["/product/1576326039/", "/product/1576326039/reviews/"]

    _run(scenario())


def test_ozon_tools_reject_non_object_payloads(monkeypatch):
    async def fake_fetch(path, ctx):
        return 200, "[]", "fake"

    async def scenario():
        monkeypatch.setattr(server, "_fetch_composer", fake_fetch)

        for call in (
            server.ozon_card("123"),
            server.ozon_reviews("123", limit=1),
            server.ozon_search("сетевой фильтр"),
        ):
            with pytest.raises(ToolError) as excinfo:
                await call
            assert "parser_drift" in str(excinfo.value)

    _run(scenario())


def test_reviews_marks_partial_when_later_page_fails(monkeypatch):
    calls = []
    first_page_review = {
        "uuid": "r1",
        "content": {"score": 5, "comment": "good"},
        "author": {"firstName": "Ann"},
        "usefulness": {"useful": 1},
        "publishedAt": 0,
    }

    async def fake_fetch(path, ctx):
        calls.append(path)
        if len(calls) == 1:
            return (
                200,
                _reviews_body(
                    reviews=[first_page_review],
                    next_button="?page=2&page_key=abc&sort=published_at_desc",
                ),
                "fake",
            )
        return 500, "upstream failed", "fake"

    async def scenario():
        monkeypatch.setattr(server, "_fetch_composer", fake_fetch)

        result = await server.ozon_reviews("123", limit=5)
        data = result.model_dump()

        assert data["status"] == "success"
        assert data["returned"] == 1
        assert data["partial"] is True
        assert data["requested_limit"] == 5
        assert data["stop_reason"] == "http"
        assert data["last_error"]["code"] == 500

    _run(scenario())


def test_reviews_tolerates_malformed_uuid_next_button_and_score(monkeypatch):
    body = json.dumps(
        {
            "widgetStates": {
                "webListReviews-1": json.dumps(
                    {
                        "paging": {"total": 1, "nextButton": {"bad": "shape"}},
                        "reviews": [
                            {"uuid": ["not-hashable"], "content": {"score": 5, "comment": "ok"}},
                        ],
                    }
                ),
                "webReviewProductScore-1": json.dumps(
                    {
                        "totalScore": 4.7,
                        "reviewsCount": 1,
                        "score": 1,
                    }
                ),
            },
        }
    )

    async def fake_fetch(path, ctx):
        return 200, body, "fake"

    async def scenario():
        monkeypatch.setattr(server, "_fetch_composer", fake_fetch)
        result = await server.ozon_reviews("123", limit=5)
        data = result.model_dump()
        assert data["status"] == "success"
        assert data["returned"] == 1
        assert data["partial"] is True
        assert data["stop_reason"] == "invalid_next_button"
        assert data["distribution"] == {}

    _run(scenario())
