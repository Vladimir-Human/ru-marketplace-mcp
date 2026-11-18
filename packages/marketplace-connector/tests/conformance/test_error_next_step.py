"""Every listed tool's connector errors name a usable next call, without network I/O."""
# START_MODULE_CONTRACT
# PURPOSE: Prove R-ERROR-NEXT-STEP through mounted servers and the public MCP client.
# SCOPE: All ErrorCode members crossed with the runtime unified/decision inventory;
#        fake error tool bodies plus one captured Lamoda refusal through its real search tool.
# DEPENDS: pytest, FastMCP, workspace servers, mcp_core.errors/runtime/sources/resources.
# LINKS: M-ERRORS; M-RUNTIME; M-SOURCES; R-ERROR-NEXT-STEP.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# error_server: Build a mounted error shell from the real servers' runtime tool inventory.
# real_schema_server: Mount the shipped compare and Avito servers with their real schemas.
# test_every_code_and_listed_tool_names_a_next_call: Cross the taxonomy with every listed tool.
# test_retry_header_and_refusal_policies: Preserve delays and the Citilink no-retry exception.
# test_old_drift_starts_with_upgrade: Keep A6's conditional upgrade ahead of health guidance.
# test_a_block_that_needs_a_person_gets_no_invented_delay: A 403 transport_down waits for a person.
# test_a_block_keeps_the_delay_its_source_supplied: A supplied delay stays and is quoted as a wait.
# test_bad_request_names_only_the_parameters_the_message_names: Name the real schemas' parameters exactly.
# test_lamoda_fixture_public_rate_limit: Exercise the real public tool with captured refusal markup.
# END_MODULE_MAP

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from mcp_core import runtime
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.errors import ChallengeRequiredError, ConnectorError, ErrorCode, raise_tool_error_from
from mcp_core.resources import mount_child
from mcp_core.sources import SOURCES

# START_BLOCK_ERROR_SERVER


