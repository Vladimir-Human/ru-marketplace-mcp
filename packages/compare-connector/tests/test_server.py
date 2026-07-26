"""Offline tests for cross-marketplace comparison.

Each marketplace's search is stubbed, so these tests exercise the part that only
this connector owns: merging, ranking, and reporting partial failures honestly.
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import pytest
from compare_connector import server
from compare_connector.models_output import MarketOffer
from fastmcp.exceptions import ToolError
from pydantic import ValidationError


def offer(source: str, price: float | None, title: str = "товар", **kwargs) -> MarketOffer:
    return MarketOffer(source=source, title=title, price_rub=price, **kwargs)


def stub_sources(monkeypatch, impls: dict[str, object]):
    """Replace the per-marketplace search implementations.

    ``SOURCES`` is patched alongside so availability checks agree with the stubs.
    """
    monkeypatch.setattr(server, "_SEARCH_IMPLS", impls)
    monkeypatch.setattr(server, "SOURCES", dict.fromkeys(impls, object()))


def error_payload(err: ToolError) -> dict:
    return json.loads(str(err))


# ------------------------------------------------------------------ basics ----


def test_server_version_matches_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == server.SERVER_VERSION


async def test_registered_tools_are_stable():
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert names == {"compare_prices", "compare_sources"}


async def test_detsky_mir_is_not_a_comparison_source():
    """Detsky Mir has no working text search, so it must not join a text comparison.

    Including it would return products unrelated to the query — confidently wrong
    is worse than absent.
    """
    assert "detsky_mir" not in server.SEARCHABLE


# ----------------------------------------------------------------- ranking ----


async def test_offers_are_ranked_cheapest_first(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 1500.0), offer("wildberries", 700.0)]

    async def ya(query, limit):
        return [offer("yandex_market", 1000.0)]

    stub_sources(monkeypatch, {"wildberries": wb, "yandex_market": ya})

    result = await server.compare_prices(query="тест")

    assert [o.price_rub for o in result.offers] == [700.0, 1000.0, 1500.0]
    assert result.cheapest.price_rub == 700.0
    assert result.cheapest.source == "wildberries"
    assert result.price_spread_rub == 800.0
    assert result.complete is True


async def test_subscription_prices_never_win_the_ranking(monkeypatch):
    """A Plus-only price must not be presented as the cheapest available.

    Yandex's subscriber price runs 25-30% below its everyday price; ranking on it
    would fabricate a bargain that non-subscribers cannot buy.
    """

    async def wb(query, limit):
        return [offer("wildberries", 1000.0)]

    async def ya(query, limit):
        return [offer("yandex_market", 1200.0, price_with_subscription_rub=800.0)]

    stub_sources(monkeypatch, {"wildberries": wb, "yandex_market": ya})

    result = await server.compare_prices(query="тест")

    assert result.cheapest.source == "wildberries"
    assert result.cheapest.price_rub == 1000.0
    # The subscriber price is still reported, just not ranked on.
    yandex_offer = next(o for o in result.offers if o.source == "yandex_market")
    assert yandex_offer.price_with_subscription_rub == 800.0


async def test_unpriced_offers_are_kept_at_the_end(monkeypatch):
    """'Found but no price' is information; dropping it would hide stock reality."""

    async def wb(query, limit):
        return [offer("wildberries", None, title="без цены"), offer("wildberries", 500.0)]

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.total_offers == 2
    assert result.offers[0].price_rub == 500.0
    assert result.offers[-1].price_rub is None
    assert result.cheapest.price_rub == 500.0


async def test_spread_is_none_with_a_single_priced_offer(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 500.0)]

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.price_spread_rub is None


# ------------------------------------------------------- partial failures ----


async def test_one_source_failing_does_not_sink_the_comparison(monkeypatch):
    """The core resilience promise: three sources answering still beats nothing."""

    async def wb(query, limit):
        return [offer("wildberries", 500.0)]

    async def ozon(query, limit):
        raise RuntimeError('{"error": "transport_down", "message": "Cloudflare"}')

    stub_sources(monkeypatch, {"wildberries": wb, "ozon": ozon})

    result = await server.compare_prices(query="тест")

    assert result.total_offers == 1
    assert result.complete is False
    assert result.sources_ok == ["wildberries"]
    outcomes = {o.source: o for o in result.source_outcomes}
    assert outcomes["ozon"].status == "blocked"
    assert any("partial" in w for w in result.warnings)


async def test_a_timeout_is_reported_as_a_timeout(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 500.0)]

    async def slow(query, limit):
        await asyncio.sleep(5)
        return []

    stub_sources(monkeypatch, {"wildberries": wb, "yandex_market": slow})
    monkeypatch.setattr(server, "SOURCE_TIMEOUT_S", 0.05)

    result = await server.compare_prices(query="тест")

    outcomes = {o.source: o for o in result.source_outcomes}
    assert outcomes["yandex_market"].status == "timeout"
    assert result.complete is False
    assert result.total_offers == 1


async def test_a_generic_failure_is_reported_as_error_not_blocked(monkeypatch):
    """Anti-bot blocks and ordinary bugs need different responses, so they differ."""

    async def wb(query, limit):
        raise ValueError("unexpected shape in response")

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.source_outcomes[0].status == "error"
    assert result.total_offers == 0
    assert any("no_prices" in w for w in result.warnings)


async def test_sources_report_their_elapsed_time(monkeypatch):
    async def wb(query, limit):
        return [offer("wildberries", 100.0)]

    stub_sources(monkeypatch, {"wildberries": wb})

    result = await server.compare_prices(query="тест", sources=["wildberries"])

    assert result.source_outcomes[0].elapsed_ms >= 0
    assert result.source_outcomes[0].offers_returned == 1


async def test_a_missing_connector_is_distinguished_from_a_block(monkeypatch):
    """'Not installed' needs a different fix than 'refused us', so they differ."""

    async def wb(query, limit):
        return [offer("wildberries", 100.0)]

    monkeypatch.setattr(server, "_SEARCH_IMPLS", {"wildberries": wb, "ozon": wb})
    monkeypatch.setattr(server, "SOURCES", {"wildberries": object()})  # Ozon absent

    result = await server.compare_prices(query="тест", sources=["wildberries", "ozon"])

    outcomes = {o.source: o for o in result.source_outcomes}
    assert outcomes["ozon"].status == "not_installed"
    assert result.complete is False


async def test_sources_run_concurrently(monkeypatch):
    """Serial queries would make a four-source comparison unusably slow."""
    started: list[float] = []

    async def make(delay: float):
        async def impl(query, limit):
            started.append(asyncio.get_running_loop().time())
            await asyncio.sleep(delay)
            return [offer("x", 100.0)]

        return impl

    impls = {
        "wildberries": await make(0.1),
        "yandex_market": await make(0.1),
        "ozon": await make(0.1),
    }
    stub_sources(monkeypatch, impls)

    loop_start = asyncio.get_running_loop().time()
    await server.compare_prices(query="тест")
    elapsed = asyncio.get_running_loop().time() - loop_start

    assert len(started) == 3
    # Concurrent: ~0.1s total rather than ~0.3s serial.
    assert elapsed < 0.25


# -------------------------------------------------------------- validation ----


@pytest.mark.parametrize("bad_query", ["", " ", "x"])
async def test_short_queries_are_rejected(monkeypatch, bad_query):
    """Rejected either by the tool's own check or by pydantic's min_length.

    Both are correct outcomes; what matters is that no marketplace is queried.
    """

    async def fail(query, limit):
        raise AssertionError("a rejected query must never reach a marketplace")

    stub_sources(monkeypatch, {"wildberries": fail})

    with pytest.raises((ToolError, ValidationError)):
        await server.compare_prices(query=bad_query, sources=["wildberries"])


async def test_unknown_source_names_are_rejected(monkeypatch):
    async def wb(query, limit):
        return []

    stub_sources(monkeypatch, {"wildberries": wb})

    with pytest.raises(ToolError) as excinfo:
        await server.compare_prices(query="тест", sources=["aliexpress"])

    payload = error_payload(excinfo.value)
    assert payload["error"] == "bad_request"
    assert "aliexpress" in payload["message"]


async def test_all_requested_sources_missing_is_an_error(monkeypatch):
    """No installed source means the answer would be empty and misleading."""

    async def wb(query, limit):
        return []

    monkeypatch.setattr(server, "_SEARCH_IMPLS", {"wildberries": wb})
    monkeypatch.setattr(server, "SOURCES", {})

    with pytest.raises(ToolError) as excinfo:
        await server.compare_prices(query="тест", sources=["wildberries"])

    assert error_payload(excinfo.value)["error"] == "bad_request"


async def test_per_source_limit_is_passed_through(monkeypatch):
    seen: dict[str, int] = {}

    async def wb(query, limit):
        seen["limit"] = limit
        return []

    stub_sources(monkeypatch, {"wildberries": wb})

    await server.compare_prices(query="тест", per_source_limit=7, sources=["wildberries"])

    assert seen["limit"] == 7


# ----------------------------------------------------------- source report ----


async def test_compare_sources_reports_installed_and_missing(monkeypatch):
    monkeypatch.setattr(server, "SOURCES", {"wildberries": object(), "yandex_market": object()})

    report = await server.compare_sources()

    assert "wildberries" in report["installed"]
    assert "ozon" in report["not_installed"]
    assert report["server_version"] == server.SERVER_VERSION
    assert "source_timeout_s" in report
