"""
kiro.cli.memory_commands — CLI commands for the memory subsystem.

Usage (from project root):
    python -m kiro.cli.memory_commands schema     # Apply schema
    python -m kiro.cli.memory_commands migrate    # Migrate legacy SQLite
    python -m kiro.cli.memory_commands maintain   # Run maintenance
    python -m kiro.cli.memory_commands fidelity   # Test all glasses
    python -m kiro.cli.memory_commands status     # Show memory stats
    python -m kiro.cli.memory_commands store      # Store a fact
    python -m kiro.cli.memory_commands query      # Query memory
    python -m kiro.cli.memory_commands cache      # Show cache stats
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Dict, Any

import yaml

logger = logging.getLogger("kiro.cli.memory")


def _load_db_cfg() -> Dict[str, Any]:
    """Load database config from config.yaml or environment."""
    try:
        with open("config.yaml", "r") as f:
            cfg = yaml.safe_load(f)
        db = cfg.get("database", cfg.get("memory", {}).get("database", {}))
        if db:
            return db
    except FileNotFoundError:
        pass

    # Fallback: try jack's config
    try:
        with open("jack/jack_config.example.yaml", "r") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("database", {})
    except FileNotFoundError:
        pass

    return {
        "host": "localhost",
        "port": 5432,
        "dbname": "kiro",
        "user": "kiro",
        "password": "",
    }


def cmd_schema(args, db_cfg):
    """Apply the memory schema to the database."""
    from kiro.memory.migration import run_schema
    run_schema(db_cfg)
    print("✓ Schema applied successfully")


def cmd_migrate(args, db_cfg):
    """Migrate legacy SQLite facts to PostgreSQL."""
    from kiro.memory.migration import migrate_legacy_sqlite
    sqlite_path = args.sqlite_path or "./data/kiro.db"
    count = migrate_legacy_sqlite(db_cfg, sqlite_path)
    print(f"✓ Migrated {count} facts from {sqlite_path}")


def cmd_maintain(args, db_cfg):
    """Run maintenance tasks."""
    from kiro.config.memory import MemoryConfigLoader
    from kiro.memory.glass_manager import GlassManager
    from kiro.memory.maintenance import run_maintenance

    config = MemoryConfigLoader(db_cfg)
    manager = GlassManager(db_cfg, config)
    try:
        summary = run_maintenance(manager, config)
        print("✓ Maintenance complete:")
        for k, v in summary.items():
            print(f"  {k}: {v}")
    finally:
        manager.close()


def cmd_fidelity(args, db_cfg):
    """Test fidelity of all active glasses."""
    from kiro.config.memory import MemoryConfigLoader
    from kiro.memory.glass_manager import GlassManager

    config = MemoryConfigLoader(db_cfg)
    manager = GlassManager(db_cfg, config)
    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(**{
            k: v for k, v in db_cfg.items()
            if k in ("host", "port", "dbname", "user", "password")
        })
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT g.id, g.label, g.fact_count, p.slug as persona
                FROM glasses g JOIN personas p ON g.persona_id = p.id
                WHERE g.status = 'active' AND g.fact_count > 0
                ORDER BY g.persona_id, g.id
            """)
            glasses = cur.fetchall()
        conn.close()

        for g in glasses:
            accuracy = manager.test_glass_fidelity(g["id"])
            status = "✓" if accuracy >= 0.8 else "⚠" if accuracy >= 0.5 else "✗"
            print(f"  {status} Glass {g['id']} [{g['persona']}] '{g['label']}' "
                  f"({g['fact_count']} facts): {accuracy:.1%}")
    finally:
        manager.close()


