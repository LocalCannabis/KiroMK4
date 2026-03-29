"""
kiro.memory.migration — SQL schema + one-time migration from legacy databases.

Creates all tables for the four-tier memory architecture in the existing
'kiro' PostgreSQL database. Also migrates facts from the legacy SQLite
(memory/) and per-persona databases into the new unified schema.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger("kiro.memory.migration")


# ─────────────────────────────────────────────────────────────────────────────
# Schema DDL
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Enable pgvector if not already present
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Personas ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS personas (
    id          SERIAL PRIMARY KEY,
    slug        VARCHAR(32) UNIQUE NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Glasses (Layer 2 — HRR containers) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS glasses (
    id              SERIAL PRIMARY KEY,
    persona_id      INTEGER NOT NULL REFERENCES personas(id),
    label           VARCHAR(128) NOT NULL,
    description     TEXT,
    fact_count      INTEGER DEFAULT 0,
    max_capacity    INTEGER DEFAULT 50,
    saturation      REAL DEFAULT 0.0,
    parent_id       INTEGER REFERENCES glasses(id),
    status          VARCHAR(20) DEFAULT 'active'
                    CHECK (status IN ('active', 'saturated', 'retired', 'pending_split')),
    hrr_seed        INTEGER NOT NULL DEFAULT 0,
    hrr_dimension   INTEGER NOT NULL DEFAULT 1024,
    shelf_embedding VECTOR(1536),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    split_at        TIMESTAMPTZ,
    merged_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_glasses_persona_status
    ON glasses(persona_id, status);

-- pgvector index for shelf retrieval (Tier 1)
-- Using ivfflat with cosine distance; will need > 100 rows before useful
-- CREATE INDEX IF NOT EXISTS idx_glasses_shelf_embedding
--     ON glasses USING ivfflat (shelf_embedding vector_cosine_ops)
--     WITH (lists = 10);

-- ── Facts (Layer 3 — source of truth) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS facts (
    id          SERIAL PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id),
    glass_id    INTEGER REFERENCES glasses(id),
    hrr_key     TEXT NOT NULL,
    hrr_value   TEXT NOT NULL,
    source      VARCHAR(128),
    confidence  REAL DEFAULT 1.0,
    is_shared   BOOLEAN DEFAULT FALSE,
    recall_count INTEGER DEFAULT 0,
    promoted    BOOLEAN DEFAULT FALSE,
    promoted_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    retired_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_facts_persona_glass
    ON facts(persona_id, glass_id)
    WHERE retired_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_facts_promoted
    ON facts(persona_id)
    WHERE promoted = TRUE AND retired_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_facts_staged
    ON facts(persona_id)
    WHERE glass_id IS NULL AND retired_at IS NULL;

-- Full-text index for Tier 3 fallback
CREATE INDEX IF NOT EXISTS idx_facts_key_trgm
    ON facts USING gin (hrr_key gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_facts_value_trgm
    ON facts USING gin (hrr_value gin_trgm_ops);

-- pg_trgm extension for ILIKE performance (safe to call multiple times)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── Glass lifecycle log ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS glass_lifecycle (
    id          SERIAL PRIMARY KEY,
    glass_id    INTEGER NOT NULL REFERENCES glasses(id),
    event       VARCHAR(32) NOT NULL,
    details     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Retrieval log (observability) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS retrieval_log (
    id          SERIAL PRIMARY KEY,
    persona_id  INTEGER NOT NULL,
    query       TEXT NOT NULL,
    tier_reached INTEGER DEFAULT 0,
    result_count INTEGER DEFAULT 0,
    total_ms    REAL DEFAULT 0.0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Memory config (config-over-code) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS memory_config (
    key         VARCHAR(128) PRIMARY KEY,
    value       JSONB NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
"""

# ── Seed data ────────────────────────────────────────────────────────────

SEED_PERSONAS_SQL = """
INSERT INTO personas (slug, display_name, description)
VALUES
    ('kiro',   'KIRO',   'Primary assistant — warm, general-purpose'),
    ('finley', 'FINLEY', 'Financial advisor — YNAB budgets and money'),
    ('coach',  'COACH',  'Fitness coach — workouts, protein, motivation'),
    ('chef',   'CHEF',   'Cooking persona — recipes, meal plans'),
    ('doc',    'DOC',    'Wellness persona — mental health, mindfulness'),
    ('sage',   'SAGE',   'Philosophy persona — debate, ethics, meaning'),
    ('jack',   'JACK',   'Grow master — cannabis cultivation and tracking')
ON CONFLICT (slug) DO NOTHING;
"""

