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
_fetch_only_cases: Discover card/seller inputs for every registered non-page fetch host.
test_page_policy_keeps_fetch_only_hosts_out: Pin the legacy page policies apart from fetch policies.
test_page_hosts_are_covered_by_fetch_hosts: Check exact and leading-dot policy containment.
test_legacy_subdomain_card_inputs_keep_reaching_transport: Keep the real parsers' mobile/regional inputs.
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
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp_core.kit import url_on_hosts
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
# START_BLOCK_PAGE_HOST_CONFORMANCE

# Page policies read from v2.9.4's Avito URL checks, WB DOM guard and compare's
# numeric-card router. These are requirement oracles, not production fallbacks;
# discovery still exercises every fetch-only host in the current registry.
PAGE_HOST_BASELINES = {
    "avito": ("www.avito.ru", "avito.ru"),
    "wildberries": ("www.wildberries.ru", "wildberries.ru"),
    "detsky_mir": ("www.detmir.ru", "detmir.ru"),
}


def _page_hosts(spec):
    # The unchanged main registry has no page_hosts yet: exercise its actual
    # broad policy in the red run instead of failing with AttributeError.
    return getattr(spec, "page_hosts", spec.hosts)


def _fetch_only_cases():
    compare = importlib.import_module(BY_NAME["compare"].module)
    verify = next(tool for tool in registered_tools(compare.mcp) if tool.name == "compare_verify_offer")
    for spec in SOURCES:
        pages = PAGE_HOST_BASELINES.get(spec.name, _page_hosts(spec))
        module = importlib.import_module(spec.module)
        tools = [
            tool
            for tool in registered_tools(module.mcp)
            if tool.name in {spec.card_tool, f"{spec.tool_prefix}card", f"{spec.tool_prefix}seller"}
        ]
        for policy_host in spec.hosts:
            host = f"attacker{policy_host}" if policy_host.startswith(".") else policy_host
            if url_on_hosts(f"https://{host}/", pages):
                continue
            for tool in tools:
                parameters = (
                    _url_parameters(tool) or [name for name in tool.parameters["properties"] if "id" in name][:1]
                )
                for parameter in parameters:
                    # Numeric/list input is tested through MCP's real schema
                    # validation, not by passing a string to an int-only fn.
                    schema = tool.parameters["properties"][parameter]
                    path = "/brands/abc123" if tool.name.endswith("seller") else "/slug_1234567890"
                    url = f"https://{host}{path}"
                    yield spec, module, tool, {parameter: [url] if schema.get("type") == "array" else url}, host
            if spec.card_tool:
                path = {
                    "wildberries": "/catalog/123456789/detail.aspx",
                    "detsky_mir": "/product/index/id/123456789/",
                }.get(spec.name, "/slug_1234567890")
                yield spec, compare, verify, {"source": spec.name, "product_id_or_url": f"https://{host}{path}"}, host


FETCH_ONLY_CASES = list(_fetch_only_cases())


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize("spec", SOURCES, ids=lambda spec: spec.name)
def test_page_policy_keeps_fetch_only_hosts_out(spec):
    assert _page_hosts(spec) == PAGE_HOST_BASELINES.get(spec.name, spec.hosts)


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize("spec", SOURCES, ids=lambda spec: spec.name)
def test_page_hosts_are_covered_by_fetch_hosts(spec):
    for host in _page_hosts(spec):
        sample = f"page{host}" if host.startswith(".") else host
        assert url_on_hosts(f"https://{sample}/", spec.hosts)
        if host.startswith("."):
            assert any(entry.startswith(".") and host.endswith(entry) for entry in spec.hosts)


@pytest.mark.req("R-NAV-HOSTS.invariant")
@pytest.mark.parametrize(
    "case", FETCH_ONLY_CASES, ids=[f"{s.name}.{t.name}[{h}]" for s, _m, t, _a, h in FETCH_ONLY_CASES]
)
async def test_fetch_only_card_and_seller_hosts_never_reach_navigation(case, transport_spies):
    _spec, module, tool, arguments, _host = case
    async with Client(module.mcp) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool(tool.name, arguments)
    # URL-capable tools must return the real pre-transport refusal. Numeric
    # tools instead reject a URL at the advertised input-schema boundary.
    message = str(caught.value)
    if message.startswith("{"):
        assert json.loads(message)["error"] in {"bad_request", "not_found"}
    else:
        assert "validation" in message.lower()
    assert transport_spies == []


# END_BLOCK_PAGE_HOST_CONFORMANCE
# START_BLOCK_LEGACY_PAGE_INPUTS

# Own-domain links read from the v2.9.4 input parsers, including the two parsers
# (Megamarket and Lamoda) that previously extracted an id without a host guard.
KEPT_PAGE_INPUTS = {
    "ozon": "https://m.ozon.ru/product/123456789/",
    "taobao": "https://h5.m.taobao.com/awp/core/detail.htm?id=123456789012",
    "megamarket": "https://www.megamarket.ru/catalog/details/123456789/",
    "lamoda": "https://m.lamoda.ru/p/mp002xm0vy5m/",
    "dns": "https://spb.dns-shop.ru/product/abcd/",
    "citilink": "https://m.citilink.ru/product/notebook-123456/",
    "aliexpress": "https://m.aliexpress.ru/item/1005010003103368.html",
    "cian": "https://spb.cian.ru/sale/flat/123456789/",
}


@pytest.mark.req("R-NAV-HOSTS.kept-inputs")
@pytest.mark.parametrize(
    "spec", [spec for spec in SOURCES if spec.name in KEPT_PAGE_INPUTS], ids=lambda spec: spec.name
)
async def test_legacy_subdomain_card_inputs_keep_reaching_transport(spec, transport_spies):
    url = KEPT_PAGE_INPUTS[spec.name]
    assert url_on_hosts(url, _page_hosts(spec))
    module = importlib.import_module(spec.module)
    name = spec.card_tool or f"{spec.tool_prefix}card"
    tool = next(tool for tool in registered_tools(module.mcp) if tool.name == name)
    parameter = _url_parameters(tool)[0]
    try:
        await tool.fn(**{parameter: url})
    except (ToolError, ConnectionError):
        pass
    assert transport_spies, f"{name} refused the previously accepted own-domain URL: {url}"


# END_BLOCK_LEGACY_PAGE_INPUTS
