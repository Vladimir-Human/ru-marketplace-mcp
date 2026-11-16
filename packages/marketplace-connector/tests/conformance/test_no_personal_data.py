"""Every registered public output schema excludes private identity fields."""
# START_MODULE_CONTRACT
# PURPOSE: Detect personal fields across every registry tool's output models and wire schema.
# SCOPE: Offline schema introspection; explicit, reasoned model-field exceptions only.
# DEPENDS: pytest; FastMCP; mcp_core.output_schema/sources; workspace servers.
# LINKS: M-MARKETPLACE; R-NO-PERSONAL-DATA.conformance.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _fields: Walk all nested schema objects and definitions without following recursive references.
# test_every_output_schema_excludes_personal_fields: Audit typed originals and advertised schemas.
# END_MODULE_MAP

from __future__ import annotations

import importlib
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastmcp.tools import Tool
from mcp_core.output_schema import registered_tools
from mcp_core.sources import SOURCES

# START_BLOCK_SCHEMA_WALK
# Paths are model.field, shared across tools reusing the same output model.
ALLOWLIST = {
    "WbCardItem.name": "Product name, not a person.",
    "WbCardItem.seller_legal_name": "Registered merchant entity, including marketplace-labelled sole traders.",
    "wb_root_info.subj_name": "Product category.",
    "wb_root_info.subj_root_name": "Root product category.",
    "wb_seller.name": "Merchant trading name.",
    "wb_seller.full_name": "Registered merchant entity, including sole traders.",
    "WbCategoryNode.name": "Catalog category.",
    "OzonSellerOut.name": "Marketplace shop name, including sole traders.",
    "AvitoSearchItemOut.seller_name": "Company or /brands/ shop only; pure parser rule nulls private/unknown.",
    "AvitoSellerOut.name": "Company or /brands/ shop only; pure parser rule nulls private/unknown.",
    "AvitoParamOut.name": "Item parameter label.",
    "AvitoMetroOut.name": "Station or landmark.",
    "CianMetroOut.name": "Metro station.",
    "CianSearchItemOut.newbuilding_name": "Residential complex.",
    "cian_card.newbuilding_name": "Residential complex.",
    "CianSearchItemOut.agency_name": "Organisation only; publisher rule nulls private/unknown.",
    "cian_card.agency_name": "Organisation only; publisher rule nulls private/unknown.",
    "CianAgentOut.name": "Organisation only; pure parser rule nulls private/unknown.",
    "CianAgentOut.user_type": "Publisher classification, not a person's identity.",
    "CianSearchItemOut.is_by_homeowner": "Publisher classification boolean.",
    "cian_card.is_by_homeowner": "Publisher classification boolean.",
    "DetmirCategory.full_name": "Catalog category path.",
    "TaobaoSearchItemOut.shop_name": "Marketplace shop name, including sole traders.",
    "taobao_card.shop_name": "Marketplace shop name, including sole traders.",
    "SourceOutcome.user_action": "Human recovery instruction, no identity.",
    "SourceOutcome.requires_user_action": "Recovery classification boolean.",
    # A field named just for the party (a name or a party object) must be reasoned too.
    "avito_card.seller": "AvitoSellerOut: its name follows the company or /brands/ shop rule.",
    "avito_seller.seller": "AvitoSellerOut: its name follows the company or /brands/ shop rule.",
    "cian_card.agent": "CianAgentOut: its name follows the organisation rule.",
    "MarketOffer.seller": "Filled from each source's seller name, which the source's own rule already nulls.",
    "DetmirProduct.vendor": "Marketplace shop selling on Detsky Mir.",
    "MpStatsItem.seller": "WB/Ozon shop in the analytics data.",
    "ozon_card.seller": "OzonSellerOut: marketplace shop.",
    "yandex_card.seller": "Shop of the displayed offer.",
    "YandexProduct.seller": "Shop of the default offer.",
    "WbCardItem.seller_inn": "Merchant taxpayer number WB publishes as consumer law requires; a sole trader's too.",
    "wb_seller.inn": "Merchant taxpayer number WB publishes as consumer law requires; a sole trader's too.",
}
SENSITIVE = re.compile(
    r"user|author|name|phone|e_?mail|contact|reviewer|owner|^(?:seller|vendor|merchant|shop|agent|publisher)$|(?:^|_)inn$",
    re.IGNORECASE,
)


def _fields(schema: Any, model: str = "$output") -> Iterator[str]:
    if isinstance(schema, dict):
        for name in schema.get("properties", {}):
            yield f"{model}.{name}"
        for key, value in schema.items():
            if key in {"$defs", "definitions"}:
                for definition, body in value.items():
                    yield from _fields(body, definition)
            elif key == "properties":
                for name, body in value.items():
                    yield from _fields(body, f"{model}.{name}")
            else:
                yield from _fields(value, model)
    elif isinstance(schema, list):
        for value in schema:
            yield from _fields(value, model)


# END_BLOCK_SCHEMA_WALK
# START_BLOCK_CONFORMANCE
@pytest.mark.req("R-NO-PERSONAL-DATA.conformance")
def test_every_output_schema_excludes_personal_fields() -> None:
    checked: set[str] = set()
    failures: set[str] = set()
    modules = {source.module for source in SOURCES} | {"compare_connector.decision_server"}
    for module_name in sorted(modules):
        tools = registered_tools(importlib.import_module(module_name).mcp)
        assert tools, module_name
        for tool in tools:
            original = Tool.from_function(tool.fn).output_schema
            if original is None:
                assert tool.name in {"compare_browser_snapshot", "compare_verify_offer"}, (
                    f"{module_name}.{tool.name}: unreviewed untyped output"
                )
                # These tools return MCP content (snapshot image / verification
                # text), not output models. Their models_output.py is audited too.
                continue
            # Root paths use the tool name; definitions use their model keys.
            root = tool.name
            for schema in (original, tool.output_schema):
                assert schema, f"{module_name}.{tool.name}: no advertised output schema"
                for path in _fields(schema, root):
                    if SENSITIVE.search(path.rsplit(".", 1)[-1]):
                        checked.add(path)
                        if not ALLOWLIST.get(path):
                            failures.add(f"{module_name}.{tool.name}: {path}")
    assert checked, "No output model fields reached"
    assert not failures, "Personal fields without a reasoned exception:\n" + "\n".join(sorted(failures))
    assert not ALLOWLIST.keys() - checked, f"Stale allowlist entries: {sorted(ALLOWLIST.keys() - checked)}"


# END_BLOCK_CONFORMANCE
