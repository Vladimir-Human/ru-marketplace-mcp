from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from compare_connector import server
from wb_connector.models_output import MetaOut, WbCardItem, WbNoResultsResponse, WbSearchResponse

CASES = [
    ("wildberries", "wb", "WbSearchResponse", "wb_search"),
    ("yandex_market", "yandex", "YandexSearchResponse", "yandex_search"),
    ("ozon", "ozon", "OzonSearchResponse", "ozon_search"),
    ("avito", "avito", "AvitoSearchResponse", "avito_search"),
    ("taobao", "taobao", "TaobaoSearchResponse", "taobao_search"),
    ("megamarket", "megamarket", "MegamarketSearchResponse", "megamarket_search"),
    ("lamoda", "lamoda", "LamodaSearchResponse", "lamoda_search"),
    ("dns", "dns", "DnsSearchResponse", "dns_search"),
    ("citilink", "citilink", "CitilinkSearchResponse", "citilink_search"),
    ("aliexpress", "aliexpress", "AliSearchResponse", "aliexpress_search"),
]


@pytest.mark.parametrize("source,module,model,tool", CASES)
async def test_every_native_adapter_preserves_warnings(monkeypatch, source, module, model, tool):
    native = getattr(importlib.import_module(f"{module}_connector.models_output"), model)()
    native.meta.healthy = False
    native.meta.warnings = ["no_items: native response needs investigation"]

    async def search(**kwargs):
        return native

    monkeypatch.setattr(server, "SOURCES", {source: SimpleNamespace(**{tool: search})})
    result = await server.compare_prices("test", sources=[source])
    assert result.complete is True
    assert result.sources_ok == [source]
    assert result.source_outcomes[0].status == "ok"
    assert result.source_outcomes[0].warnings == native.meta.warnings
    assert f"source_warning:{source}: {native.meta.warnings[0]}" in result.warnings


async def test_warning_does_not_drop_valid_offer_and_is_isolated_per_request(monkeypatch):
    async def search(query, page):
        await asyncio.sleep(0)
        return WbSearchResponse(
            items=[WbCardItem(nm_id=1, name=query, price_rub=100, total_quantity=2, in_stock=True)],
            meta=MetaOut(healthy=False, warnings=[f"fallback: {query}"]),
        )

    monkeypatch.setattr(server, "SOURCES", {"wildberries": SimpleNamespace(wb_search=search)})
    first, second = await asyncio.gather(
        server.compare_prices("first", sources=["wildberries"], in_stock_only=True),
        server.compare_prices("second", sources=["wildberries"], in_stock_only=True),
    )
    for result in (first, second):
        assert result.complete is True
        assert result.cheapest.price_rub == 100
        assert result.source_outcomes[0].warnings == [f"fallback: {result.query}"]
        assert not any("partial:" in w for w in result.warnings)


async def test_no_results_without_meta_is_not_invented_degradation(monkeypatch):
    async def search(**kwargs):
        return WbNoResultsResponse(query="none")

    monkeypatch.setattr(server, "SOURCES", {"wildberries": SimpleNamespace(wb_search=search)})
    result = await server.compare_prices("none", sources=["wildberries"])
    assert result.complete is True
    assert result.source_outcomes[0].warnings == []
    assert not any(w.startswith("source_warning:") for w in result.warnings)


def test_unhealthy_without_reason_gets_explicit_diagnostic():
    batch = server.OfferBatch([], SimpleNamespace(meta={"healthy": False, "warnings": []}))
    assert batch.warnings == ["source_unhealthy: native validation did not pass; no diagnostic supplied"]


def test_warnings_are_bounded_normalized_deduplicated_and_redacted():
    raw = ["a warning" + chr(10) + "continued", "a warning continued", "https://user:secret@example.com/"] + [
        "x" * 600 + str(i) for i in range(20)
    ]
    batch = server.OfferBatch([], SimpleNamespace(meta={"warnings": raw}))
    assert batch.warnings[0] == "a warning continued"
    assert len(batch.warnings) <= 10
    assert all(len(w) <= 500 and chr(10) not in w for w in batch.warnings)
    assert "secret" not in str(batch.warnings)


async def test_coupon_warning_keeps_regular_price_and_does_not_claim_failure(monkeypatch):
    from aliexpress_connector.models_output import AliSearchItemOut, AliSearchResponse

    native = AliSearchResponse(items=[AliSearchItemOut(item_id="42", title="Kettle", price_rub=2000)])
    native.meta.healthy = False
    native.meta.warnings = ["1 tile advertises a coupon price; price_rub carries regular price"]

    async def search(**kwargs):
        return native

    monkeypatch.setattr(server, "SOURCES", {"aliexpress": SimpleNamespace(aliexpress_search=search)})
    result = await server.compare_prices("Kettle", sources=["aliexpress"])
    assert result.complete is True
    assert result.cheapest.price_rub == 2000
    assert result.source_outcomes[0].warnings == native.meta.warnings
    assert any("coupon price" in warning for warning in result.warnings)
