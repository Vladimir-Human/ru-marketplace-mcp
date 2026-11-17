"""The reference half of a tool description lives in resources, not in tools/list.

Each tool's field list and error mapping is reference text the agent reads only
while parsing one specific answer, while ``tools/list`` is paid on every
request. These tests pin the module that moves it: one guide per tool, one
shared error table for the whole family, and a URI scheme that stays unique when
a server mounts other servers.
"""

# START_MODULE_CONTRACT
# PURPOSE: Prove guide registration and static metadata isolation from hostile marketplace output.
# SCOPE: Existing resource unit apps; real registry metadata and fixture-backed public Avito output offline.
# DEPENDS: pytest,FastMCP,httpx,curl_cffi; stdlib asyncio,copy,importlib,json,socket,pathlib;
#          mcp_core.resources/sources/cache/testing/transport.
# LINKS: M-RESOURCES; M-CAPTURE-MUTATION; R-TOOL-RESOURCES; R-CAPTURE-MUTATION.static-metadata.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_*: Existing guide/registration contracts.
# _static_metadata_snapshot/_assert_static_metadata: Complete listed descriptions/schemas and guide resources.
# test_registered_metadata_is_static_offline_after_hostile_marketplace_output: Real fixture output, network blocked.
# test_static_metadata_oracle_rejects_planted_marketplace_text: Description/schema/guide contamination controls.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import socket
from pathlib import Path

import httpx
import pytest
from curl_cffi import requests as cffi
from fastmcp import Client, FastMCP
from mcp_core.cache import TTLCache
from mcp_core.errors import ErrorCode
from mcp_core.resources import (
    ERROR_REFERENCE_URI,
    GUIDES,
    guide_pointer,
    guide_uri,
    mount_child,
    register_reference_resources,
    render_error_reference,
    tool_has_guide,
)
from mcp_core.sources import BY_NAME, SOURCES
from mcp_core.testing.capture_mutation import preserving_case, seller_attack
from mcp_core.transport import chrome_cdp

REFERENCE = """## Return Format

WbCardResponse: {dest, count, items, meta}. price_rub is None — never 0.

## Error Format

ToolError: BadRequestError on malformed nm_ids."""


def _wb_app(name: str = "unittest") -> FastMCP:
    """A server carrying one tool whose name has a guide in ``GUIDES``."""
    app = FastMCP(name)

    @app.tool(name="wb_card")
    async def wb_card() -> dict[str, object]:
        """Fetch product card data from WB v4 API.

        Returns prices in rubles for up to 100 SKUs.

        Full field list and error codes: `guide://wb/wb_card`.
        """
        return {}

    return app


def _bare_app(tool_name: str) -> FastMCP:
    app = FastMCP("bare")

    async def body() -> dict[str, object]:
        return {}

    body.__name__ = tool_name
    body.__doc__ = f"Do a {tool_name} thing.\n\nFull field list and error codes: `{guide_uri(tool_name)}`."
    app.tool(name=tool_name)(body)
    return app


@pytest.mark.req("R-TOOL-RESOURCES")
def test_error_reference_lists_every_code_of_the_enum():
    text = render_error_reference()

    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert len(rows) == len(ErrorCode), rows
    for code in ErrorCode:
        assert f"`{code.value}`" in text, code


@pytest.mark.req("R-TOOL-RESOURCES")
def test_error_reference_marks_retryability_from_the_enum():
    text = render_error_reference()
    rows = {line.split("`")[1]: line for line in text.splitlines() if line.startswith("| `")}

    for code in ErrorCode:
        expected = "| yes |" if code.retryable else "| no |"
        assert expected in rows[code.value], (code, rows[code.value])


@pytest.mark.req("R-TOOL-RESOURCES")
def test_guide_carries_both_reference_sections_for_the_tool():
    """The guide is the reference half: both headings, for the tool it names."""
    app = _wb_app()
    register_reference_resources(app)

    async def read() -> str:
        async with Client(app) as client:
            return (await client.read_resource(guide_uri("wb_card")))[0].text

    contents = asyncio.run(read())

    assert contents.startswith("# wb_card\n\n## Return Format\n")
    assert "## Error Format" in contents
    assert contents.endswith("\n")
    assert GUIDES["wb_card"] in contents