SEED_CONFIG_SQL = """
INSERT INTO memory_config (key, value, description)
VALUES
    ('glass.max_capacity',        '50',     'Max facts per glass before mitosis'),
    ('glass.saturation_threshold','0.75',   'Fidelity threshold to trigger split'),
    ('glass.min_facts_for_split', '10',     'Min facts needed before a glass can split'),
    ('glass.cold_threshold_days', '90',     'Days of inactivity before cold retirement'),
    ('glass.min_facts_for_glass', '5',      'Min staged facts before creating a new glass'),
    ('hrr.dimension',             '1024',   'HRR vector dimensionality'),
    ('hrr.embedding_model',       '"text-embedding-3-small"', 'Model for shelf embeddings'),
    ('hrr.sharpen_p',             '1.0',    'Sharpening exponent (1.0 = disabled)'),
    ('hrr.corvacs_a',             '0.0',    'CORVACS magnitude limiter (0.0 = disabled)'),
    ('hrr.temp_T',                '0.9',    'Softmax temperature for decode'),
    ('hrr.orth_iters',            '1',      'Gram-Schmidt iterations during glass build'),
    ('hrr.orth_step',             '0.4',    'Orthogonalization learning rate'),
    ('shelf.top_k',               '3',      'Number of glasses to retrieve from shelf'),
    ('retrieval.test_interval',   '100',    'Queries between automatic fidelity tests'),
    ('promotion.recall_threshold','3',      'Recalls before promotion eligibility'),
    ('promotion.max_per_persona', '50',     'Max promoted facts per persona'),
    ('cache.eviction_minutes',    '30',     'Idle minutes before cache eviction'),
    ('cache.max_glasses',         '100',    'Max glasses in memory cache')
ON CONFLICT (key) DO NOTHING;
"""


def run_schema(db_cfg: Dict[str, Any]) -> None:
    """
    Create all tables, indexes, and seed data in the kiro database.
    Safe to run multiple times (idempotent via IF NOT EXISTS / ON CONFLICT).
    """
    conn = psycopg2.connect(
        host=db_cfg.get("host", "localhost"),
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg.get("dbname", "kiro"),
        user=db_cfg.get("user", "kiro"),
        password=db_cfg.get("password", ""),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(SEED_PERSONAS_SQL)
            cur.execute(SEED_CONFIG_SQL)
        conn.commit()
        logger.info("Memory schema applied successfully")
    except Exception as e:
        conn.rollback()
        logger.error("Schema migration failed: %s", e)
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Legacy migration: SQLite facts → PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────

def migrate_legacy_sqlite(
    db_cfg: Dict[str, Any],
    sqlite_path: str = "./data/kiro.db",
) -> int:
    """
    Migrate facts from the legacy SQLite memory database into the new
    PostgreSQL facts table.

    Facts are imported with glass_id=NULL (staged) and source='legacy_sqlite'.
    They will be auto-assigned to glasses on the next maintenance run.

    Returns the number of facts migrated.
    """
    if not os.path.exists(sqlite_path):
        logger.warning("Legacy SQLite not found at %s, skipping migration", sqlite_path)
        return 0

    # Get persona slug → id mapping from PG
    pg_conn = psycopg2.connect(
        host=db_cfg.get("host", "localhost"),
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg.get("dbname", "kiro"),
        user=db_cfg.get("user", "kiro"),
        password=db_cfg.get("password", ""),
    )

    try:
        with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, slug FROM personas")
            persona_map = {row["slug"]: row["id"] for row in cur.fetchall()}

        # Read from legacy SQLite
        sq_conn = sqlite3.connect(sqlite_path)
        sq_conn.row_factory = sqlite3.Row
        sq_cur = sq_conn.cursor()

        # Try to read from the 'memories' table (old schema)
        try:
            sq_cur.execute("""
                SELECT persona, content, source, created_at
                FROM memories
                WHERE deleted_at IS NULL
                ORDER BY created_at
            """)
            rows = sq_cur.fetchall()
        except sqlite3.OperationalError:
            logger.warning("No 'memories' table in SQLite, trying 'facts'")
            try:
                sq_cur.execute("""
                    SELECT persona, key, value, source, created_at
                    FROM facts
                    WHERE retired_at IS NULL
                    ORDER BY created_at
                """)
                rows = sq_cur.fetchall()
            except sqlite3.OperationalError:
                logger.warning("No recognizable table in legacy SQLite")
                sq_conn.close()
                return 0

        migrated = 0
        with pg_conn.cursor() as cur:
            for row in rows:
                # Determine persona
                slug = row["persona"] if "persona" in row.keys() else "kiro"
                persona_id = persona_map.get(slug, persona_map.get("kiro", 1))

                # Extract key/value
                if "key" in row.keys():
                    hrr_key = row["key"]
                    hrr_value = row["value"]
                else:
                    # Old 'memories' table: content is the whole thing
                    content = row["content"]
                    # Split on first colon or use content as both
                    if ":" in content:
                        hrr_key, hrr_value = content.split(":", 1)
                        hrr_key = hrr_key.strip()
                        hrr_value = hrr_value.strip()
                    else:
                        hrr_key = content[:60]
                        hrr_value = content

                source = row.get("source", "legacy_sqlite") or "legacy_sqlite"

                cur.execute(
                    """INSERT INTO facts
                       (persona_id, glass_id, hrr_key, hrr_value, source,
                        confidence, is_shared, created_at, updated_at)
                       VALUES (%s, NULL, %s, %s, %s, 0.8, FALSE, NOW(), NOW())
                       ON CONFLICT DO NOTHING""",
                    (persona_id, hrr_key, hrr_value, f"legacy:{source}"),
                )
                migrated += 1

        pg_conn.commit()
        sq_conn.close()
        logger.info("Migrated %d facts from legacy SQLite", migrated)
        return migrated

    except Exception as e:
        pg_conn.rollback()
        logger.error("Legacy migration failed: %s", e)
        raise
    finally:
        pg_conn.close()
