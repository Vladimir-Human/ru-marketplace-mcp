"""Registry/schema-driven refusal of foreign URLs before any transport call.

START_MODULE_CONTRACT
PURPOSE: Prove R-NAV-HOSTS.invariant through real public tool functions and transport spies.
SCOPE: URL/id-or-URL parameters discovered from current registry tool schemas; offline only.
DEPENDS: pytest; httpx,curl_cffi; mcp_core.sources,output_schema,transport.chrome_cdp.
LINKS: M-SOURCES; M-CHROME-CDP; M-KIT; R-NAV-HOSTS.invariant.
END_MODULE_CONTRACT
START_MODULE_MAP
_url_parameters: Discover URL-capable arguments, not a typed tool/parameter inventory.
_cases: Expand the compare card router over the current card-supported sources.
_transport_spies: Record and block browser and HTTP transport attempts.
test_foreign_url_arguments_never_reach_transport: Exercise hostile authorities and product-shaped paths.
END_MODULE_MAP
"""

from __future__ import annotations

import importlib
import json
import re
from typing import Any

import httpx
import pytest
from curl_cffi import requests as cffi
from fastmcp.exceptions import ToolError
from mcp_core.output_schema import registered_tools
from mcp_core.sources import BY_NAME, SOURCES
from mcp_core.transport import chrome_cdp

# START_BLOCK_SCHEMA_DISCOVERY


def _url_parameters(tool) -> list[str]:
    return [
        name
        for name, schema in tool.parameters.get("properties", {}).items()
        if "url" in name.lower()
        or re.search(r"\b(?:full|direct)\b.*\burl\b", schema.get("description", ""), re.IGNORECASE)
    ]


def _cases():
    for spec in SOURCES:
        module = importlib.import_module(spec.module)
        for tool in registered_tools(module.mcp):
            for parameter in _url_parameters(tool):
                if spec.kind == "profile":
                    # compare_verify_offer routes its URL into each source's real card tool.
                    for target in SOURCES:
                        if target.card_tool:
                            yield module, tool, parameter, target, {"source": target.name}
                else:
                    yield module, tool, parameter, spec, {}


CASES = list(_cases())

# END_BLOCK_SCHEMA_DISCOVERY
# START_BLOCK_TRANSPORT_SPIES


@pytest.fixture
def transport_spies(monkeypatch):
    calls: list[str] = []

    def blocked(*args: Any, **kwargs: Any):
        calls.append(str(args[0]) if args else "transport")
        raise ConnectionError("offline navigation-host spy")

    async def async_blocked(*args: Any, **kwargs: Any):
        return blocked(*args, **kwargs)

    monkeypatch.setattr(chrome_cdp, "open_page", blocked)
    monkeypatch.setattr(chrome_cdp, "open_owned_popup", blocked)
    # Cover early-imported connector bindings as well as kit's lazy import.
    for spec in SOURCES:
        module = importlib.import_module(spec.module)
        for name in ("open_page", "open_owned_popup"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, blocked)
        if hasattr(module, "_polite_wait"):
            monkeypatch.setattr(module, "_polite_wait", async_blocked_wait)
        if hasattr(module, "_run_sync_bounded"):
            # Ozon runs curl in a worker; stop at that HTTP dispatch seam too.
            monkeypatch.setattr(module, "_run_sync_bounded", async_blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_blocked)
    monkeypatch.setattr(httpx.AsyncClient, "stream", blocked)
    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(cffi, "get", blocked)
    monkeypatch.setattr(cffi.Session, "request", blocked)
    monkeypatch.setattr(cffi.AsyncSession, "request", async_blocked)
    return calls


async def async_blocked_wait(*args: Any, **kwargs: Any) -> None:
    """Remove pacing sleeps only; all input validation and error mapping stay real."""


# END_BLOCK_TRANSPORT_SPIES
# START_BLOCK_CONFORMANCE


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize("case", CASES, ids=[f"{t.name}.{p}[{s.name}]" for _m, t, p, s, _a in CASES])
async def test_foreign_url_arguments_never_reach_transport(case, transport_spies):
    _module, tool, parameter, spec, args = case
    # On the old registry use its existing landing URL, so the red run does not
    # fail merely because SourceSpec.hosts has not been implemented yet.
    host = (getattr(spec, "hosts", ()) or (spec.unblock_page or "https://www.wildberries.ru").split("/")[2:3])[0]
    host = host.lstrip(".")
    foreign = [
        "https://example.com/x",
        "http://127.0.0.1:9222/json/version",
        f"https://{host}@example.com/",
        f"https://{host}.example.com/",
        "https://[::1]/x",
        "file:///c:/x",
        "file:///product/123456789/",
        "ftp://example.com/product/123456789/",
        "//example.com/product/123456789/",
    ]
    # Valid-looking paths and query ids must not rescue an off-host authority.
    paths = (
        "/product/123456789/",
        "/p/mp002xm0vy5m/",
        "/item/1005010003103368.html?id=123456789012",
        "/catalog/details/100000123456/",
        "/cian.ru/sale/flat/123456789/",
        "/sale/flat/123456789/",
        "/brands/abc123",
    )
    foreign += [base.rstrip("/") + path for base in foreign for path in paths]
    failures = []
    for url in foreign:
        transport_spies.clear()
        try:
            await tool.fn(**args, **{parameter: url})
        except ToolError as exc:
            error = json.loads(str(exc))
            if error.get("error") not in {"bad_request", "not_found"}:
                failures.append((url, error.get("error"), list(transport_spies)))
        else:
            failures.append((url, "not refused", list(transport_spies)))
        if transport_spies:
            failures.append((url, "transport called", list(transport_spies)))
    assert not failures, f"{tool.name}.{parameter}: {failures}"


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize("case", CASES, ids=[f"{t.name}.{p}[{s.name}]" for _m, t, p, s, _a in CASES])
async def test_valid_source_input_can_reach_the_transport(case, transport_spies):
    """A reject-everything implementation must fail the same conformance suite."""
    _module, tool, parameter, spec, args = case
    host = (getattr(spec, "hosts", ()) or (spec.unblock_page or "https://www.wildberries.ru").split("/")[2:3])[0]
    paths = (
        "/product/123456789/",
        "/p/mp002xm0vy5m/",
        "/item/1005010003103368.html?id=123456789012",
        "/sale/flat/123456789/",
        "/brands/abc123",
        "/catalog/123456789/detail.aspx",
        "/product/index/id/123456789/",
    )
    candidates = [f"https://{host.lstrip('.')}{path}" for path in paths] + ["123456789012"]
    for value in candidates:
        transport_spies.clear()
        try:
            await tool.fn(**args, **{parameter: value})
        except (ToolError, ConnectionError):
            pass
        if transport_spies:
            return
    pytest.fail(f"{tool.name}.{parameter}[{spec.name}] never reached a transport with valid source input")


@pytest.mark.req("R-NAV-HOSTS.invariant")
def test_url_inventory_includes_each_url_capable_source():
    assert CASES
    for spec in SOURCES:
        if spec.kind == "marketplace":
            assert spec.hosts, f"{spec.name}: missing source policy"
            assert all(host and "/" not in host and "*" not in host for host in spec.hosts)
    assert {spec.name for _m, _t, _p, spec, _a in CASES} >= {
        spec.name for spec in SOURCES if spec.kind == "marketplace" and spec.unblock_page
    }
    # The schema discovery must include the non-URL-named Ozon parameters.
    assert {tool.name for _m, tool, _p, spec, _a in CASES if spec == BY_NAME["ozon"]} >= {"ozon_card", "ozon_reviews"}


# END_BLOCK_CONFORMANCE
