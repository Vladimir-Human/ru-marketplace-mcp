"""The reference half of a tool description lives in resources, not in tools/list.

Each tool's field list and error mapping is reference text the agent reads only
while parsing one specific answer, while ``tools/list`` is paid on every
request. These tests pin the module that moves it: one guide per tool, one
shared error table for the whole family, and a URI scheme that stays unique when
a server mounts other servers.
"""

from __future__ import annotations

import asyncio

from fastmcp import Client, FastMCP
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


def test_error_reference_lists_every_code_of_the_enum():
    text = render_error_reference()

    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert len(rows) == len(ErrorCode), rows
    for code in ErrorCode:
        assert f"`{code.value}`" in text, code


def test_error_reference_marks_retryability_from_the_enum():
    text = render_error_reference()
    rows = {line.split("`")[1]: line for line in text.splitlines() if line.startswith("| `")}

    for code in ErrorCode:
        expected = "| yes |" if code.retryable else "| no |"
        assert expected in rows[code.value], (code, rows[code.value])


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


def test_guide_pointer_is_the_line_the_docstring_carries():
    app = _wb_app()
    tool = asyncio.run(app.list_tools())[0]

    assert guide_pointer(tool.name) in (tool.description or "")
    assert guide_pointer("wb_card") == "Full field list and error codes: `guide://wb/wb_card`."
    assert guide_uri("compare_prices") == "guide://compare/compare_prices"


def test_registration_is_idempotent():
    app = _wb_app()

    assert register_reference_resources(app) >= 2
    assert register_reference_resources(app) == 0


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