def cmd_status(args, db_cfg):
    """Show memory system status."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(**{
        k: v for k, v in db_cfg.items()
        if k in ("host", "port", "dbname", "user", "password")
    })
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Persona stats
        cur.execute("""
            SELECT p.slug, p.display_name,
                   COUNT(DISTINCT g.id) FILTER (WHERE g.status = 'active') as active_glasses,
                   COUNT(DISTINCT f.id) FILTER (WHERE f.retired_at IS NULL) as active_facts,
                   COUNT(DISTINCT f.id) FILTER (WHERE f.promoted = TRUE AND f.retired_at IS NULL) as promoted_facts
            FROM personas p
            LEFT JOIN glasses g ON g.persona_id = p.id
            LEFT JOIN facts f ON f.persona_id = p.id
            GROUP BY p.id, p.slug, p.display_name
            ORDER BY p.id
        """)
        personas = cur.fetchall()

        # Global stats
        cur.execute("SELECT COUNT(*) FROM facts WHERE retired_at IS NULL")
        total_facts = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) FROM glasses WHERE status = 'active'")
        total_glasses = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) FROM facts WHERE promoted = TRUE AND retired_at IS NULL")
        total_promoted = cur.fetchone()["count"]

    conn.close()

    print(f"\n  KIRO MEMORY STATUS")
    print(f"  ══════════════════════════════════════")
    print(f"  Total active facts:    {total_facts}")
    print(f"  Total active glasses:  {total_glasses}")
    print(f"  Total promoted (T0):   {total_promoted}")
    print(f"  ──────────────────────────────────────")

    for p in personas:
        print(f"  {p['display_name']:>8s}: "
              f"{p['active_facts']:>4d} facts, "
              f"{p['active_glasses']:>3d} glasses, "
              f"{p['promoted_facts']:>3d} promoted")

    print()


def cmd_store(args, db_cfg):
    """Store a fact manually."""
    from kiro.config.memory import MemoryConfigLoader
    from kiro.memory.glass_manager import GlassManager

    config = MemoryConfigLoader(db_cfg)
    manager = GlassManager(db_cfg, config)
    try:
        # Resolve persona
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(**{
            k: v for k, v in db_cfg.items()
            if k in ("host", "port", "dbname", "user", "password")
        })
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM personas WHERE slug = %s", (args.persona,))
            row = cur.fetchone()
        conn.close()

        if not row:
            print(f"✗ Unknown persona: {args.persona}")
            return

        fact = manager.store_fact(
            persona_id=row["id"],
            hrr_key=args.key,
            hrr_value=args.value,
            source="cli",
        )
        print(f"✓ Stored fact {fact.id}: {fact.hrr_key} → {fact.hrr_value}")
    finally:
        manager.close()


def cmd_query(args, db_cfg):
    """Query memory via the four-tier pipeline."""
    from kiro.config.memory import MemoryConfigLoader
    from kiro.memory.glass_manager import GlassManager
    from kiro.memory.retrieval import retrieve

    config = MemoryConfigLoader(db_cfg)
    manager = GlassManager(db_cfg, config)
    try:
        # Resolve persona
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(**{
            k: v for k, v in db_cfg.items()
            if k in ("host", "port", "dbname", "user", "password")
        })
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM personas WHERE slug = %s", (args.persona,))
            row = cur.fetchone()
        conn.close()

        if not row:
            print(f"✗ Unknown persona: {args.persona}")
            return

        ctx = retrieve(
            query=args.query_text,
            persona_id=row["id"],
            manager=manager,
            config=config,
            db_cfg=db_cfg,
            embedding_fn=None,  # Tier 1 skipped without embeddings
            top_k=args.top_k,
        )

        print(f"\n  Query: {ctx.query}")
        print(f"  Tier reached: {ctx.tier_reached}")
        print(f"  Time: {ctx.total_ms:.1f}ms")
        print(f"  Results ({len(ctx.results)}):")
        for r in ctx.results:
            print(f"    [{r.tier}] {r.score:.3f}  {r.fact.hrr_key}: {r.fact.hrr_value[:80]}")
        print()
    finally:
        manager.close()


def cmd_cache(args, db_cfg):
    """Show cache statistics."""
    from kiro.config.memory import MemoryConfigLoader
    from kiro.memory.glass_manager import GlassManager

    config = MemoryConfigLoader(db_cfg)
    manager = GlassManager(db_cfg, config)
    try:
        stats = manager.cache_stats()
        print("\n  GLASS CACHE STATS")
        print("  ═════════════════════")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print()
    finally:
        manager.close()


def main():
    parser = argparse.ArgumentParser(
        prog="kiro-memory",
        description="KIRO memory subsystem CLI",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # schema
    sub.add_parser("schema", help="Apply database schema")

    # migrate
    p_migrate = sub.add_parser("migrate", help="Migrate legacy SQLite facts")
    p_migrate.add_argument("--sqlite-path", default="./data/kiro.db")

    # maintain
    sub.add_parser("maintain", help="Run maintenance tasks")

    # fidelity
    sub.add_parser("fidelity", help="Test glass fidelity")

    # status
    sub.add_parser("status", help="Show memory system status")

    # store
    p_store = sub.add_parser("store", help="Store a fact")
    p_store.add_argument("--persona", default="kiro")
    p_store.add_argument("--key", required=True)
    p_store.add_argument("--value", required=True)

    # query
    p_query = sub.add_parser("query", help="Query memory")
    p_query.add_argument("query_text")
    p_query.add_argument("--persona", default="kiro")
    p_query.add_argument("--top-k", type=int, default=5)

    # cache
    sub.add_parser("cache", help="Show cache stats")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_cfg = _load_db_cfg()

    commands = {
        "schema": cmd_schema,
        "migrate": cmd_migrate,
        "maintain": cmd_maintain,
        "fidelity": cmd_fidelity,
        "status": cmd_status,
        "store": cmd_store,
        "query": cmd_query,
        "cache": cmd_cache,
    }

    cmd_fn = commands.get(args.command)
    if cmd_fn:
        cmd_fn(args, db_cfg)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
