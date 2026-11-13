"""Ozon's Windows compatibility is explicit, validated and CDP-only."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from ozon_connector import server
from ozon_connector.settings import OzonSettings
from pydantic import ValidationError


def test_chrome_compat_defaults_to_native(monkeypatch):
    monkeypatch.delenv("OZON_CHROME_COMPAT", raising=False)
    assert OzonSettings(_env_file=None).chrome_compat == "native"


@pytest.mark.parametrize("mode", ["native", "windows"])
def test_chrome_compat_reads_supported_mode_from_environment(monkeypatch, mode):
    monkeypatch.setenv("OZON_CHROME_COMPAT", mode)
    assert OzonSettings(_env_file=None).chrome_compat == mode


@pytest.mark.parametrize("mode", ["", "linux", "WINDOWS", "windows "])
def test_chrome_compat_rejects_unknown_mode(monkeypatch, mode):
    monkeypatch.setenv("OZON_CHROME_COMPAT", mode)
    with pytest.raises(ValidationError, match="chrome_compat"):
        OzonSettings(_env_file=None)


@pytest.mark.parametrize("mode", ["native", "windows"])
async def test_ozon_only_passes_explicit_compat_to_its_owned_cdp_tab(monkeypatch, mode):
    calls = []

    class Page:
        async def evaluate(self, _expression, args):
            calls.append(("fetch", args))
            return json.dumps({"status": 200, "text": '{"widgetStates": {}}'})

    @asynccontextmanager
    async def page(url, **kwargs):
        calls.append(("open", url, kwargs))
        try:
            yield Page()
        finally:
            calls.append(("close",))

    monkeypatch.setattr(server._settings, "chrome_compat", mode)
    monkeypatch.setattr(server, "open_page", page)
    status, body = await server._cdp_fetch_json("https://www.ozon.ru/api/test", None)
    expected = {"wait_ms": 4000}
    if mode == "windows":
        expected["ozon_windows_compat"] = True
    assert calls[0] == ("open", "https://www.ozon.ru/", expected)
    assert calls[-1] == ("close",)
    assert status == 200 and json.loads(body) == {"widgetStates": {}}
    assert not server._cdp_lock.locked()


async def test_ozon_windows_compat_does_not_turn_a_navigation_403_into_success(monkeypatch):
    @asynccontextmanager
    async def blocked_page(url, **kwargs):
        assert url == "https://www.ozon.ru/"
        assert kwargs["ozon_windows_compat"] is True
        raise server.NavBlocked(403, url)
        yield

    monkeypatch.setattr(server._settings, "chrome_compat", "windows")
    monkeypatch.setattr(server, "open_page", blocked_page)
    with pytest.raises(server.NavBlocked) as caught:
        await server._cdp_fetch_json("https://www.ozon.ru/api/test", None)
    assert caught.value.status == 403
    assert not server._cdp_lock.locked()
