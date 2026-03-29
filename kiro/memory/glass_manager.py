"""
kiro.memory.glass_manager — Glass CRUD, mitosis, merge, fidelity, promotion.

Owns all write paths for glasses and facts. Talks to PostgreSQL (source of
truth) and keeps the in-memory GlassCache warm. Vectors are rebuilt from
facts + seed via hrr.build_glass_vector() — never persisted.

Connection pooling follows the jack/db.py pattern (ThreadedConnectionPool).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from kiro.config.memory import MemoryConfigLoader
from kiro.memory.glass_cache import GlassCache
from kiro.memory.hrr import (
    add_to_glass_vector,
    build_glass_vector,
    decode,
    forget,
    generate_vector,
    similarity,
)
from kiro.models.fact import Fact
from kiro.models.glass import Glass

logger = logging.getLogger("kiro.memory.glass_manager")


class GlassManager:
    """
    Central manager for the four-tier memory system.

    Tier 0: Promoted facts (instant recall, bypasses HRR)
    Tier 1: pgvector shelf (coarse glass retrieval)
    Tier 2: FHRR glass decode (fine-grained fact retrieval)
    Tier 3: PostgreSQL full-text fallback (100 % recall guarantee)

    All vector math uses ephemeral FHRR vectors rebuilt from seed.
    """

    def __init__(
        self,
        db_cfg: Dict[str, Any],
        config: MemoryConfigLoader,
    ) -> None:
        self._config = config
        self._pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            host=db_cfg.get("host", "localhost"),
            port=int(db_cfg.get("port", 5432)),
            dbname=db_cfg.get("dbname", "kiro"),
            user=db_cfg.get("user", "kiro"),
            password=db_cfg.get("password", ""),
        )
        self._cache = GlassCache(
            max_glasses=config.get_int("cache.max_glasses", 100),
            eviction_minutes=config.get_float("cache.eviction_minutes", 30.0),
        )
        logger.info("GlassManager initialized")

    # ── Connection helpers ────────────────────────────────────────────────

    def _conn(self):
        return self._pool.getconn()

    def _put(self, conn):
        self._pool.putconn(conn)

    def close(self):
        self._pool.closeall()

    # ── Glass CRUD ────────────────────────────────────────────────────────

    def create_glass(
        self,
        persona_id: int,
        label: str,
        description: Optional[str] = None,
        max_capacity: Optional[int] = None,
    ) -> Glass:
        """Create a new empty glass with a random PRNG seed."""
        max_cap = max_capacity or self._config.get_int("glass.max_capacity", 50)
        dimension = self._config.get_int("hrr.dimension", 1024)
        seed = random.randint(0, 0xFFFFFFFF)

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO glasses
                       (persona_id, label, description, max_capacity,
                        hrr_seed, hrr_dimension, status, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, 'active', NOW())
                       RETURNING *""",
                    (persona_id, label, description, max_cap, seed, dimension),
                )
                row = cur.fetchone()
            conn.commit()
            glass = Glass.from_row(row)
            logger.info("Created glass %d '%s' for persona %d (seed=%d)",
                        glass.id, label, persona_id, seed)
            return glass
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_glass(self, glass_id: int) -> Optional[Glass]:
        """Fetch a glass row by ID."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM glasses WHERE id = %s", (glass_id,))
                row = cur.fetchone()
            return Glass.from_row(row) if row else None
        finally:
            self._put(conn)

    def list_glasses(
        self,
        persona_id: int,
        status: str = "active",
    ) -> List[Glass]:
        """List all glasses for a persona with a given status."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT * FROM glasses
                       WHERE persona_id = %s AND status = %s
                       ORDER BY created_at""",
                    (persona_id, status),
                )
                return [Glass.from_row(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    # ── Fact CRUD ─────────────────────────────────────────────────────────

    def store_fact(
        self,
        persona_id: int,
        hrr_key: str,
        hrr_value: str,
        glass_id: Optional[int] = None,
        source: Optional[str] = None,
        confidence: float = 1.0,
        is_shared: bool = False,
    ) -> Fact:
        """
        Insert a new fact into the facts table.

        If glass_id is provided and the glass is cached, incrementally
        add the binding to the cached vector (skips full rebuild).
        """
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO facts
                       (persona_id, glass_id, hrr_key, hrr_value, source,
                        confidence, is_shared, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                       RETURNING *""",
                    (persona_id, glass_id, hrr_key, hrr_value, source,
                     confidence, is_shared),
                )
                row = cur.fetchone()

                # Update glass fact_count
                if glass_id:
                    cur.execute(
                        """UPDATE glasses SET fact_count = fact_count + 1,
                                             updated_at = NOW()
                           WHERE id = %s""",
                        (glass_id,),
                    )

            conn.commit()
            fact = Fact.from_row(row)
            logger.info("Stored fact %d: %s → %s (glass=%s)",
                        fact.id, hrr_key[:40], hrr_value[:40], glass_id)

            # Incremental cache update
            if glass_id:
                entry = self._cache.get(glass_id)
                if entry:
                    glass = self.get_glass(glass_id)
                    if glass:
                        dim = glass.hrr_dimension
                        seed = glass.hrr_seed
                        entry.vector = add_to_glass_vector(
                            entry.vector, hrr_key, hrr_value,
                            entry.fact_count, dim, seed,
                        )
                        entry.fact_count += 1

            return fact
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_facts_for_glass(self, glass_id: int) -> List[Fact]:
        """Fetch all active facts belonging to a glass."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT * FROM facts
                       WHERE glass_id = %s AND retired_at IS NULL
                       ORDER BY created_at""",
                    (glass_id,),
                )
                return [Fact.from_row(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def retire_fact(self, fact_id: int) -> bool:
        """
        Soft-delete a fact and remove its binding from the cached glass vector.
        """
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """UPDATE facts SET retired_at = NOW(), updated_at = NOW()
                       WHERE id = %s AND retired_at IS NULL
                       RETURNING *""",
                    (fact_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False

                fact = Fact.from_row(row)

                if fact.glass_id:
                    cur.execute(
                        """UPDATE glasses SET fact_count = GREATEST(fact_count - 1, 0),
                                             updated_at = NOW()
                           WHERE id = %s""",
                        (fact.glass_id,),
                    )

            conn.commit()

            # Remove from cached glass vector via forget
            if fact.glass_id:
                entry = self._cache.get(fact.glass_id)
                if entry and entry.fact_count > 0:
                    glass = self.get_glass(fact.glass_id)
                    if glass:
                        dim = glass.hrr_dimension
                        seed = glass.hrr_seed
                        key_vec = generate_vector(fact.hrr_key, dim, seed)
                        val_vec = generate_vector(fact.hrr_value, dim, seed)
                        entry.vector = forget(
                            entry.vector, key_vec, val_vec, entry.fact_count,
                        )
                        entry.fact_count -= 1

            logger.info("Retired fact %d", fact_id)
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ── Tier 0 promotion ──────────────────────────────────────────────────

    def bump_recall(self, fact_id: int) -> int:
        """
        Increment recall_count for a fact. If it crosses the promotion
        threshold, mark as promoted (Tier 0).

        Returns the new recall_count.
        """
        threshold = self._config.get_int("promotion.recall_threshold", 3)
        max_promoted = self._config.get_int("promotion.max_per_persona", 50)

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """UPDATE facts
                       SET recall_count = recall_count + 1,
                           updated_at = NOW()
                       WHERE id = %s
                       RETURNING recall_count, persona_id, promoted""",
                    (fact_id,),
                )
                row = cur.fetchone()
                if not row:
                    return 0

                new_count = row["recall_count"]

                # Check if should promote
                if not row["promoted"] and new_count >= threshold:
                    # Count current promotions for this persona
                    cur.execute(
                        """SELECT COUNT(*) as cnt FROM facts
                           WHERE persona_id = %s AND promoted = TRUE
                                 AND retired_at IS NULL""",
                        (row["persona_id"],),
                    )
                    current = cur.fetchone()["cnt"]

                    if current < max_promoted:
                        cur.execute(
                            """UPDATE facts
                               SET promoted = TRUE, promoted_at = NOW()
                               WHERE id = %s""",
                            (fact_id,),
                        )
                        logger.info(
                            "Fact %d promoted to Tier 0 (recall_count=%d)",
                            fact_id, new_count,
                        )

            conn.commit()
            return new_count
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_promoted_facts(self, persona_id: int) -> List[Fact]:
        """Tier 0 — fetch all promoted facts for a persona. Instant recall."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT * FROM facts
                       WHERE persona_id = %s AND promoted = TRUE
                             AND retired_at IS NULL
                       ORDER BY promoted_at DESC""",
                    (persona_id,),
                )
                return [Fact.from_row(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    # ── Glass vector materialization ──────────────────────────────────────

    def materialize_glass(self, glass_id: int, force: bool = False) -> Optional[np.ndarray]:
        """
        Get (or rebuild) the FHRR vector for a glass.

        1. Check the LRU cache — if hit, return immediately.
        2. Cache miss: fetch all facts from PG, rebuild from seed.
        3. Store in cache for future queries.

        Args:
            glass_id: Glass to materialize.
            force:    If True, bypass cache and rebuild from scratch.

        Returns:
            Complex128 glass vector, or None if glass not found.
        """
        # Check cache (unless forced rebuild)
        if not force:
            entry = self._cache.get(glass_id)
            if entry is not None:
                return entry.vector

        # Cache miss — rebuild from facts + seed
        glass = self.get_glass(glass_id)
        if not glass:
            return None

        facts = self.get_facts_for_glass(glass_id)
        fact_dicts = [{"hrr_key": f.hrr_key, "hrr_value": f.hrr_value} for f in facts]

        orth_iters = self._config.get_int("hrr.orth_iters", 1)
        orth_step = self._config.get_float("hrr.orth_step", 0.4)

        vec = build_glass_vector(
            fact_dicts,
            dimension=glass.hrr_dimension,
            seed=glass.hrr_seed,
            orth_iters=orth_iters,
            orth_step=orth_step,
        )

        self._cache.put(glass_id, vec, fact_count=len(facts))
        logger.debug("Materialized glass %d: %d facts, dim=%d",
                      glass_id, len(facts), glass.hrr_dimension)
        return vec

    # ── HRR decode (Tier 2) ───────────────────────────────────────────────

    def decode_from_glass(
        self,
        glass_id: int,
        query_key: str,
        top_k: int = 5,
    ) -> List[Tuple[Fact, float]]:
        """
        Unbind a query key from a glass vector and decode against known values.

        Full Nuggets pipeline: unbind → sharpen → corvacs → similarity → softmax.

        Returns list of (Fact, similarity_score) sorted descending.
        """
        glass = self.get_glass(glass_id)
        if not glass:
            return []

        glass_vec = self.materialize_glass(glass_id)
        if glass_vec is None:
            return []

        facts = self.get_facts_for_glass(glass_id)
        if not facts:
            return []

        dim = glass.hrr_dimension
        seed = glass.hrr_seed

        # Generate query key vector and unbind
        query_vec = generate_vector(query_key, dim, seed)
        from kiro.memory.hrr import unbind as hrr_unbind
        retrieved = hrr_unbind(glass_vec, query_vec)

        # Generate candidate value vectors
        candidate_vecs = [generate_vector(f.hrr_value, dim, seed) for f in facts]

        # Full decode pipeline
        sharpen_p = self._config.get_float("hrr.sharpen_p", 1.0)
        corvacs_a = self._config.get_float("hrr.corvacs_a", 0.0)
        temp_T = self._config.get_float("hrr.temp_T", 0.9)

        best_idx, best_sim, probs = decode(
            retrieved, candidate_vecs,
            sharpen_p=sharpen_p,
            corvacs_a=corvacs_a,
            temp_T=temp_T,
        )

        # Rank all facts by similarity
        from kiro.memory.hrr import similarity_matrix
        sims = similarity_matrix(retrieved, candidate_vecs)

        results = list(zip(facts, sims.tolist()))
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:top_k]

    # ── Fidelity testing ──────────────────────────────────────────────────

    def test_glass_fidelity(self, glass_id: int) -> float:
        """
        Round-trip fidelity test: for each fact, unbind key from glass
        and check if the top-1 decoded value matches the original.

        Returns accuracy (0.0 to 1.0). Used to detect when a glass
        needs a full rebuild (too much incremental drift) or mitosis.
        """
        glass = self.get_glass(glass_id)
        if not glass:
            return 0.0

        glass_vec = self.materialize_glass(glass_id)
        if glass_vec is None:
            return 0.0

        facts = self.get_facts_for_glass(glass_id)
        if not facts:
            return 1.0  # empty glass is trivially perfect

        dim = glass.hrr_dimension
        seed = glass.hrr_seed
        sharpen_p = self._config.get_float("hrr.sharpen_p", 1.0)
        corvacs_a = self._config.get_float("hrr.corvacs_a", 0.0)
        temp_T = self._config.get_float("hrr.temp_T", 0.9)

        candidate_vecs = [generate_vector(f.hrr_value, dim, seed) for f in facts]
        correct = 0

        for i, fact in enumerate(facts):
            key_vec = generate_vector(fact.hrr_key, dim, seed)
            from kiro.memory.hrr import unbind as hrr_unbind
            retrieved = hrr_unbind(glass_vec, key_vec)

            best_idx, best_sim, _ = decode(
                retrieved, candidate_vecs,
                sharpen_p=sharpen_p,
                corvacs_a=corvacs_a,
                temp_T=temp_T,
            )
            if best_idx == i:
                correct += 1

        accuracy = correct / len(facts)
        logger.debug("Glass %d fidelity: %d/%d = %.2f%%",
                      glass_id, correct, len(facts), accuracy * 100)

        # Update saturation in DB
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE glasses SET saturation = %s, updated_at = NOW()
                       WHERE id = %s""",
                    (1.0 - accuracy, glass_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            self._put(conn)

        return accuracy

    # ── Mitosis (glass splitting) ─────────────────────────────────────────

    def mitosis(self, glass_id: int) -> Tuple[Glass, Glass]:
        """
        Split a saturated glass into two daughter glasses.

        Algorithm:
        1. Fetch all active facts for the glass.
        2. Split facts roughly in half (by creation date).
        3. Create two new glasses (new seeds) and re-assign facts.
        4. Mark parent as 'retired', log in glass_lifecycle.
        5. Invalidate parent from cache.

        Returns the two new glasses.
        """
        glass = self.get_glass(glass_id)
        if not glass:
            raise ValueError(f"Glass {glass_id} not found")

        facts = self.get_facts_for_glass(glass_id)
        if len(facts) < 2:
            raise ValueError(f"Glass {glass_id} has too few facts to split")

        mid = len(facts) // 2
        left_facts = facts[:mid]
        right_facts = facts[mid:]

        # Create daughter glasses
        daughter_a = self.create_glass(
            persona_id=glass.persona_id,
            label=f"{glass.label} (α)",
            description=f"Split from glass {glass_id}",
        )
        daughter_b = self.create_glass(
            persona_id=glass.persona_id,
            label=f"{glass.label} (β)",
            description=f"Split from glass {glass_id}",
        )

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Re-assign facts to daughter glasses
                left_ids = [f.id for f in left_facts]
                right_ids = [f.id for f in right_facts]

                if left_ids:
                    cur.execute(
                        """UPDATE facts SET glass_id = %s, updated_at = NOW()
                           WHERE id = ANY(%s)""",
                        (daughter_a.id, left_ids),
                    )
                    cur.execute(
                        """UPDATE glasses SET fact_count = %s WHERE id = %s""",
                        (len(left_ids), daughter_a.id),
                    )

                if right_ids:
                    cur.execute(
                        """UPDATE facts SET glass_id = %s, updated_at = NOW()
                           WHERE id = ANY(%s)""",
                        (daughter_b.id, right_ids),
                    )
                    cur.execute(
                        """UPDATE glasses SET fact_count = %s WHERE id = %s""",
                        (len(right_ids), daughter_b.id),
                    )

                # Retire parent
                cur.execute(
                    """UPDATE glasses
                       SET status = 'retired', split_at = NOW(),
                           updated_at = NOW()
                       WHERE id = %s""",
                    (glass_id,),
                )

                # Log lifecycle event
                cur.execute(
                    """INSERT INTO glass_lifecycle
                       (glass_id, event, details, created_at)
                       VALUES (%s, 'mitosis', %s, NOW())""",
                    (glass_id, f"Split into {daughter_a.id}, {daughter_b.id}"),
                )

            conn.commit()
            self._cache.invalidate(glass_id)
            logger.info(
                "Mitosis: glass %d → [%d (%d facts), %d (%d facts)]",
                glass_id, daughter_a.id, len(left_ids),
                daughter_b.id, len(right_ids),
            )
            return daughter_a, daughter_b
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ── Auto-assign staging facts ─────────────────────────────────────────

    def assign_staged_facts(self, persona_id: int) -> int:
        """
        Find facts with glass_id IS NULL and assign them to the best glass
        based on label similarity, or create a new glass if none fit.

        Returns the number of facts assigned.
        """
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get staged facts
                cur.execute(
                    """SELECT * FROM facts
                       WHERE persona_id = %s AND glass_id IS NULL
                             AND retired_at IS NULL
                       ORDER BY created_at""",
                    (persona_id,),
                )
                staged = [Fact.from_row(r) for r in cur.fetchall()]

            if not staged:
                return 0

            # Get active glasses
            glasses = self.list_glasses(persona_id)

            # Simple heuristic: find a non-full glass, or create one
            target_glass = None
            for g in glasses:
                if not g.is_full:
                    target_glass = g
                    break

            if not target_glass:
                min_facts = self._config.get_int("glass.min_facts_for_glass", 5)
                if len(staged) >= min_facts:
                    target_glass = self.create_glass(
                        persona_id=persona_id,
                        label=f"auto-{int(time.time())}",
                        description="Auto-created for staged facts",
                    )
                else:
                    return 0  # not enough staged facts to justify a new glass

            # Assign facts
            fact_ids = [f.id for f in staged]
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE facts SET glass_id = %s, updated_at = NOW()
                       WHERE id = ANY(%s)""",
                    (target_glass.id, fact_ids),
                )
                cur.execute(
                    """UPDATE glasses SET fact_count = fact_count + %s,
                                         updated_at = NOW()
                       WHERE id = %s""",
                    (len(fact_ids), target_glass.id),
                )
            conn.commit()

            # Invalidate cache so next query triggers a rebuild
            self._cache.invalidate(target_glass.id)

            logger.info("Assigned %d staged facts to glass %d",
                        len(staged), target_glass.id)
            return len(staged)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ── Cache passthrough ─────────────────────────────────────────────────

    def cache_stats(self) -> Dict:
        return self._cache.stats()

    def evict_expired_cache(self) -> int:
        return self._cache.evict_expired()
