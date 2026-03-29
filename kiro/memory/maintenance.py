"""
kiro.memory.maintenance — Scheduled maintenance tasks.

These run on a timer (or via CLI) to keep the memory system healthy:
- Assign staged facts to glasses
- Fidelity-test glasses with high query counts
- Trigger mitosis for saturated glasses
- Retire cold glasses
- Evict expired cache entries
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from kiro.config.memory import MemoryConfigLoader
from kiro.memory.glass_manager import GlassManager

logger = logging.getLogger("kiro.memory.maintenance")


def run_maintenance(
    manager: GlassManager,
    config: MemoryConfigLoader,
    persona_ids: List[int] = None,
) -> Dict[str, Any]:
    """
    Run all maintenance tasks. Returns a summary dict.

    Args:
        manager:     GlassManager instance.
        config:      MemoryConfigLoader instance.
        persona_ids: Optional list of persona IDs to maintain.
                     If None, all personas are maintained.
    """
    t_start = time.monotonic()
    summary = {
        "staged_assigned": 0,
        "fidelity_tested": 0,
        "glasses_split": 0,
        "glasses_retired": 0,
        "cache_evicted": 0,
    }

    # 1. Evict expired cache entries
    summary["cache_evicted"] = manager.evict_expired_cache()

    # 2. Get persona IDs if not provided
    if persona_ids is None:
        persona_ids = _get_all_persona_ids(manager)

    for pid in persona_ids:
        # 3. Assign staged facts
        try:
            assigned = manager.assign_staged_facts(pid)
            summary["staged_assigned"] += assigned
        except Exception as e:
            logger.error("Failed to assign staged facts for persona %d: %s", pid, e)

        # 4. Fidelity test active glasses
        try:
            glasses = manager.list_glasses(pid, status="active")
            sat_threshold = config.get_float("glass.saturation_threshold", 0.75)
            min_split = config.get_int("glass.min_facts_for_split", 10)

            for glass in glasses:
                if glass.fact_count == 0:
                    continue

                accuracy = manager.test_glass_fidelity(glass.id)
                summary["fidelity_tested"] += 1

                # 5. Mitosis if saturated
                if (1.0 - accuracy) >= sat_threshold and glass.fact_count >= min_split:
                    try:
                        manager.mitosis(glass.id)
                        summary["glasses_split"] += 1
                    except Exception as e:
                        logger.error("Mitosis failed for glass %d: %s", glass.id, e)

        except Exception as e:
            logger.error("Fidelity testing failed for persona %d: %s", pid, e)

        # 6. Retire cold glasses
        try:
            cold_days = config.get_int("glass.cold_threshold_days", 90)
            retired = _retire_cold_glasses(manager, pid, cold_days)
            summary["glasses_retired"] += retired
        except Exception as e:
            logger.error("Cold retirement failed for persona %d: %s", pid, e)

    elapsed_ms = (time.monotonic() - t_start) * 1000.0
    summary["elapsed_ms"] = round(elapsed_ms, 1)

    logger.info(
        "Maintenance complete: %d staged assigned, %d fidelity tested, "
        "%d split, %d retired, %d cache evicted (%.1fms)",
        summary["staged_assigned"], summary["fidelity_tested"],
        summary["glasses_split"], summary["glasses_retired"],
        summary["cache_evicted"], elapsed_ms,
    )
    return summary


def _get_all_persona_ids(manager: GlassManager) -> List[int]:
    """Fetch all persona IDs from the database."""
    conn = manager._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM personas ORDER BY id")
            return [row[0] for row in cur.fetchall()]
    finally:
        manager._put(conn)


def _retire_cold_glasses(
    manager: GlassManager,
    persona_id: int,
    cold_days: int,
) -> int:
    """Retire glasses that haven't been updated in cold_days."""
    conn = manager._conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE glasses
                   SET status = 'retired', updated_at = NOW()
                   WHERE persona_id = %s
                     AND status = 'active'
                     AND updated_at < NOW() - INTERVAL '%s days'
                   RETURNING id""",
                (persona_id, cold_days),
            )
            retired_ids = [row[0] for row in cur.fetchall()]

            for gid in retired_ids:
                cur.execute(
                    """INSERT INTO glass_lifecycle
                       (glass_id, event, details, created_at)
                       VALUES (%s, 'retired_cold', 'Inactive for %s+ days', NOW())""",
                    (gid, cold_days),
                )

        conn.commit()

        for gid in retired_ids:
            manager._cache.invalidate(gid)

        return len(retired_ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        manager._put(conn)