def _install(server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    if hasattr(runtime, "install_tool_error_middleware"):
        runtime.install_tool_error_middleware(server)
    else:
        # Old-module red proof: install the old boundary through its old launch
        # path, with only serving suppressed; none of the error assertions change.
        monkeypatch.setattr(server, "run", lambda **kwargs: None)
        monkeypatch.setenv("MCP_TRANSPORT", "stdio")
        runtime.run_server(server, server_name="error-conformance")


@pytest.fixture
async def error_server(monkeypatch: pytest.MonkeyPatch):
    marketplace = importlib.import_module("marketplace_connector.server")
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    inventory = FastMCP("complete-inventory")
    monkeypatch.setattr(marketplace, "mcp", inventory)
    for attr, value in (("_MOUNTED", []), ("_SKIPPED", {}), ("_MOUNTED_MODULES", {})):
        monkeypatch.setattr(marketplace, attr, value)
    marketplace._mount_all()
    assert not marketplace._SKIPPED, marketplace._SKIPPED
    inventory.add_tool(marketplace.marketplace_sources)
    inventory.add_tool(marketplace.marketplace_health)
    inventory.add_tool(importlib.import_module("compare_connector.decision_server").decision_inspect)
    async with Client(inventory) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names
    state: dict[str, Any] = {
        "code": ErrorCode.RATE_LIMITED,
        "provider": None,
        "delay": None,
        "old": False,
        "status_code": None,
    }
    monkeypatch.setattr(
        "mcp_core.errors.old_build_info",
        lambda: (
            {
                "version": "2.9.4",
                "released": "2026-10-01",
                "age_days": 40,
                "upgrade": "if a newer version exists: uvx ru-marketplace-mcp@latest (or: pip install -U ru-marketplace-mcp)",
            }
            if state["old"]
            else None
        ),
    )
    child = FastMCP("error-bodies")

    def fail(query: str = "fixture") -> str:
        code = state["code"]
        err = (
            ChallengeRequiredError("human challenge", provider=state["provider"])
            if code == ErrorCode.CHALLENGE_REQUIRED
            else ConnectorError(
                code,
                "query must be valid",
                provider=state["provider"],
                retry_after_s=state["delay"],
                status_code=state["status_code"],
            )
        )
        raise_tool_error_from(err)

    for name in sorted(names):
        child.tool(name=name)(fail)
    server = FastMCP("error-conformance")
    server.mount(child)
    _install(server, monkeypatch)
    yield server, names, state


def _payload(result: Any) -> dict[str, Any]:
    assert result.is_error
    return json.loads(result.content[0].text)


# END_BLOCK_ERROR_SERVER
# START_BLOCK_REGISTRY_CONFORMANCE


@pytest.mark.req("R-ERROR-NEXT-STEP.boundary", "R-ERROR-NEXT-STEP.guidance", "R-ERROR-NEXT-STEP.pacing")
@pytest.mark.parametrize("code", list(ErrorCode), ids=lambda code: code.value)
async def test_every_code_and_listed_tool_names_a_next_call(error_server, code: ErrorCode) -> None:
    server, names, state = error_server
    state["code"] = code
    async with Client(server) as client:
        for name in sorted(names):
            source = next((s for s in SOURCES if s.tool_prefix and name.startswith(s.tool_prefix)), None)
            state["provider"] = source.name if source else None
            error = _payload(await client.call_tool(name, {}, raise_on_error=False))
            assert error["error"] == code.value
            assert error["retryable"] == code.retryable
            assert error["tool"] == name, (code, name, error)
            step = error["next_step"]
            assert isinstance(step, str) and step.strip(), (code, name, error)
            assert step.endswith(".") and not re.search(r"\.\s+[A-Z]", step), step
            named_tools = set(re.findall(r"`([a-z][a-z0-9_]+)`", step))
            # Settings and fields are not tools. Call positions are extracted
            # separately; this test never silently discards a misspelled call.
            calls = set(re.findall(r"(?:call|retry) `([a-z][a-z0-9_]+)`", step, flags=re.I))
            calls |= named_tools & names
            assert named_tools - {"user_action"} <= names, (name, step, named_tools - names)
            assert calls and calls <= names, (name, step, calls - names)
            if code in {ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.TRANSPORT_DOWN}:
                assert error["retry_after_s"] >= 0
                if not (code == ErrorCode.RATE_LIMITED and source and not source.retry_rate_limit):
                    assert f"after {error['retry_after_s']:g} seconds" in step
                    assert f"`{name}` again with the same arguments" in step
            if code == ErrorCode.PARSER_DRIFT:
                assert step == (
                    "Do not retry; call `marketplace_health` for the source state; "
                    "this source is unavailable until a fix."
                )
            if code == ErrorCode.NOT_FOUND:
                if source and source.listing_tool:
                    assert step == f"Check the id or URL; call `{source.listing_tool}` to find the item."
                else:
                    assert "Check the id or URL" in step and "call `compare_prices`" in step
            if code == ErrorCode.PERMISSION_DENIED:
                assert "query must be valid" in step and "retry will not help" in step
            if code == ErrorCode.AUTH_MISSING:
                assert "Ask the user" in step and "agent must not set keys" in step
            if code == ErrorCode.BAD_REQUEST:
                assert "query" in step
            if code == ErrorCode.CHALLENGE_REQUIRED:
                assert error["retryable_now"] is False
                assert error["retry_when"] == "after_user_confirmation"
                assert "user_action" in step and "user confirms" in step
            if code == ErrorCode.NOT_FOUND and source and source.name == "detsky_mir":
                assert "`detmir_category`" in step


@pytest.mark.req("R-ERROR-NEXT-STEP.guidance", "R-ERROR-NEXT-STEP.pacing")
@pytest.mark.parametrize(
    ("provider", "tool", "delay"),
    [
        ("citilink", "citilink_search", 30.0),
        ("lamoda", "lamoda_search", 1200.0),
        ("megamarket", "megamarket_search", 1200.0),
        ("wb", "wb_search", 17.5),
    ],
)
async def test_retry_header_and_refusal_policies(error_server, provider: str, tool: str, delay: float) -> None:
    server, _, state = error_server
    state.update(provider=provider, delay=delay)
    async with Client(server) as client:
        error = _payload(await client.call_tool(tool, {}, raise_on_error=False))
    assert error["retry_after_s"] == delay
    if provider == "citilink":
        assert error["next_step"] == (
            "Do not retry `citilink_search` now; `compare_prices` covers the other sources; tell the user this source is limited."
        )
    else:
        assert error["next_step"] == f"Call `{tool}` again with the same arguments after {delay:g} seconds."


@pytest.mark.req("R-ERROR-NEXT-STEP.guidance")
async def test_old_drift_starts_with_upgrade(error_server) -> None:
    server, _, state = error_server
    state.update(code=ErrorCode.PARSER_DRIFT, old=True)
    async with Client(server) as client:
        error = _payload(await client.call_tool("wb_search", {}, raise_on_error=False))
    assert error["next_step"].startswith("Run `uvx ru-marketplace-mcp@latest` if a newer version exists;")
    assert "Do not retry; call `marketplace_health`" in error["next_step"]
    assert error["old_build"]["age_days"] == 40


@pytest.mark.req("R-ERROR-NEXT-STEP.pacing", "R-ERROR-NEXT-STEP.guidance")
@pytest.mark.parametrize("code", [ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.TRANSPORT_DOWN])
async def test_live_pacing_floor_and_registry_listing(error_server, source, code, monkeypatch) -> None:
    server, names, state = error_server
    if source.kind != "marketplace":
        # Profiles have no upstream pacer; the shared conservative floor is 3s.
        name = "marketplace_health" if source.name == "marketplace" else "compare_prices"
        expected = 3.0
    else:
        if source.listing_tool is not None:
            assert source.listing_tool in names, source.name
            name = source.listing_tool
        else:
            # Analytics has no discovery tool: do not invent one for not_found.
            assert not source.text_search and source.card_tool is None
            name = next(name for name in sorted(names) if name.startswith(source.tool_prefix))
        module = importlib.import_module(source.module)
        assert source.pacing_floor_paths, source.name
        for path in source.pacing_floor_paths:
            owner = module
            attributes = path.split(".")
            for attribute in attributes[:-1]:
                owner = getattr(owner, attribute)
            assert isinstance(getattr(owner, attributes[-1]), (int, float)), (source.name, path)
            monkeypatch.setattr(owner, attributes[-1], 9.25)
        expected = 9.25
    state.update(code=code, provider=source.name)
    async with Client(server) as client:
        error = _payload(await client.call_tool(name, {}, raise_on_error=False))
    assert error["retry_after_s"] == expected, (source.name, error)
    assert error["tool"] == name


@pytest.mark.req("R-ERROR-NEXT-STEP.pacing", "R-ERROR-NEXT-STEP.guidance")
async def test_a_block_that_needs_a_person_gets_no_invented_delay(error_server) -> None:
    """A 403 transport_down waits for a person: no pacing floor is invented."""
    server, _, state = error_server
    state.update(code=ErrorCode.TRANSPORT_DOWN, status_code=403, delay=None)
    async with Client(server) as client:
        error = _payload(await client.call_tool("avito_search", {}, raise_on_error=False))
    assert "retry_after_s" not in error, error
    assert error["next_step"] == (
        "A person must clear the block first, as the message says; then call `avito_search` again with the same arguments."
    )


@pytest.mark.req("R-ERROR-NEXT-STEP.pacing", "R-ERROR-NEXT-STEP.guidance")
async def test_a_block_keeps_the_delay_its_source_supplied(error_server) -> None:
    """A delay the source supplied stays, and is quoted as a wait, not as permission."""
    server, _, state = error_server
    state.update(code=ErrorCode.TRANSPORT_DOWN, status_code=403, delay=1200)
    async with Client(server) as client:
        error = _payload(await client.call_tool("avito_search", {}, raise_on_error=False))
    assert error["retry_after_s"] == 1200
    assert error["next_step"] == (
        "A person must clear the block first, as the message says; "
        "then call `avito_search` again with the same arguments, not before 1200 seconds."
    )


# END_BLOCK_REGISTRY_CONFORMANCE
# START_BLOCK_REAL_SCHEMA_PARAMETERS


@pytest.fixture
async def real_schema_server(monkeypatch: pytest.MonkeyPatch):
    """The shipped compare and Avito servers, so the schemas are the real ones.

    The fake tools of the taxonomy fixture carry one parameter each, which is
    exactly what let a wrong parameter name pass: here every tool keeps the
    schema it ships with, and the middleware reads the names from it.
    """
    compare = importlib.import_module("compare_connector.server")
    avito = importlib.import_module("avito_connector.server")
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    server = FastMCP("real-schema-conformance")
    mount_child(server, compare.mcp)
    mount_child(server, avito.mcp)
    _install(server, monkeypatch)
    async with Client(server) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert {"compare_prices", "compare_verify_offer", "avito_search", "avito_card"} <= names, names
    yield server


def _named_parameters(step: str) -> set[str]:
    """The parameter names a bad-request sentence names; empty for the generic one."""
    match = re.search(r"Correct the (.+?) parameter using", step)
    return {name.strip() for name in match.group(1).split(",")} if match else set()


@pytest.mark.req("R-ERROR-NEXT-STEP.guidance")
@pytest.mark.parametrize(
    ("tool", "arguments", "named", "expected"),
    [
        (
            "compare_prices",
            {"query": "чайник", "sources": ["foo"]},
            set(),
            "Correct the arguments the message describes, using `compare_prices`'s schema, "
            "then call `compare_prices` again.",
        ),
        (
            "avito_search",
            {"query": "x", "location_id": "abc"},
            {"location_id"},
            "Correct the location_id parameter using `avito_search`'s schema, then call `avito_search` again.",
        ),
        (
            "avito_card",
            {"item_id_or_url": "http://[bad"},
            {"item_id_or_url"},
            "Correct the item_id_or_url parameter using `avito_card`'s schema, then call `avito_card` again.",
        ),
        (
            "compare_verify_offer",
            {"source": "ozon", "product_id_or_url": "123", "expected_variant_id": "42"},
            {"expected_variant_id"},
            "Correct the expected_variant_id parameter using `compare_verify_offer`'s schema, "
            "then call `compare_verify_offer` again.",
        ),
    ],
    ids=["compare_prices", "avito_search", "avito_card", "compare_verify_offer"],
)
async def test_bad_request_names_only_the_parameters_the_message_names(
    real_schema_server, tool: str, arguments: dict[str, Any], named: set[str], expected: str
) -> None:
    """The real schemas: name the parameter the message mentions, and no other."""
    async with Client(real_schema_server) as client:
        error = _payload(await client.call_tool(tool, arguments, raise_on_error=False))
    assert error["error"] == "bad_request"
    assert error["next_step"] == expected
    assert _named_parameters(error["next_step"]) == named


# END_BLOCK_REAL_SCHEMA_PARAMETERS
# START_BLOCK_PUBLIC_FIXTURE


@pytest.mark.req("R-ERROR-NEXT-STEP.boundary", "R-ERROR-NEXT-STEP.guidance", "R-ERROR-NEXT-STEP.pacing")
async def test_lamoda_fixture_public_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("lamoda_connector.server")
    fixture = Path(__file__).resolve().parents[3] / "lamoda-connector/tests/fixtures/refusal_page_live.html"
    try:
        payload = run_extractor(
            module._SEARCH_EXTRACT_JS, fixture, page_url="https://www.lamoda.ru/catalogsearch/result/?q=fixture"
        )
    except JsdomUnavailable as exc:
        # jsdom is an optional developer tool (mcp_core.domtest); the jobs that install it run this.
        pytest.skip(str(exc))
    assert payload["items"] == []

    async def fake_render(query: str, ctx: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(module, "_cdp_render_search", fake_render)
    monkeypatch.setattr("mcp_core.errors.old_build_info", lambda: None)
    server = FastMCP("lamoda-fixture-public")
    server.mount(module.mcp)
    _install(server, monkeypatch)
    async with Client(server) as client:
        error = _payload(await client.call_tool("lamoda_search", {"query": "fixture"}, raise_on_error=False))
    assert error["error"] == "rate_limited"
    assert error["tool"] == "lamoda_search"
    assert error["retry_after_s"] == 1200.0
    assert error["next_step"] == "Call `lamoda_search` again with the same arguments after 1200 seconds."
    print("FIXTURE_RATE_LIMIT=" + json.dumps(error, ensure_ascii=False, indent=2))


# END_BLOCK_PUBLIC_FIXTURE
