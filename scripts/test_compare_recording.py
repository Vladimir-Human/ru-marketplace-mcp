"""Offline regressions for the compare recording boundary.

START_MODULE_CONTRACT
PURPOSE: Prove city-free recordings and honest cancellation through the capture hook.
SCOPE: Synthetic typed search answers, record-time scrubbing and compare timeouts; no live I/O.
DEPENDS: compare_recording, compare_connector.server; connector output models; pytest; stdlib asyncio,json,pathlib,types.
LINKS: M-COMPARE-RECORDING, R-COMPARE-BENCHMARK-REPLAY.record.
START_MODULE_MAP
  test_*: Requirement-derived recording regressions.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from avito_connector.models_output import AvitoSearchItemOut, AvitoSearchResponse
from compare_connector import server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare_recording as recording

# START_BLOCK_RECORDING_REGRESSIONS
# Invented item and region, not a capture or the owner's location.
ITEM_ID = 4612345678
CITY_URL = f"https://www.avito.ru/moskva/bytovaya_tehnika/kettle_{ITEM_ID}?region=moskva#details"


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
async def test_an_avito_city_url_is_scrubbed_before_the_recording_is_written(tmp_path: Path) -> None:
    response = AvitoSearchResponse(
        items=[AvitoSearchItemOut(item_id=ITEM_ID, title="Synthetic kettle", price_rub=100, url=CITY_URL)],
        location_id="650400",
        meta={"healthy": True, "warnings": []},
    )

    async def search(**kwargs):
        return response

    module = SimpleNamespace(avito_search=search)
    captures = tmp_path / "captures"
    recording.capture_sources(captures, {"avito": module})
    assert await module.avito_search(query="kettle") is response
    captured = [json.loads(path.read_text(encoding="utf-8")) for path in captures.glob("*.json")]
    document = recording.assemble_recording("kettle", "2026-11-16T10:00:00Z", captured)
    path = recording.write_recording(tmp_path / "recordings", "city-free", document)
    stored = json.loads(path.read_text(encoding="utf-8"))

    item = stored["sources"]["avito"]["response"]["items"][0]
    assert item["url"] == f"https://www.avito.ru/{ITEM_ID}"
    assert item["item_id"] == ITEM_ID
    assert stored["sources"]["avito"]["response"]["region"] == "R1"
    assert "moskva" not in path.read_text(encoding="utf-8")


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
def test_warnings_are_redacted_and_regions_use_the_payload_placeholder() -> None:
    response = AvitoSearchResponse(
        items=[AvitoSearchItemOut(item_id=ITEM_ID, title="Synthetic kettle", seller_name="ООО Synthetic Shop")],
        location_id="Москва",
        meta={"healthy": True, "warnings": ["region=Москва; Bearer SYNTHETIC_SECRET", "Москва: delivery unknown"]},
    )
    cut = recording.cut_response("avito", response)
    document = recording.build_recording("kettle", "2026-11-16T10:00:00Z", {"avito": {"response": cut}})
    payload = document["sources"]["avito"]["response"]

    assert payload["meta"]["warnings"] == ["region=R1; Bearer <redacted>", "R1: delivery unknown"]
    assert payload["region"] == "R1"
    assert payload["items"][0]["seller_name"] == "ООО Synthetic Shop"


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
def test_warning_only_regions_are_numbered_in_order_of_appearance() -> None:
    sources = {
        "avito": {
            "response": {
                "items": [],
                "meta": {"healthy": True, "warnings": ["city=Тестоград; region=650400", "Тестоград: delivery unknown"]},
                "region": "650400",
            }
        }
    }
    document = recording.build_recording("kettle", "2026-11-16T10:00:00Z", sources)
    payload = document["sources"]["avito"]["response"]

    assert payload["meta"]["warnings"] == ["city=R1; region=R2", "R1: delivery unknown"]
    assert payload["region"] == "R2"


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
@pytest.mark.parametrize("failure", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
async def test_control_flow_exceptions_write_no_capture_and_still_raise(tmp_path: Path, failure: type) -> None:
    async def search(**kwargs):
        raise failure()

    module = SimpleNamespace(avito_search=search)
    recording.capture_sources(tmp_path, {"avito": module})
    with pytest.raises(failure):
        await module.avito_search(query="kettle")
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
async def test_a_compare_timeout_records_the_outcomes_timeout_not_a_cancelled_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = asyncio.Event()

    async def search(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    module = SimpleNamespace(avito_search=search)
    recording.capture_sources(tmp_path, {"avito": module})
    monkeypatch.setattr(server, "SOURCES", {"avito": module})
    monkeypatch.setattr(server, "SOURCE_TIMEOUT_S", 0.01)
    result = await server.compare_prices("kettle", sources=["avito"])
    outcomes = [outcome.model_dump() for outcome in result.source_outcomes]
    captures = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    document = recording.assemble_recording("kettle", "2026-11-16T10:00:00Z", captures, outcomes)

    assert cancelled.is_set()
    assert outcomes[0]["status"] == "timeout"
    assert document["sources"]["avito"]["error"]["code"] == "timeout"
    assert captures == []


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
@pytest.mark.parametrize(
    ("source", "url", "expected"),
    [
        (
            "yandex_market",
            "https://market.yandex.ru/card/x/123?lr=650400#details",
            "https://market.yandex.ru/card/x/123",
        ),
        ("ozon", "https://www.ozon.ru/product/kettle-123/?region=650400", "https://www.ozon.ru/product/kettle-123/"),
        (
            "megamarket",
            "https://megamarket.ru/catalog/details/kettle-123/?region=650400",
            "https://megamarket.ru/catalog/details/kettle-123/",
        ),
        (
            "dns",
            "https://www.dns-shop.ru/product/abcd/kettle/?city=650400",
            "https://www.dns-shop.ru/product/abcd/kettle/",
        ),
        (
            "citilink",
            "https://www.citilink.ru/product/kettle-123/?city=650400",
            "https://www.citilink.ru/product/kettle-123/",
        ),
        ("lamoda", "https://www.lamoda.ru/p/sku123/?region=650400", "https://www.lamoda.ru/p/sku123/"),
        (
            "aliexpress",
            "https://aliexpress.ru/item/1005010003103368.html?region=650400",
            "https://aliexpress.ru/item/1005010003103368.html",
        ),
        (
            "taobao",
            "https://item.taobao.com/item.htm?id=123456789012&region=650400",
            "https://item.taobao.com/item.htm?id=123456789012",
        ),
        (
            "taobao",
            "https://detail.tmall.com/item.htm?id=123456789012&region=650400",
            "https://detail.tmall.com/item.htm?id=123456789012",
        ),
    ],
)
def test_recorded_page_urls_drop_region_queries_without_losing_item_identity(source, url, expected) -> None:
    response = SimpleNamespace(items=[SimpleNamespace(url=url)], meta={"healthy": True, "warnings": []})
    cut = recording.cut_response(source, response)
    document = recording.build_recording("kettle", "2026-11-16T10:00:00Z", {source: {"response": cut}})
    assert document["sources"][source]["response"]["items"][0]["url"] == expected


@pytest.mark.req("R-COMPARE-BENCHMARK-REPLAY.record")
@pytest.mark.parametrize("source", ["yandex_market", "ozon", "megamarket", "dns", "aliexpress"])
def test_recorded_image_urls_drop_location_queries_and_keep_rendering_parameters(source: str) -> None:
    image_field = "image" if source == "yandex_market" else "image_url"
    safe_url = "https://cdn.example/kettle.jpg?width=300&sig=synthetic"
    response = SimpleNamespace(
        items=[
            SimpleNamespace(**{image_field: safe_url}),
            SimpleNamespace(**{image_field: safe_url + "&city=moskva&region_id=650400#details"}),
        ],
        meta={"healthy": True, "warnings": []},
    )
    cut = recording.cut_response(source, response)
    document = recording.build_recording("kettle", "2026-11-16T10:00:00Z", {source: {"response": cut}})

    assert [item[image_field] for item in document["sources"][source]["response"]["items"]] == [safe_url, safe_url]


# END_BLOCK_RECORDING_REGRESSIONS