@pytest.mark.req("R-TOOL-RESOURCES")
def test_a_tool_without_a_guide_entry_serves_no_guide():
    """Registration follows the GUIDES table: no entry, no resource, no stub.

    The tool here is deliberately named after nothing in the table, so the test
    keeps its meaning when a real tool of the same name is added.
    """
    app = _bare_app("nowhere_tool")
    assert not tool_has_guide("nowhere_tool")

    assert register_reference_resources(app) == 1  # the shared error table only

    async def list_uris() -> list[str]:
        async with Client(app) as client:
            return [str(item.uri) for item in await client.list_resources()]

    assert asyncio.run(list_uris()) == [ERROR_REFERENCE_URI]


@pytest.mark.req("R-TOOL-RESOURCES")
def test_guide_pointer_is_the_line_the_docstring_carries():
    app = _wb_app()
    tool = asyncio.run(app.list_tools())[0]

    assert guide_pointer(tool.name) in (tool.description or "")
    assert guide_pointer("wb_card") == "Full field list and error codes: `guide://wb/wb_card`."
    assert guide_uri("compare_prices") == "guide://compare/compare_prices"


@pytest.mark.req("R-TOOL-RESOURCES")
def test_registration_is_idempotent():
    app = _wb_app()

    assert register_reference_resources(app) >= 2
    assert register_reference_resources(app) == 0


@pytest.mark.req("R-TOOL-RESOURCES")
def test_every_guide_is_readable_through_an_in_process_client():
    app = _wb_app()

    async def read_all() -> dict[str, str]:
        register_reference_resources(app)
        async with Client(app) as client:
            listed = await client.list_resources()
            assert [str(item.uri) for item in listed].count(ERROR_REFERENCE_URI) == 1
            return {str(item.uri): (await client.read_resource(str(item.uri)))[0].text for item in listed}

    texts = asyncio.run(read_all())

    assert set(texts) == {guide_uri("wb_card"), ERROR_REFERENCE_URI}
    assert texts[ERROR_REFERENCE_URI].startswith("# Marketplace error reference")
    assert texts[guide_uri("wb_card")].startswith("# wb_card")


@pytest.mark.req("R-TOOL-RESOURCES")
def test_mounting_children_keeps_one_owner_of_the_shared_error_uri():
    parent = FastMCP("parent")
    children = [_bare_app("wb_card"), _bare_app("ozon_card")]
    for child in children:
        register_reference_resources(child)
        mount_child(parent, child)
    register_reference_resources(parent)

    async def collect(app: FastMCP) -> list[str]:
        async with Client(app) as client:
            return [str(item.uri) for item in await client.list_resources()]

    parent_uris = asyncio.run(collect(parent))
    assert parent_uris.count(ERROR_REFERENCE_URI) == 1, parent_uris
    assert set(parent_uris) == {ERROR_REFERENCE_URI, guide_uri("wb_card"), guide_uri("ozon_card")}
    # The children keep their own copy for standalone use: mounting hides it from
    # the parent's listing, it does not strip the child.
    for child, tool_name in zip(children, ("wb_card", "ozon_card"), strict=True):
        child_uris = asyncio.run(collect(child))
        assert set(child_uris) == {ERROR_REFERENCE_URI, guide_uri(tool_name)}


# START_BLOCK_STATIC_METADATA_MUTATION_GATE


