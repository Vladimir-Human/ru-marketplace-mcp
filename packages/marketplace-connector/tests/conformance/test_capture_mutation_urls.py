"""P2-1 m6d: recursive URL-output conformance, not the existing input-host gate."""

# START_MODULE_CONTRACT
# PURPOSE: Check page URLs on page_hosts (explicit Tmall exception); images are HTTPS, "" (no image) or null.
# SCOPE: Registry/schema discovery, all existing captured output samples, and explicit planted oracle controls.
#        No marketplace/browser request; empty strings are not null and HTTP is not HTTPS.
# DEPENDS: pytest; FastMCP; stdlib importlib,re,urllib.parse; mcp_core.sources/kit/output_schema;
#          existing fixture-to-transport adapters in test_output_size.
# LINKS: M-CAPTURE-MUTATION; M-SOURCES; R-CAPTURE-MUTATION.output-urls.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _schema_url_names/_inventory: Discover URL/media fields from every registered original output schema.
# assert_output_urls: Walk nested objects/lists and switch to a nested offer's own source policy.
# test_captured_public_outputs_have_https_own_host_urls_or_null: Real parsers and MCP serialization.
# test_*_negative_control: Reject foreign, non-HTTPS, malformed and empty URLs at every discovered field.
# END_MODULE_MAP

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastmcp import Client
from fastmcp.tools import Tool
from mcp_core.kit import url_on_hosts
from mcp_core.output_schema import registered_tools
from mcp_core.sources import BY_NAME, SOURCES, SourceSpec

from .test_output_size import SAMPLES, Sample, build_full_server, install_sample

# START_BLOCK_URL_DISCOVERY
URL_NAME = re.compile(r"url|href|^(?:link|links|image|images|picture|pictures|photo.*)$", re.IGNORECASE)
IMAGE_NAME = re.compile(r"^(?:image|picture|photo)", re.IGNORECASE)
# This is an Avito delivery status enum, not an image or a link.
URL_METADATA_FIELDS = {"photos_status"}
# Taobao search intentionally returns Tmall listing links (models_output.py).
OUTPUT_EXTRA_PAGE_HOSTS = {"taobao": ("detail.tmall.com",)}


def _schema_url_names(schema: Any) -> set[str]:
    names = set()
    if isinstance(schema, dict):
        for name, field in schema.get("properties", {}).items():
            description = field.get("description", "")
            # Mentioning a listing URL in an id/alias description does not make that field a URL.
            if name not in URL_METADATA_FIELDS and (
                URL_NAME.search(name)
                or re.match(r"(?:HTTPS?\s+)?URLs?\b", description, re.I)
                or field.get("format") == "uri"
            ):
                names.add(name)
        for child in schema.values():
            names.update(_schema_url_names(child))
    elif isinstance(schema, list):
        for child in schema:
            names.update(_schema_url_names(child))
    return names


def _inventory() -> dict[str, dict[str, set[str]]]:
    inventory = {}
    for spec in SOURCES:
        tools = registered_tools(importlib.import_module(spec.module).mcp)
        inventory[spec.name] = {
            tool.name: _schema_url_names(Tool.from_function(tool.fn).output_schema) for tool in tools
        }
    return inventory


INVENTORY = _inventory()
URL_FIELDS = {name for tools in INVENTORY.values() for fields in tools.values() for name in fields}
MARKETPLACES = tuple(spec for spec in SOURCES if spec.kind == "marketplace")


# END_BLOCK_URL_DISCOVERY
# START_BLOCK_URL_ORACLE


def _source_in_object(output: dict, source: SourceSpec) -> SourceSpec:
    if source.kind != "profile":
        return source
    name = output.get("source") or output.get("name")
    return BY_NAME.get(name, source) if isinstance(name, str) else source


def _assert_url(value: Any, source: SourceSpec, path: str, image: bool) -> None:
    # Five public models declare image_url: str = "" for a tile without a picture; "" is their "no image".
    if value is None or (image and value == ""):
        return
    assert isinstance(value, str), f"{source.name}:{path}: URL is neither a string nor null: {value!r}"
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise AssertionError(f"{source.name}:{path}: malformed URL {value!r}") from exc
    assert parts.scheme == "https" and parts.hostname and not parts.username and not parts.password, (
        f"{source.name}:{path}: URL must be non-empty HTTPS or null: {value!r}"
    )
    if not image:
        hosts = (source.page_hosts or source.hosts) + OUTPUT_EXTRA_PAGE_HOSTS.get(source.name, ())
        assert url_on_hosts(value, hosts), (
            f"{source.name}:{path}: page URL must be on SourceSpec.page_hosts or null: {value!r}; hosts={hosts!r}"
        )


