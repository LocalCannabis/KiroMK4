#!/usr/bin/env python3
"""
jack/migrate.py — Run Jack's PostgreSQL migrations.

Usage:
    python -m jack.migrate                          # Apply all pending migrations
    python -m jack.migrate --print                  # Print SQL without running
    python -m jack.migrate --config jack_config.yaml # Custom config path

Requires psycopg2 and a running PostgreSQL instance.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

from .config import load_jack_config

logger = logging.getLogger("jack.migrate")

MIGRATION_DIR = str(Path(__file__).parent / "migrations")


def _load_migration_sql(filename: str) -> str:
    """Load SCHEMA_SQL from a migration file using importlib (handles numeric prefixes)."""
    filepath = Path(MIGRATION_DIR) / filename
    spec = importlib.util.spec_from_file_location(filepath.stem, filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "SQL")


def get_connection(cfg: dict):
    """Create a psycopg2 connection from Jack's database config."""
    import psycopg2
    db_cfg = cfg.get("database", {})
    return psycopg2.connect(
        host=db_cfg.get("host", "localhost"),
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg.get("dbname", "kiro"),
        user=db_cfg.get("user", "kiro"),
        password=db_cfg.get("password", ""),
    )


def run_migrations(cfg: dict, print_only: bool = False) -> None:
    """Execute pending migration SQL files (skips already-applied ones)."""
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
            print(f"-- {fname}")
            print(sql)
            print()
        return

    conn = get_connection(cfg)
    try:
        # Create tracking table if it doesn't exist
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
        conn.commit()

        # Get already-applied migrations
        with conn.cursor() as cur:
            cur.execute("SELECT filename FROM schema_migrations ORDER BY filename;")
            applied = {row[0] for row in cur.fetchall()}

        pending = [f for f in migration_files if f not in applied]
        if not pending:
            logger.info("All migrations already applied (%d total).", len(applied))
            return

        for fname in pending:
            sql = _load_migration_sql(fname)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING;",
                    (fname,),
                )
            conn.commit()
            logger.info("Migration %s applied successfully.", fname.replace(".py", ""))

        logger.info("Done. %d migration(s) applied, %d total.", len(pending), len(applied) + len(pending))
    except Exception as exc:
        conn.rollback()
        logger.error("Migration failed: %s", exc)
        raise
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    parser = argparse.ArgumentParser(description="Jack PostgreSQL migration runner")
    parser.add_argument("--config", default=None, help="Path to jack_config.yaml")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print SQL without executing")
    args = parser.parse_args()

    cfg = load_jack_config(args.config)
    run_migrations(cfg, print_only=args.print_only)


if __name__ == "__main__":
    main()
