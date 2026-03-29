#!/usr/bin/env python3
"""
finley/migrate.py — Run Finley PostgreSQL migrations.

Usage:
    python -m finley.migrate              # Apply all pending migrations
    python -m finley.migrate --print      # Print SQL without running
    python -m finley.migrate --seed       # Apply migrations + migrate legacy SQLite data

Reuses Jack's DB config from ~/.kiro/jack_config.yaml (same kiro database).
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("finley.migrate")

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
                CREATE TABLE IF NOT EXISTS _finley_migrations (
                    id          SERIAL PRIMARY KEY,
                    filename    VARCHAR(200) UNIQUE NOT NULL,
                    applied_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()

            cur.execute("SELECT filename FROM _finley_migrations ORDER BY filename")
            applied = {row[0] for row in cur.fetchall()}

        pending = [f for f in migration_files if f not in applied]
        if not pending:
            logger.info("All Finley migrations already applied.")
            return

        for fname in pending:
            sql = _load_migration_sql(fname)
            logger.info("Applying Finley migration: %s", fname)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO _finley_migrations (filename) VALUES (%s)",
                    (fname,),
                )
            conn.commit()
            logger.info("Applied: %s", fname)

        logger.info("All %d Finley migration(s) applied.", len(pending))

    except Exception as e:
        conn.rollback()
        logger.error("Migration failed: %s", e)
        raise
    finally:
        conn.close()


def migrate_sqlite_data(cfg: dict) -> None:
    """
    One-time migration of YNAB cache data from the old SQLite database
    into the new PostgreSQL tables. Safe to run multiple times (uses
    INSERT ... ON CONFLICT DO NOTHING).
    """
    import json
    import sqlite3
    import psycopg2

    sqlite_path = os.path.expanduser("~/.kiro/finley.db")
    if not os.path.exists(sqlite_path):
        logger.info("No legacy SQLite database found at %s — skipping.", sqlite_path)
        return

    logger.info("Migrating legacy SQLite data from %s", sqlite_path)
    sconn = sqlite3.connect(sqlite_path)
    sconn.row_factory = sqlite3.Row
    pconn = get_connection(cfg)

    try:
        pcur = pconn.cursor()

        # --- Accounts ---
        rows = sconn.execute("SELECT * FROM accounts").fetchall()
        for r in rows:
            pcur.execute("""
                INSERT INTO finley_accounts
                    (id, name, type, on_budget, closed, balance,
                     cleared_balance, uncleared_balance, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (r["id"], r["name"], r["type"],
                  bool(r["on_budget"]), bool(r["closed"]),
                  r["balance"], r["cleared_balance"], r["uncleared_balance"],
                  r["last_updated"]))
        logger.info("  Accounts: %d rows", len(rows))

        # --- Categories ---
        rows = sconn.execute("SELECT * FROM categories").fetchall()
        for r in rows:
            pcur.execute("""
                INSERT INTO finley_categories
                    (id, group_id, group_name, name, budgeted, activity,
                     balance, goal_type, goal_target, goal_target_date, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (r["id"], r["group_id"], r["group_name"], r["name"],
                  r["budgeted"], r["activity"], r["balance"],
                  r["goal_type"], r["goal_target"], r["goal_target_date"],
                  r["last_updated"]))
        logger.info("  Categories: %d rows", len(rows))

        # --- Transactions ---
        rows = sconn.execute("SELECT * FROM transactions").fetchall()
        for r in rows:
            subs = r["subtransactions"]
            if subs and isinstance(subs, str):
                try:
                    subs = json.loads(subs)
                except json.JSONDecodeError:
                    subs = None
            pcur.execute("""
                INSERT INTO finley_transactions
                    (id, date, amount, memo, payee_id, payee_name,
                     category_id, category_name, account_id, account_name,
                     approved, cleared, flag_color, transfer_account_id,
                     subtransactions)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (r["id"], r["date"], r["amount"], r["memo"],
                  r["payee_id"], r["payee_name"],
                  r["category_id"], r["category_name"],
                  r["account_id"], r["account_name"],
                  bool(r["approved"]), r["cleared"],
                  r["flag_color"], r["transfer_account_id"],
                  json.dumps(subs) if subs else None))
        logger.info("  Transactions: %d rows", len(rows))

        # --- Scheduled Transactions ---
        rows = sconn.execute("SELECT * FROM scheduled_transactions").fetchall()
        for r in rows:
            pcur.execute("""
                INSERT INTO finley_scheduled_transactions
                    (id, date_first, date_next, frequency, amount,
                     payee_name, category_name, memo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (r["id"], r["date_first"], r["date_next"],
                  r["frequency"], r["amount"],
                  r["payee_name"], r["category_name"], r["memo"]))
        logger.info("  Scheduled transactions: %d rows", len(rows))

        # --- Sync log (no PK conflict — just insert all) ---
        rows = sconn.execute("SELECT * FROM sync_log").fetchall()
        for r in rows:
            pcur.execute("""
                INSERT INTO finley_sync_log
                    (endpoint, server_knowledge, synced_at, records_updated)
                VALUES (%s, %s, %s, %s)
            """, (r["endpoint"], r["server_knowledge"],
                  r["synced_at"], r["records_updated"]))
        logger.info("  Sync log: %d rows", len(rows))

        pconn.commit()
        logger.info("Legacy SQLite migration complete.")

    except Exception as e:
        pconn.rollback()
        logger.error("SQLite migration failed: %s", e)
        raise
    finally:
        sconn.close()
        pconn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Finley PostgreSQL migrations")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print SQL without executing")
    parser.add_argument("--seed", action="store_true",
                        help="Also migrate legacy SQLite data after applying migrations")
    args = parser.parse_args()

    cfg = _load_config()
    run_migrations(cfg, print_only=args.print_only)

    if args.seed and not args.print_only:
        migrate_sqlite_data(cfg)


if __name__ == "__main__":
    main()
