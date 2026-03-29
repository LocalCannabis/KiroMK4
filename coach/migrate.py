#!/usr/bin/env python3
"""
coach/migrate.py — Run Coach PostgreSQL migrations.

Usage:
    python -m coach.migrate              # Apply all pending migrations
    python -m coach.migrate --print      # Print SQL without running

Reuses Jack's DB config from ~/.kiro/jack_config.yaml (same kiro database).
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("coach.migrate")

MIGRATION_DIR = str(Path(__file__).parent / "migrations")


def _load_migration_sql(filename: str) -> str:
    """Load SQL from a migration file's SQL attribute."""
    filepath = Path(MIGRATION_DIR) / filename
    spec = importlib.util.spec_from_file_location(filepath.stem, filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "SQL")


def get_connection(cfg: dict):
    """Create a psycopg2 connection from database config."""
    import psycopg2
    db_cfg = cfg.get("database", {})
    return psycopg2.connect(
        host=db_cfg.get("host", "localhost"),
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg.get("dbname", "kiro"),
        user=db_cfg.get("user", "kiro"),
        password=db_cfg.get("password", ""),
    )


def _load_config() -> dict:
    """Load database config — reuse Jack's config since we share the kiro DB."""
    try:
        from jack.config import load_jack_config
        return load_jack_config()
    except Exception:
        return {
            "database": {
                "host": "localhost",
                "port": 5432,
                "dbname": "kiro",
                "user": "kiro",
                "password": "",
            }
        }


def run_migrations(cfg: dict, print_only: bool = False) -> None:
    """Execute pending migration SQL files."""
    migration_dir = Path(MIGRATION_DIR)
    migration_files = sorted(
        f.name for f in migration_dir.glob("*.py")
        if f.name != "__init__.py" and not f.name.startswith("__")
    )

    if not migration_files:
        logger.info("No migration files found in %s", MIGRATION_DIR)
        return

    if print_only:
        for fname in migration_files:
            sql = _load_migration_sql(fname)
            print(f"\n-- === {fname} ===\n")
            print(sql)
        return

    conn = get_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS _coach_migrations (
                    id          SERIAL PRIMARY KEY,
                    filename    VARCHAR(200) UNIQUE NOT NULL,
                    applied_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()

            cur.execute("SELECT filename FROM _coach_migrations ORDER BY filename")
            applied = {row[0] for row in cur.fetchall()}

        pending = [f for f in migration_files if f not in applied]
        if not pending:
            logger.info("All Coach migrations already applied.")
            return

        for fname in pending:
            sql = _load_migration_sql(fname)
            logger.info("Applying Coach migration: %s", fname)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO _coach_migrations (filename) VALUES (%s)",
                    (fname,),
                )
            conn.commit()
            logger.info("Applied: %s", fname)

        logger.info("All %d Coach migration(s) applied.", len(pending))

    except Exception as e:
        conn.rollback()
        logger.error("Coach migration failed: %s", e)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Coach PostgreSQL migrations")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print SQL without executing")
    args = parser.parse_args()

    cfg = _load_config()
    run_migrations(cfg, print_only=args.print_only)


if __name__ == "__main__":
    main()