def assert_output_urls(
    output: Any, source: SourceSpec, path: str = "$", url_field: bool = False, image: bool = False
) -> None:
    if isinstance(output, dict):
        owner = _source_in_object(output, source)
        for name, value in output.items():
            is_url = name not in URL_METADATA_FIELDS and (name in URL_FIELDS or bool(URL_NAME.search(name)))
            # Some sources use photos/images for a numeric count, not URL data.
            if name in {"photos", "images"} and not isinstance(value, (str, list, dict)):
                is_url = False
            assert_output_urls(value, owner, f"{path}.{name}", is_url, image or bool(IMAGE_NAME.search(name)))
    elif isinstance(output, list):
        for index, value in enumerate(output):
            assert_output_urls(value, source, f"{path}[{index}]", url_field, image)
    elif url_field:
        _assert_url(output, source, path, image)


# END_BLOCK_URL_ORACLE
# START_BLOCK_CAPTURED_OUTPUTS


@pytest.mark.req("R-CAPTURE-MUTATION.output-urls")
def test_url_field_inventory_includes_every_registered_source_and_output() -> None:
    assert INVENTORY.keys() == {spec.name for spec in SOURCES}
    for spec in SOURCES:
        tools = registered_tools(importlib.import_module(spec.module).mcp)
        assert INVENTORY[spec.name].keys() == {tool.name for tool in tools}
        if spec.kind == "marketplace" and any(INVENTORY[spec.name].values()):
            assert spec.hosts, spec.name
    assert {"url", "image", "image_url", "picture", "profile_url", "photos"} <= URL_FIELDS
    assert {sample.source for sample in SAMPLES} >= {spec.name for spec in MARKETPLACES if spec.listing_tool}


@pytest.mark.req("R-CAPTURE-MUTATION.output-urls")
@pytest.mark.parametrize(
    "sample",
    SAMPLES,
    ids=lambda sample: f"{sample.source}.{sample.tool}/{sample.fixture}",
)
async def test_captured_public_outputs_have_https_own_host_urls_or_null(
    sample: Sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_full_server(monkeypatch)
    args = install_sample(sample, monkeypatch)
    async with Client(app) as client:
        result = await client.call_tool(sample.tool, args)
    assert isinstance(result.structured_content, dict), "must inspect actual successful MCP output"
    assert_output_urls(result.structured_content, BY_NAME[sample.source])


RECORDINGS = Path(__file__).resolve().parents[4] / "packages/compare-connector/tests/benchmark/recordings"


@pytest.mark.req("R-CAPTURE-MUTATION.output-urls")
@pytest.mark.parametrize(
    "path",
    sorted(RECORDINGS.glob("*.json")),
    ids=lambda path: path.name,
)
async def test_recorded_compare_outputs_have_https_own_host_urls_or_null(path: Path) -> None:
    replay = importlib.import_module("packages.compare-connector.tests.benchmark.replay")
    result = await replay.replay(json.loads(path.read_text(encoding="utf-8")))
    assert_output_urls(result.model_dump(), BY_NAME["compare"])


# END_BLOCK_CAPTURED_OUTPUTS
# START_BLOCK_NEGATIVE_CONTROLS


def _field_cases():
    return [
        (spec, field)
        for spec in MARKETPLACES
        for field in sorted({name for names in INVENTORY[spec.name].values() for name in names})
    ]


@pytest.mark.req("R-CAPTURE-MUTATION.output-urls")
@pytest.mark.parametrize(
    "spec,field", _field_cases(), ids=lambda value: value.name if isinstance(value, SourceSpec) else value
)
def test_url_oracle_rejects_planted_bad_urls_in_every_discovered_field(spec: SourceSpec, field: str) -> None:
    host = (spec.page_hosts or spec.hosts)[0].lstrip(".")
    good = f"https://{host}/p21-control"
    valid = {"result": {"items": [{field: [None, good]}]}}
    assert_output_urls(valid, spec)
    bad: tuple[Any, ...] = (
        f"http://{host}/p21-control",
        f"https://{host}@attacker.invalid/x",
        "//attacker.invalid/x",
        "javascript:alert(1)",
        "https://[broken",
        "https://",
        123,
    )
    if IMAGE_NAME.search(field):
        # Declared gap: SourceSpec has no image-host registry, so foreign CDNs are allowed.
        assert_output_urls({field: "https://cdn.example.invalid/image.jpg"}, spec)
        assert_output_urls({field: ""}, spec)
    else:
        bad += ("", "https://attacker.invalid/x", f"https://{host}.attacker.invalid/x")
    for value in bad:
        with pytest.raises(AssertionError, match="URL"):
            assert_output_urls({"result": {"items": [{field: [None, good, value]}]}}, spec)


@pytest.mark.req("R-CAPTURE-MUTATION.output-urls")
@pytest.mark.parametrize("profile", [spec for spec in SOURCES if spec.kind == "profile"], ids=lambda spec: spec.name)
def test_profile_offer_urls_use_each_nested_offer_source_not_a_union(profile: SourceSpec) -> None:
    left, right = BY_NAME["wildberries"], BY_NAME["yandex_market"]
    url = f"https://{(left.page_hosts or left.hosts)[0].lstrip('.')}/p21-control"
    assert_output_urls({"offers": [{"source": left.name, "url": url}]}, profile)
    with pytest.raises(AssertionError, match="URL"):
        assert_output_urls({"offers": [{"source": right.name, "url": url}]}, profile)


# END_BLOCK_NEGATIVE_CONTROLS
