"""Issue #197: compare tools accept the names marketplace_sources shows.

marketplace_sources lists mounted sources by their mount name (``yandex``) and
mcp_core.sources declares aliases (``wb``, ``ym``, ``ali``). An agent that copies
one of those into compare_prices, compare_verify_offer or decision_inspect must
reach the same marketplace as the canonical name, not a 400.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from compare_connector import decision_server, server
from compare_connector.models_output import MarketOffer
from fastmcp.exceptions import ToolError
from mcp_core.sources import SOURCES as SPECS


def _stub_all(monkeypatch, calls: list[str]):
    names = list(server._SEARCH_IMPLS)

    def make(name):
        async def search(query, limit):
            calls.append(name)
            return [MarketOffer(source=name, title="товар", price_rub=100.0)]

        return search

    monkeypatch.setattr(server, "_SEARCH_IMPLS", {name: make(name) for name in names})
    monkeypatch.setattr(server, "SOURCES", dict.fromkeys(names, object()))
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)


# Every alias and mount name the registry declares for a compare-searchable source.
_SPELLINGS = sorted(
    {
        (spelling, spec.name)
        for spec in SPECS
        if spec.name in server._SEARCH_IMPLS
        for spelling in (*spec.aliases, spec.mount)
        if spelling and spelling != spec.name
    }
)


def test_the_registry_declares_the_spellings_from_the_issue():
    assert ("yandex", "yandex_market") in _SPELLINGS
    assert ("ym", "yandex_market") in _SPELLINGS
    assert ("wb", "wildberries") in _SPELLINGS


@pytest.mark.parametrize(("spelling", "canonical"), _SPELLINGS)
async def test_compare_prices_resolves_aliases_and_mount_names(monkeypatch, spelling, canonical):
    calls: list[str] = []
    _stub_all(monkeypatch, calls)

    result = await server.compare_prices(query="тест", sources=[spelling.upper()])

    assert calls == [canonical]
    assert {offer.source for offer in result.offers} == {canonical}


async def test_an_alias_and_its_canonical_name_query_the_source_once(monkeypatch):
    calls: list[str] = []
    _stub_all(monkeypatch, calls)

    await server.compare_prices(query="тест", sources=["wb", "wildberries", " Yandex "])

    assert calls == ["wildberries", "yandex_market"]


async def test_an_unknown_name_is_still_refused(monkeypatch):
    _stub_all(monkeypatch, [])

    with pytest.raises(ToolError) as exc:
        await server.compare_prices(query="тест", sources=["wb", "amazon"])

    payload = json.loads(str(exc.value))
    assert payload["error"] == "bad_request"
    assert "amazon" in payload["message"]


async def test_verify_offer_and_decision_inspect_accept_an_alias(monkeypatch):
    seen: list[list[int]] = []

    async def wb_card(*, nm_ids):
        seen.append(nm_ids)
        return {"items": [{"nm_id": nm_ids[0], "price_rub": 1234}]}

    monkeypatch.setattr(server, "SOURCES", {"wildberries": SimpleNamespace(wb_card=wb_card)})

    verified = await server.compare_verify_offer("WB", "123", expected_price_rub=1234)
    inspected = await decision_server.decision_inspect("wb", "123")

    assert verified["source"] == "wildberries"
    assert verified["price_verification"]["matches"] is True
    assert inspected["source"] == "wildberries"
    assert len(seen) == 2


async def test_yandex_variant_check_accepts_the_mount_name(monkeypatch):
    async def yandex_card(**kwargs):
        return {"sku_id": "777", "price_rub": 10}

    monkeypatch.setattr(server, "SOURCES", {"yandex_market": SimpleNamespace(yandex_card=yandex_card)})

    with pytest.raises(ToolError) as exc:
        await server.compare_verify_offer("yandex", "https://market.yandex.ru/card/x/1", expected_variant_id="888")

    assert "variant mismatch" in json.loads(str(exc.value))["message"]