def _block_metadata_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def blocked(*args, **kwargs):
        calls.append("network")
        raise AssertionError("static metadata must not contact a marketplace or browser")

    async def async_blocked(*args, **kwargs):
        return blocked(*args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    for client in (httpx.Client, httpx.AsyncClient):
        monkeypatch.setattr(client, "send", async_blocked if client is httpx.AsyncClient else blocked)
    monkeypatch.setattr(cffi.Session, "request", blocked)
    monkeypatch.setattr(cffi.AsyncSession, "request", async_blocked)
    monkeypatch.setattr(chrome_cdp, "open_page", async_blocked)
    monkeypatch.setattr(chrome_cdp, "open_owned_popup", async_blocked)
    return calls


async def _static_metadata_snapshot() -> dict:
    modules = {spec.module for spec in SOURCES} | {"compare_connector.decision_server", "marketplace_connector.server"}
    snapshot = {}
    for name in sorted(modules):
        module = importlib.import_module(name)
        async with Client(module.mcp) as client:
            tools = await client.list_tools()
            assert tools, name
            guides = {}
            for resource in await client.list_resources():
                uri = str(resource.uri)
                if uri.startswith("guide://") or uri == ERROR_REFERENCE_URI:
                    guides[uri] = [content.text for content in await client.read_resource(uri)]
            assert guides, name
            snapshot[name] = {
                "tools": [tool.model_dump(by_alias=True) for tool in tools],
                "guides": guides,
            }
    return snapshot


def _assert_static_metadata(clean: dict, network_off: dict, marketplace_texts: tuple[str, ...]) -> None:
    assert clean == network_off, "tool descriptions, schemas or guides changed with network off/seller output"
    serialized = json.dumps(network_off, ensure_ascii=False)
    for text in marketplace_texts:
        assert text not in serialized, f"marketplace text contaminated static metadata: {text!r}"


@pytest.mark.req("R-CAPTURE-MUTATION.static-metadata")
async def test_registered_metadata_is_static_offline_after_hostile_marketplace_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _block_metadata_network(monkeypatch)
    clean = await _static_metadata_snapshot()
    source = importlib.import_module(BY_NAME["avito"].module)
    root = Path(__file__).resolve().parents[3]
    raw = (root / "packages/avito-connector/tests/fixtures/js_items_live.json").read_text(encoding="utf-8")
    rows, _total = source._parse_search_items(json.loads(raw))
    titles = tuple(row["title"] for row in rows if row.get("title"))
    attack = seller_attack(0)
    mutated = preserving_case(raw, "m6a", 0, kind="json", text_paths=(r"^catalog\.items\[\]\.title$",))
    fetches = []

    async def fixture_fetch(url: str, ctx=None):
        fetches.append(url)
        return 200, mutated, "fixture"

    monkeypatch.setattr(source, "_cache", TTLCache(ttl_s=300, max_entries=16))
    monkeypatch.setattr(source, "_fetch", fixture_fetch)
    async with Client(source.mcp) as client:
        reply = await client.call_tool("avito_search", {"query": "p21-static-metadata"})
    assert fetches and "P21_FAKE" in json.dumps(reply.structured_content, ensure_ascii=False)
    offline = await _static_metadata_snapshot()
    _assert_static_metadata(clean, offline, (*titles, attack, "P21_FAKE", "attacker.invalid/p21-m6"))
    assert calls == [], "metadata or the fixture-backed public call escaped the offline transport"


@pytest.mark.req("R-CAPTURE-MUTATION.static-metadata")
@pytest.mark.parametrize("surface", ("description", "schema", "guide", "stable-but-tainted"))
async def test_static_metadata_oracle_rejects_planted_marketplace_text(
    surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _block_metadata_network(monkeypatch)
    clean = await _static_metadata_snapshot()
    broken = copy.deepcopy(clean)
    module = next(iter(broken.values()))
    text = "P21_PLANTED_MARKETPLACE_TEXT"
    if surface == "description":
        module["tools"][0]["description"] = text
    elif surface == "schema":
        module["tools"][0]["inputSchema"]["description"] = text
    else:
        module["guides"][next(iter(module["guides"]))] = [text]
    _assert_static_metadata(clean, clean, (text,))
    with pytest.raises(AssertionError, match=r"changed with network off|contaminated static metadata"):
        _assert_static_metadata(broken if surface == "stable-but-tainted" else clean, broken, (text,))
    assert calls == []


# END_BLOCK_STATIC_METADATA_MUTATION_GATE
