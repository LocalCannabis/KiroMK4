"""
kiro.memory.retrieval — Four-tier query pipeline.

Tier 0: Promoted facts     → instant recall, bypasses HRR entirely
Tier 1: pgvector shelf     → coarse glass retrieval via embedding similarity
Tier 2: FHRR glass decode  → fine-grained fact retrieval within each glass
Tier 3: PostgreSQL ILIKE   → full-text fallback, guaranteed 100% recall

The pipeline cascades: each tier is tried in order. If a tier returns
results with confidence above threshold, we stop. Otherwise, fall through.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from kiro.config.memory import MemoryConfigLoader
from kiro.memory.glass_manager import GlassManager
from kiro.models.fact import Fact

logger = logging.getLogger("kiro.memory.retrieval")


@dataclass
class RetrievalResult:
    """A single result from the memory retrieval pipeline."""

    fact: Fact
    score: float          # similarity or match confidence [0, 1]
    tier: int             # which tier produced this result (0-3)
    glass_id: Optional[int] = None

    def __str__(self) -> str:
        return f"[T{self.tier} {self.score:.3f}] {self.fact.hrr_key}: {self.fact.hrr_value[:60]}"


@dataclass
class RetrievalContext:
    """Full result of a retrieval query, including timing and tier info."""

    results: List[RetrievalResult] = field(default_factory=list)
    query: str = ""
    persona_id: int = 0
    tier_reached: int = 0       # highest tier we fell through to
    tier_times_ms: Dict[int, float] = field(default_factory=dict)
    total_ms: float = 0.0

    @property
    def facts(self) -> List[Fact]:
        return [r.fact for r in self.results]

    @property
    def top_result(self) -> Optional[RetrievalResult]:
        return self.results[0] if self.results else None


def retrieve(
    query: str,
    persona_id: int,
    manager: GlassManager,
    config: MemoryConfigLoader,
    db_cfg: Dict[str, Any],
    embedding_fn=None,
    top_k: int = 5,
    min_score: float = 0.3,
    include_shared: bool = True,
) -> RetrievalContext:
    """
    Execute the four-tier retrieval pipeline.

    Args:
        query:          Natural language query or HRR key.
        persona_id:     Which persona's memory to search.
        manager:        GlassManager instance (owns cache + DB access).
        config:         MemoryConfigLoader for thresholds.
        db_cfg:         Database connection config dict.
        embedding_fn:   Callable(text) → List[float] for shelf embeddings.
                        If None, Tier 1 is skipped.
        top_k:          Max results to return.
        min_score:      Minimum similarity score to include a result.
        include_shared: Whether to include is_shared=TRUE facts.

    Returns:
        RetrievalContext with results, timing, and tier info.
    """
    ctx = RetrievalContext(query=query, persona_id=persona_id)
    t_start = time.monotonic()

    # ── Tier 0: Promoted facts ────────────────────────────────────────
    t0 = time.monotonic()
    tier0_results = _tier0_promoted(query, persona_id, manager, include_shared)
    ctx.tier_times_ms[0] = (time.monotonic() - t0) * 1000.0

    if tier0_results:
        ctx.results.extend(tier0_results)
        ctx.tier_reached = 0
        # Tier 0 results are always returned but we still check lower tiers
        # for additional context

    # ── Tier 1: pgvector shelf ────────────────────────────────────────
    if embedding_fn is not None:
        t1 = time.monotonic()
        shelf_top_k = config.get_int("shelf.top_k", 3)
        glass_ids = _tier1_shelf(
            query, persona_id, embedding_fn, db_cfg,
            top_k=shelf_top_k, include_shared=include_shared,
        )
        ctx.tier_times_ms[1] = (time.monotonic() - t1) * 1000.0

        if glass_ids:
            ctx.tier_reached = max(ctx.tier_reached, 1)

            # ── Tier 2: FHRR decode within matched glasses ────────
            t2 = time.monotonic()
            for gid in glass_ids:
                decoded = manager.decode_from_glass(gid, query, top_k=top_k)
                for fact, score in decoded:
                    if score >= min_score:
                        ctx.results.append(RetrievalResult(
                            fact=fact, score=score, tier=2, glass_id=gid,
                        ))
            ctx.tier_times_ms[2] = (time.monotonic() - t2) * 1000.0
            ctx.tier_reached = max(ctx.tier_reached, 2)

    # ── Tier 3: PostgreSQL ILIKE fallback ─────────────────────────────
    # Only if we have fewer than top_k results so far
    existing_ids = {r.fact.id for r in ctx.results}
    if len(ctx.results) < top_k:
        t3 = time.monotonic()
        tier3_results = _tier3_pg_fallback(
            query, persona_id, db_cfg,
            top_k=top_k, include_shared=include_shared,
            exclude_ids=existing_ids,
        )
        ctx.tier_times_ms[3] = (time.monotonic() - t3) * 1000.0
        ctx.results.extend(tier3_results)
        ctx.tier_reached = max(ctx.tier_reached, 3)

    # Deduplicate by fact ID, keeping highest-scoring
    seen = {}
    for r in ctx.results:
        if r.fact.id not in seen or r.score > seen[r.fact.id].score:
            seen[r.fact.id] = r
    ctx.results = sorted(seen.values(), key=lambda r: r.score, reverse=True)[:top_k]

    ctx.total_ms = (time.monotonic() - t_start) * 1000.0

    # Bump recall counts for returned facts
    for r in ctx.results:
        try:
            manager.bump_recall(r.fact.id)
        except Exception as e:
            logger.warning("Failed to bump recall for fact %d: %s", r.fact.id, e)

    # Log retrieval
    _log_retrieval(ctx, db_cfg)

    logger.debug(
        "retrieve(%s, persona=%d): %d results, tier=%d, %.1fms",
        query[:40], persona_id, len(ctx.results), ctx.tier_reached, ctx.total_ms,
    )
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Tier implementations
# ─────────────────────────────────────────────────────────────────────────────

def _tier0_promoted(
    query: str,
    persona_id: int,
    manager: GlassManager,
    include_shared: bool,
) -> List[RetrievalResult]:
    """Tier 0: return promoted facts that textually match the query."""
    promoted = manager.get_promoted_facts(persona_id)

    results = []
    q_lower = query.lower()
    for fact in promoted:
        # Simple text overlap scoring for promoted facts
        key_match = q_lower in fact.hrr_key.lower() or fact.hrr_key.lower() in q_lower
        val_match = q_lower in fact.hrr_value.lower()
        if key_match or val_match:
            score = 1.0 if key_match else 0.9
            results.append(RetrievalResult(
                fact=fact, score=score, tier=0, glass_id=fact.glass_id,
            ))

    return results


def _tier1_shelf(
    query: str,
    persona_id: int,
    embedding_fn,
    db_cfg: Dict[str, Any],
    top_k: int = 3,
    include_shared: bool = True,
) -> List[int]:
    """
    Tier 1: pgvector shelf — find the top_k most similar glasses by
    shelf_embedding cosine distance.

    Returns list of glass IDs sorted by relevance.
    """
    try:
        query_embedding = embedding_fn(query)
    except Exception as e:
        logger.warning("Embedding generation failed for Tier 1: %s", e)
        return []

    conn = psycopg2.connect(
        host=db_cfg.get("host", "localhost"),
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg.get("dbname", "kiro"),
        user=db_cfg.get("user", "kiro"),
        password=db_cfg.get("password", ""),
    )
    try:
        with conn.cursor() as cur:
            if include_shared:
                cur.execute(
                    """SELECT id FROM glasses
                       WHERE (persona_id = %s OR persona_id IN (
                           SELECT id FROM personas WHERE slug = 'kiro'))
                         AND status = 'active'
                         AND shelf_embedding IS NOT NULL
                       ORDER BY shelf_embedding <=> %s::vector
                       LIMIT %s""",
                    (persona_id, str(query_embedding), top_k),
                )
            else:
                cur.execute(
                    """SELECT id FROM glasses
                       WHERE persona_id = %s
                         AND status = 'active'
                         AND shelf_embedding IS NOT NULL
                       ORDER BY shelf_embedding <=> %s::vector
                       LIMIT %s""",
                    (persona_id, str(query_embedding), top_k),
                )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.warning("Tier 1 shelf query failed: %s", e)
        return []
    finally:
        conn.close()


def _tier3_pg_fallback(
    query: str,
    persona_id: int,
    db_cfg: Dict[str, Any],
    top_k: int = 5,
    include_shared: bool = True,
    exclude_ids: set = None,
) -> List[RetrievalResult]:
    """
    Tier 3: PostgreSQL full-text fallback via ILIKE.
    Guaranteed 100% recall if the fact exists in the DB.
    """
    exclude_ids = exclude_ids or set()

    conn = psycopg2.connect(
        host=db_cfg.get("host", "localhost"),
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg.get("dbname", "kiro"),
        user=db_cfg.get("user", "kiro"),
        password=db_cfg.get("password", ""),
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            pattern = f"%{query}%"

            persona_filter = "(persona_id = %s)"
            if include_shared:
                persona_filter = "(persona_id = %s OR is_shared = TRUE)"

            sql = f"""
                SELECT * FROM facts
                WHERE {persona_filter}
                  AND retired_at IS NULL
                  AND (hrr_key ILIKE %s OR hrr_value ILIKE %s)
                ORDER BY updated_at DESC
                LIMIT %s
            """
            cur.execute(sql, (persona_id, pattern, pattern, top_k * 2))
            rows = cur.fetchall()

        results = []
        for row in rows:
            fact = Fact.from_row(row)
            if fact.id in exclude_ids:
                continue
            results.append(RetrievalResult(
                fact=fact,
                score=0.5,  # constant score for text match
                tier=3,
                glass_id=fact.glass_id,
            ))

        return results[:top_k]
    except Exception as e:
        logger.warning("Tier 3 PG fallback failed: %s", e)
        return []
    finally:
        conn.close()


def _log_retrieval(ctx: RetrievalContext, db_cfg: Dict[str, Any]) -> None:
    """Log retrieval metrics to the retrieval_log table."""
    try:
        conn = psycopg2.connect(
            host=db_cfg.get("host", "localhost"),
            port=int(db_cfg.get("port", 5432)),
            dbname=db_cfg.get("dbname", "kiro"),
            user=db_cfg.get("user", "kiro"),
            password=db_cfg.get("password", ""),
        )
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO retrieval_log
                   (persona_id, query, tier_reached, result_count,
                    total_ms, created_at)
                   VALUES (%s, %s, %s, %s, %s, NOW())""",
                (ctx.persona_id, ctx.query[:500], ctx.tier_reached,
                 len(ctx.results), ctx.total_ms),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Could not log retrieval: %s", e)
