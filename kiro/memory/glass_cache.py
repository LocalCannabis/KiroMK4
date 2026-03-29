"""
kiro.memory.glass_cache — LRU in-memory cache of materialized glass vectors.

Vectors are NEVER persisted. This cache holds rebuilt glass vectors so that
hot glasses don't need to be reconstructed from facts + seed on every query.
Glasses are evicted after config-driven idle time or when the cache exceeds
a max size, whichever comes first.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger("kiro.memory.glass_cache")


@dataclass
class CacheEntry:
    """One cached glass vector plus metadata."""

    glass_id: int
    vector: np.ndarray       # complex128 HRR glass vector
    fact_count: int           # how many facts are in this vector
    built_at: float           # time.monotonic() when vector was built
    last_hit: float           # time.monotonic() of last access
    hits: int = 0             # access counter

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.built_at

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_hit


class GlassCache:
    """
    LRU cache for materialized glass vectors.

    Usage:
        cache = GlassCache(max_glasses=100, eviction_minutes=30)
        cache.put(42, vector, fact_count=15)
        entry = cache.get(42)  # returns CacheEntry or None
        cache.invalidate(42)   # force eviction after fact mutation

    Thread-safety: NOT thread-safe. If used from multiple threads, wrap
    calls with a threading.Lock (or use the GlassManager which does this).
    """

    def __init__(
        self,
        max_glasses: int = 100,
        eviction_minutes: float = 30.0,
    ) -> None:
        self._max = max_glasses
        self._eviction_s = eviction_minutes * 60.0
        self._store: OrderedDict[int, CacheEntry] = OrderedDict()

    @property
    def size(self) -> int:
        return len(self._store)

    def get(self, glass_id: int) -> Optional[CacheEntry]:
        """
        Fetch a cached glass vector. Returns None if not cached or expired.
        Moves the entry to most-recently-used on hit.
        """
        entry = self._store.get(glass_id)
        if entry is None:
            return None

        # Check idle expiry
        if entry.idle_seconds > self._eviction_s:
            self._store.pop(glass_id, None)
            logger.debug("Glass %d evicted (idle %.0fs)", glass_id, entry.idle_seconds)
            return None

        # LRU bump
        self._store.move_to_end(glass_id)
        entry.last_hit = time.monotonic()
        entry.hits += 1
        return entry

    def put(
        self,
        glass_id: int,
        vector: np.ndarray,
        fact_count: int,
    ) -> CacheEntry:
        """
        Insert or replace a glass vector in the cache.
        Evicts the least-recently-used entry if over capacity.
        """
        now = time.monotonic()

        # If already cached, update in place
        if glass_id in self._store:
            entry = self._store[glass_id]
            entry.vector = vector
            entry.fact_count = fact_count
            entry.built_at = now
            entry.last_hit = now
            self._store.move_to_end(glass_id)
            return entry

        # Evict if at capacity
        while len(self._store) >= self._max:
            evicted_id, evicted = self._store.popitem(last=False)
            logger.debug(
                "Glass %d evicted (LRU, %d hits, idle %.0fs)",
                evicted_id, evicted.hits, evicted.idle_seconds,
            )

        entry = CacheEntry(
            glass_id=glass_id,
            vector=vector,
            fact_count=fact_count,
            built_at=now,
            last_hit=now,
        )
        self._store[glass_id] = entry
        return entry

    def invalidate(self, glass_id: int) -> bool:
        """
        Force-evict a specific glass (e.g. after a fact mutation).
        Returns True if the glass was cached.
        """
        removed = self._store.pop(glass_id, None)
        if removed:
            logger.debug("Glass %d invalidated (had %d hits)", glass_id, removed.hits)
        return removed is not None

    def invalidate_persona(self, persona_id: int) -> int:
        """
        Bulk invalidate is not directly possible without scanning,
        so this is a placeholder. The GlassManager tracks persona→glass
        mappings and calls invalidate() per glass.
        """
        # This would require the cache to store persona_id per entry.
        # For now, callers should use invalidate() per glass_id.
        logger.warning("invalidate_persona() not implemented in cache; use per-glass invalidation")
        return 0

    def clear(self) -> int:
        """Evict everything. Returns number of entries removed."""
        n = len(self._store)
        self._store.clear()
        logger.info("Glass cache cleared (%d entries)", n)
        return n

    def evict_expired(self) -> int:
        """Scan and remove all idle-expired entries. Returns count removed."""
        to_remove = [
            gid for gid, entry in self._store.items()
            if entry.idle_seconds > self._eviction_s
        ]
        for gid in to_remove:
            self._store.pop(gid, None)
        if to_remove:
            logger.debug("Evicted %d expired glasses", len(to_remove))
        return len(to_remove)

    def stats(self) -> Dict:
        """Return cache statistics for observability."""
        if not self._store:
            return {"size": 0, "max": self._max, "oldest_s": 0, "total_hits": 0}

        entries = list(self._store.values())
        return {
            "size": len(entries),
            "max": self._max,
            "eviction_minutes": self._eviction_s / 60.0,
            "oldest_s": max(e.age_seconds for e in entries),
            "newest_s": min(e.age_seconds for e in entries),
            "total_hits": sum(e.hits for e in entries),
            "avg_fact_count": sum(e.fact_count for e in entries) / len(entries),
        }
