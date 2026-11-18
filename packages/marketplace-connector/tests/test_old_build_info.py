"""R-OLD-BUILD-INFO: doctor and a real public tool share the offline age hint."""
# START_MODULE_CONTRACT
# PURPOSE: Prove the operator and public-tool paths expose the same local old-build information.
# SCOPE: Doctor output with faked checks; real MCP tool call with deliberately failing transport; offline.
# DEPENDS: M-CORE,M-MARKETPLACE,M-YANDEX,M-CACHE; pytest,httpx,FastMCP; stdlib datetime,json.
# LINKS: R-OLD-BUILD-INFO.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_doctor_prints_age_info_as_its_first_row_only_for_an_old_build: First-row hint and young-build silence.
# test_public_yandex_tool_transport_failure_carries_age_info_only_when_old: Real error JSON at 10/40 days.
# END_MODULE_MAP

from __future__ import annotations

import datetime as dt
import json

import httpx
import mcp_core
import pytest
from fastmcp import Client
from marketplace_connector import cli
from mcp_core import build_age
from mcp_core.cache import TTLCache

# START_BLOCK_PUBLIC_PATH_PROOFS

UPGRADE = "if a newer version exists: uvx ru-marketplace-mcp@latest (or: pip install -U ru-marketplace-mcp)"


@pytest.mark.req("R-OLD-BUILD-INFO")
@pytest.mark.parametrize("age_days", [10, 30, 40])
def test_doctor_prints_age_info_as_its_first_row_only_for_an_old_build(monkeypatch, capsys, age_days) -> None:
    fixed_today = dt.datetime.now(dt.UTC).date()
    released = (fixed_today - dt.timedelta(days=age_days)).isoformat()
    monkeypatch.setattr(build_age, "_utc_today", lambda: fixed_today)
    monkeypatch.setattr(mcp_core, "__release_date__", released, raising=False)
    monkeypatch.setattr(cli, "_run_selfchecks", lambda only: [("yandex", "success", "ok", {})])
    monkeypatch.setattr(cli, "_health_steps", lambda results: ({}, "offline browser probe"))

    assert cli.main(["doctor", "yandex"]) == 0
    rows = capsys.readouterr().out.splitlines()
    if age_days > 30:
        assert rows[0] == (
            f"info:old_build: version {mcp_core.__version__}, released {released}, age {age_days} days; {UPGRADE}"
        )
        assert rows[1] == "Marketplace connector health (selfcheck):"
    else:
        assert rows[0] == "Marketplace connector health (selfcheck):"
        assert "old_build" not in "\n".join(rows)
        assert UPGRADE not in "\n".join(rows)


@pytest.mark.req("R-OLD-BUILD-INFO")
@pytest.mark.parametrize("age_days", [10, 40])
async def test_public_yandex_tool_transport_failure_carries_age_info_only_when_old(monkeypatch, age_days) -> None:
    from yandex_connector import server

    fixed_today = dt.datetime.now(dt.UTC).date()
    released = (fixed_today - dt.timedelta(days=age_days)).isoformat()
    monkeypatch.setattr(build_age, "_utc_today", lambda: fixed_today)
    monkeypatch.setattr(mcp_core, "__release_date__", released, raising=False)
    requests = []

    def fail_transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("deliberate offline failure", request=request)

    monkeypatch.setattr(
        server, "build_client", lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(fail_transport))
    )
    monkeypatch.setattr(server._settings, "net_retries", 0)
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=60, max_entries=1))

    async with Client(server.mcp) as client:
        result = await client.call_tool("yandex_search", {"query": "offline age test"}, raise_on_error=False)
    assert result.is_error is True
    assert result.structured_content is None
    assert len(requests) == 1, "the real tool must reach the deliberately failing transport exactly once"
    payload = json.loads(result.content[0].text)
    assert payload["error"] == "transport_down"
    assert payload["provider"] == "yandex"
    assert "deliberate offline failure" in payload["message"]
    if age_days > 30:
        assert payload["old_build"] == {
            "version": mcp_core.__version__,
            "released": released,
            "age_days": age_days,
            "upgrade": UPGRADE,
        }
    else:
        assert "old_build" not in payload


# END_BLOCK_PUBLIC_PATH_PROOFS
