"""
kiro.config.memory — Config-over-code loader for the memory subsystem.

All thresholds, capacity limits, model names, and tuning parameters are
read from the memory_config PostgreSQL table at startup and cached in-process.
Nothing is hardcoded. Changes to the table take effect on next reload.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger("kiro.config.memory")

# ── Defaults (used only when the DB row is missing) ──────────────────────
# These exist so the system can boot even if memory_config is empty.
# They are NOT authoritative — the table is.
_DEFAULTS: Dict[str, Any] = {
    # Glass lifecycle
    "glass.max_capacity": 50,
    "glass.saturation_threshold": 0.75,
    "glass.min_facts_for_split": 10,
    "glass.cold_threshold_days": 90,
    "glass.min_facts_for_glass": 5,
    # HRR vector parameters
    "hrr.dimension": 1024,
    "hrr.embedding_model": "text-embedding-3-small",
    "hrr.sharpen_p": 1.0,           # sharpening exponent (1.0 = disabled)
    "hrr.corvacs_a": 0.0,           # CORVACS magnitude limiter (0.0 = disabled)
    "hrr.temp_T": 0.9,              # softmax temperature for decode
    "hrr.orth_iters": 1,            # Gram-Schmidt iterations during glass build
    "hrr.orth_step": 0.4,           # orthogonalization learning rate
    # Shelf retrieval
    "shelf.top_k": 3,
    # Retrieval
    "retrieval.test_interval": 100,
    # Tier 0 promotion
    "promotion.recall_threshold": 3,    # recalls before a fact is promotion-eligible
    "promotion.max_per_persona": 50,    # max promoted facts per persona
    # Glass cache
    "cache.eviction_minutes": 30,       # minutes before an idle glass is evicted
    "cache.max_glasses": 100,           # max glasses held in memory at once
}


class MemoryConfigLoader:
    """
    Loads and caches memory_config values from PostgreSQL.

    Usage:
        cfg = MemoryConfigLoader(db_cfg)
        dim = cfg.get("hrr.dimension")              # -> 1024
        top_k = cfg.get("shelf.top_k")              # -> 3
        model = cfg.get("hrr.embedding_model")       # -> "text-embedding-3-small"

    Call cfg.reload() to refresh from the database.
    """

    def __init__(self, db_cfg: Dict[str, Any]) -> None:
        self._db_cfg = db_cfg
        self._cache: Dict[str, Any] = dict(_DEFAULTS)
        self.reload()

    def _connect(self):
        return psycopg2.connect(
            host=self._db_cfg.get("host", "localhost"),
            port=int(self._db_cfg.get("port", 5432)),
            dbname=self._db_cfg.get("dbname", "kiro"),
            user=self._db_cfg.get("user", "kiro"),
            password=self._db_cfg.get("password", ""),
        )

    def reload(self) -> None:
        """Refresh the in-memory cache from the memory_config table."""
        try:
            conn = self._connect()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT key, value FROM memory_config")
                for row in cur.fetchall():
                    val = row["value"]
                    # psycopg2 auto-parses JSONB; handle string fallback
                    if isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    self._cache[row["key"]] = val
            conn.close()
            logger.info("Memory config loaded: %d keys", len(self._cache))
        except Exception as e:
            logger.warning("Could not load memory_config from DB, using defaults: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key. Falls back to default if missing."""
        val = self._cache.get(key)
        if val is None:
            return default if default is not None else _DEFAULTS.get(key)
        return val

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.get(key, default))

    def get_str(self, key: str, default: str = "") -> str:
        return str(self.get(key, default))

    def set(self, key: str, value: Any, description: Optional[str] = None) -> None:
        """Update a config value in both the DB and the cache."""
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO memory_config (key, value, description, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value,
                           description = COALESCE(EXCLUDED.description, memory_config.description),
                           updated_at = NOW()""",
                    (key, json.dumps(value), description),
                )
            conn.commit()
            conn.close()
            self._cache[key] = value
            logger.info("Memory config updated: %s = %r", key, value)
        except Exception as e:
            logger.error("Failed to update memory_config %s: %s", key, e)
            raise

    def all(self) -> Dict[str, Any]:
        """Return a copy of the full config cache."""
        return dict(self._cache)
