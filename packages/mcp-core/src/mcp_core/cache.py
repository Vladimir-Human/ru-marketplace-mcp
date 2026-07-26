"""In-process TTL cache for idempotent upstream reads.

Marketplace catalog data is read-mostly and an agent conversation tends to hit
the same SKU several times in a row ("what's the price", then "what do reviews
say", then a comparison). Caching those responses for a short window cuts
latency and — more importantly for unofficial endpoints — keeps request volume
low enough to stay under anti-bot radar.

Deliberately in-process and dependency-free: an MCP stdio server is a
single-user process, so a shared cache server would add operational weight for
no gain. Nothing here is persisted, so a restart always re-reads upstream.

Set ``*_CACHE_TTL=0`` (per connector) to disable caching entirely.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.lookups if self.lookups else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "hit_rate": round(self.hit_rate, 3),
        }


class TTLCache[T]:
    """Bounded LRU cache whose entries expire after ``ttl_s`` seconds.

    ``ttl_s <= 0`` disables the cache: every lookup misses and nothing is
    stored, so callers need no conditional logic to turn caching off.
    """

    def __init__(self, *, ttl_s: float, max_entries: int = 256) -> None:
        self._ttl = float(ttl_s)
        self._max = max(1, int(max_entries))
        self._data: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()
        self.stats = CacheStats()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    @property
    def ttl_s(self) -> float:
        return self._ttl

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: Hashable) -> T | None:
        """Return a live value, or ``None`` on miss/expiry."""
        if not self.enabled:
            self.stats.misses += 1
            return None
        entry = self._data.get(key)
        if entry is None:
            self.stats.misses += 1
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del self._data[key]
            self.stats.expirations += 1
            self.stats.misses += 1
            return None
        self._data.move_to_end(key)
        self.stats.hits += 1
        return value

    def set(self, key: Hashable, value: T) -> None:
        if not self.enabled:
            return
        self._data[key] = (time.monotonic() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)
            self.stats.evictions += 1

    def invalidate(self, key: Hashable) -> bool:
        return self._data.pop(key, None) is not None

    def clear(self) -> None:
        self._data.clear()

    def purge_expired(self) -> int:
        now = time.monotonic()
        stale = [k for k, (expires_at, _) in self._data.items() if expires_at <= now]
        for key in stale:
            del self._data[key]
        self.stats.expirations += len(stale)
        return len(stale)

    async def get_or_fetch(self, key: Hashable, factory: Callable[[], Awaitable[T]]) -> T:
        """Return the cached value or await ``factory`` to produce it.

        A per-cache lock collapses concurrent misses for the same key into one
        upstream call, which matters when a tool fans out over several SKUs that
        share a parent resource.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self.get(key)
            if cached is not None:
                return cached
            value = await factory()
            self.set(key, value)
            return value
