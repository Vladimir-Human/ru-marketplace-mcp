"""Tests for the in-process TTL cache.

Time is driven through a fake ``monotonic`` rather than ``sleep`` so expiry is
exercised deterministically and the suite stays fast.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp_core.cache import TTLCache


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic clock for the cache module."""

    class Clock:
        def __init__(self) -> None:
            self.now = 1_000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    c = Clock()
    monkeypatch.setattr("mcp_core.cache.time.monotonic", lambda: c.now)
    return c


def test_miss_then_hit():
    cache: TTLCache[str] = TTLCache(ttl_s=60)
    assert cache.get("k") is None
    cache.set("k", "v")
    assert cache.get("k") == "v"
    assert cache.stats.hits == 1
    assert cache.stats.misses == 1


def test_entry_expires_after_ttl(clock):
    cache: TTLCache[str] = TTLCache(ttl_s=10)
    cache.set("k", "v")
    clock.advance(9.9)
    assert cache.get("k") == "v"
    clock.advance(0.2)
    assert cache.get("k") is None
    assert cache.stats.expirations == 1


def test_zero_ttl_disables_caching():
    """ttl_s=0 must make the cache inert, not merely fast-expiring."""
    cache: TTLCache[str] = TTLCache(ttl_s=0)
    assert not cache.enabled
    cache.set("k", "v")
    assert len(cache) == 0
    assert cache.get("k") is None


def test_lru_eviction_keeps_recently_used():
    cache: TTLCache[int] = TTLCache(ttl_s=60, max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # 'a' becomes most-recently-used, so 'b' should go first
    cache.set("c", 3)

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert cache.stats.evictions == 1


def test_invalidate_and_clear():
    cache: TTLCache[str] = TTLCache(ttl_s=60)
    cache.set("k", "v")
    assert cache.invalidate("k") is True
    assert cache.invalidate("k") is False
    cache.set("x", "y")
    cache.clear()
    assert len(cache) == 0


def test_purge_expired_drops_only_stale_entries(clock):
    cache: TTLCache[str] = TTLCache(ttl_s=10)
    cache.set("old", "1")
    clock.advance(11)
    cache.set("new", "2")

    assert cache.purge_expired() == 1
    assert cache.get("new") == "2"


async def test_get_or_fetch_calls_factory_once_per_key():
    cache: TTLCache[str] = TTLCache(ttl_s=60)
    calls = []

    async def factory() -> str:
        calls.append(1)
        return "value"

    assert await cache.get_or_fetch("k", factory) == "value"
    assert await cache.get_or_fetch("k", factory) == "value"
    assert len(calls) == 1


async def test_get_or_fetch_collapses_concurrent_misses():
    """Concurrent misses on one key must produce a single upstream call.

    This is what keeps a fan-out tool (several SKUs sharing a parent resource)
    from multiplying identical requests against an unofficial endpoint.
    """
    cache: TTLCache[str] = TTLCache(ttl_s=60)
    calls = []

    async def slow_factory() -> str:
        calls.append(1)
        await asyncio.sleep(0.02)
        return "value"

    results = await asyncio.gather(*(cache.get_or_fetch("k", slow_factory) for _ in range(5)))

    assert results == ["value"] * 5
    assert len(calls) == 1


async def test_get_or_fetch_bypasses_a_disabled_cache():
    cache: TTLCache[str] = TTLCache(ttl_s=0)
    calls = []

    async def factory() -> str:
        calls.append(1)
        return "v"

    await cache.get_or_fetch("k", factory)
    await cache.get_or_fetch("k", factory)
    assert len(calls) == 2


def test_stats_hit_rate_is_safe_when_empty():
    cache: TTLCache[str] = TTLCache(ttl_s=60)
    assert cache.stats.hit_rate == 0.0
    assert cache.stats.as_dict()["hit_rate"] == 0.0
