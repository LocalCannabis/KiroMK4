#!/usr/bin/env python3
"""
memory_cli.py — Kiro Memory Diagnostic Shell.

Interactive REPL for inspecting, querying, and managing all three memory layers:
  L0: Recent turns (SQLite turns table, per-session)
  L1: Vector store (ChromaDB, cosine similarity)
  L2: Structured facts (SQLite facts table)

Usage:
  conda run -n kiro_asr python memory_cli.py
  conda run -n kiro_asr python memory_cli.py --command "stats"

Commands:
  stats              — counts for facts, turns, sessions, vectors
  facts              — list all structured facts
  turns [n]          — show last n turns (default 20)
  sessions           — list all sessions with turn counts
  search <query>     — vector similarity search (shows what the LLM would see)
  retrieve <query>   — full retrieve() output exactly as injected into the prompt
  addfact <text>     — manually add a structured fact
  delfact <id>       — delete a fact by ID
  forget <query>     — run the forget pipeline (SQLite + ChromaDB)
  seed <yaml_path>   — bulk-load facts from a YAML file
  export             — dump all facts as YAML to stdout
  wipe-vectors       — ⚠ delete ALL vector embeddings (keeps SQLite)
  help               — show this help
  quit               — exit
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_components(cfg: Dict[str, Any], logger: logging.Logger):
    """Initialise FactStore and VectorStore directly (no full orchestrator needed)."""
    import sqlite3
    import chromadb
    from chromadb.utils import embedding_functions

    mem_cfg = cfg.get("memory", {})
    db_path = mem_cfg.get("sqlite_path", "./data/kiro.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)

    vec_cfg = mem_cfg.get("vector", {})
    chroma_path = vec_cfg.get("path", "./data/chroma")
    model_name = vec_cfg.get("embedding_model", "all-MiniLM-L6-v2")

    client = chromadb.PersistentClient(path=chroma_path)
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
    collection = client.get_or_create_collection(name="kiro_memory", embedding_function=ef)

    return conn, collection


# ======================================================================
# Commands
# ======================================================================

def cmd_stats(conn, collection):
    fact_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    turn_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    session_count = conn.execute("SELECT COUNT(DISTINCT session_id) FROM turns").fetchone()[0]
    vec_count = collection.count()
    fact_sources = conn.execute(
        "SELECT source, COUNT(*) FROM facts GROUP BY source ORDER BY source"
    ).fetchall()

    print(f"\n  📊 Memory Stats")
    print(f"  ─────────────────────────────")
    print(f"  L2 Facts:      {fact_count}")
    for src, cnt in fact_sources:
        print(f"    └─ {src}: {cnt}")
    print(f"  L0/L1 Turns:   {turn_count} (across {session_count} sessions)")
    print(f"  L1 Vectors:    {vec_count} docs in ChromaDB")
    print()


def cmd_facts(conn):
    rows = conn.execute(
        "SELECT id, text, source, created_at FROM facts ORDER BY id"
    ).fetchall()
    if not rows:
        print("\n  No facts stored.\n")
        return
    print(f"\n  📋 All Facts ({len(rows)})")
    print(f"  ─────────────────────────────")
    for fid, text, source, ts in rows:
        ts_short = ts[:19] if ts else "?"
        print(f"  [{fid:>3}] ({source:>8}) {text}")
        print(f"         created: {ts_short}")
    print()


def cmd_turns(conn, n: int = 20):
    rows = conn.execute(
        "SELECT id, session_id, role, content, timestamp FROM turns ORDER BY id DESC LIMIT ?",
        (n,),
    ).fetchall()
    rows.reverse()
    if not rows:
        print("\n  No turns recorded.\n")
        return
    print(f"\n  💬 Last {len(rows)} Turns")
    print(f"  ─────────────────────────────")
    for tid, sid, role, content, ts in rows:
        sid_short = sid[:8]
        ts_short = ts[11:19] if len(ts) > 19 else ts
        tag = "🧑" if role == "user" else "🤖"
        # Truncate long content for display
        display = content if len(content) <= 100 else content[:97] + "..."
        print(f"  {tag} [{sid_short}] {ts_short} | {display}")
    print()


def cmd_sessions(conn):
    rows = conn.execute(
        "SELECT session_id, COUNT(*) as cnt, MIN(timestamp), MAX(timestamp) "
        "FROM turns GROUP BY session_id ORDER BY MIN(timestamp)"
    ).fetchall()
    if not rows:
        print("\n  No sessions recorded.\n")
        return
    print(f"\n  🗂  Sessions ({len(rows)})")
    print(f"  ─────────────────────────────")
    for sid, cnt, first, last in rows:
        first_short = first[:19] if first else "?"
        last_short = last[11:19] if last and len(last) > 19 else last or "?"
        print(f"  {sid[:12]}…  turns={cnt:>4}  first={first_short}  last=…{last_short}")
    print()


def cmd_search(collection, query: str, n: int = 5):
    count = collection.count()
    if count == 0:
        print("\n  Vector store is empty.\n")
        return
    results = collection.query(
        query_texts=[query],
        n_results=min(n, count),
        include=["documents", "metadatas", "distances"],
    )
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    print(f"\n  🔍 Vector Search: \"{query}\" (top {len(docs)})")
    print(f"  ─────────────────────────────")
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
        doc_type = meta.get("type", "?")
        persona = meta.get("persona", "-")
        ts = meta.get("timestamp", "?")[:19]
        score = 1.0 - dist  # cosine distance → similarity
        display = doc if len(doc) <= 120 else doc[:117] + "..."
        print(f"  [{i+1}] sim={score:.3f} type={doc_type} persona={persona} ts={ts}")
        print(f"      {display}")
    print()


def cmd_retrieve(cfg, query: str):
    """Run the exact same retrieve() the LLM would see."""
    from memory.manager import MemoryManager
    logger = logging.getLogger("memory_cli")
    logger.setLevel(logging.WARNING)
    mm = MemoryManager(cfg, logger)
    result = mm.retrieve(query)
    if not result:
        print("\n  retrieve() returned empty string (no relevant memories).\n")
    else:
        print(f"\n  🧠 Prompt injection for: \"{query}\"")
        print(f"  ─────────────────────────────")
        for line in result.split("\n"):
            print(f"  {line}")
    print()


def cmd_addfact(conn, collection, text: str, source: str = "cli"):
    from datetime import datetime
    import hashlib, time
    ts = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO facts (text, source, created_at) VALUES (?, ?, ?)",
        (text, source, ts),
    )
    conn.commit()
    fid = cur.lastrowid
    # Also embed into vector store
    doc_id = hashlib.md5(f"[FACT] {text}{time.time()}".encode()).hexdigest()
    collection.add(
        documents=[f"[FACT] {text}"],
        metadatas=[{"type": "fact", "source": source, "timestamp": ts}],
        ids=[doc_id],
    )
    print(f"\n  ✅ Fact #{fid} stored: \"{text}\" (source={source})\n")


def cmd_delfact(conn, collection, fid: int):
    row = conn.execute("SELECT text FROM facts WHERE id = ?", (fid,)).fetchone()
    if not row:
        print(f"\n  ❌ No fact with id={fid}\n")
        return
    fact_text = row[0]
    conn.execute("DELETE FROM facts WHERE id = ?", (fid,))
    conn.commit()
    # Also remove from vector store
    _delete_vectors_matching(collection, f"[FACT] {fact_text}")
    print(f"\n  🗑  Deleted fact #{fid}: \"{fact_text}\"\n")


def cmd_forget(conn, collection, query: str):
    """Full forget pipeline: SQLite + ChromaDB."""
    # Find matching facts first
    rows = conn.execute(
        "SELECT id, text FROM facts WHERE LOWER(text) LIKE ?",
        (f"%{query.lower()}%",),
    ).fetchall()
    if not rows:
        print(f"\n  No facts matching \"{query}\".\n")
        return
    for fid, text in rows:
        conn.execute("DELETE FROM facts WHERE id = ?", (fid,))
        _delete_vectors_matching(collection, f"[FACT] {text}")
        print(f"  🗑  Deleted fact #{fid}: \"{text}\"")
    conn.commit()
    print(f"\n  Forgotten {len(rows)} fact(s) + their vector embeddings.\n")


def _delete_vectors_matching(collection, search_text: str):
    """Delete ChromaDB docs whose content matches the search text."""
    count = collection.count()
    if count == 0:
        return
    # Search for the vector then delete by ID
    results = collection.query(
        query_texts=[search_text],
        n_results=min(5, count),
        include=["documents"],
    )
    ids = results.get("ids", [[]])[0]
    docs = results.get("documents", [[]])[0]
    to_delete = []
    for doc_id, doc_text in zip(ids, docs):
        # Only delete if it's actually the fact (not just similar)
        if search_text.lower() in doc_text.lower() or doc_text.lower() in search_text.lower():
            to_delete.append(doc_id)
    if to_delete:
        collection.delete(ids=to_delete)


def cmd_seed(conn, collection, yaml_path: str):
    """Bulk-load facts from a YAML file."""
    path = Path(yaml_path)
    if not path.exists():
        print(f"\n  ❌ File not found: {yaml_path}\n")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    facts = data if isinstance(data, list) else data.get("facts", [])
    if not facts:
        print(f"\n  ❌ No facts found in {yaml_path}. Expected a list or {{facts: [...]}}.\n")
        return

    from datetime import datetime
    import hashlib, time
    ts = datetime.now().isoformat()
    loaded = 0
    for item in facts:
        text = item if isinstance(item, str) else item.get("text", "")
        source = "seed" if isinstance(item, str) else item.get("source", "seed")
        if not text:
            continue
        # Skip duplicates
        existing = conn.execute(
            "SELECT id FROM facts WHERE LOWER(text) = ?", (text.lower(),)
        ).fetchone()
        if existing:
            print(f"  ⏭  Skip (duplicate): {text}")
            continue
        cur = conn.execute(
            "INSERT INTO facts (text, source, created_at) VALUES (?, ?, ?)",
            (text, source, ts),
        )
        doc_id = hashlib.md5(f"[FACT] {text}{time.time()}".encode()).hexdigest()
        collection.add(
            documents=[f"[FACT] {text}"],
            metadatas=[{"type": "fact", "source": source, "timestamp": ts}],
            ids=[doc_id],
        )
        loaded += 1
    conn.commit()
    print(f"\n  ✅ Seeded {loaded} new facts from {yaml_path}\n")


def cmd_export(conn):
    rows = conn.execute(
        "SELECT text, source, created_at FROM facts ORDER BY id"
    ).fetchall()
    output = {"facts": [{"text": t, "source": s, "created_at": c} for t, s, c in rows]}
    print(yaml.dump(output, default_flow_style=False, sort_keys=False))


def cmd_wipe_vectors(collection):
    count = collection.count()
    if count == 0:
        print("\n  Vector store is already empty.\n")
        return
    confirm = input(f"  ⚠  Delete ALL {count} vector embeddings? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("  Aborted.\n")
        return
    # ChromaDB: delete all by getting all IDs
    all_ids = collection.get()["ids"]
    if all_ids:
        collection.delete(ids=all_ids)
    print(f"\n  🗑  Wiped {count} vectors from ChromaDB.\n")


def print_help():
    print("""
  Kiro Memory CLI — Commands:
  ─────────────────────────────
  stats              show memory layer counts
  facts              list all structured facts
  turns [n]          show last n turns (default 20)
  sessions           list all sessions
  search <query>     vector similarity search
  retrieve <query>   full prompt injection preview
  addfact <text>     add a structured fact
  delfact <id>       delete a fact by ID
  forget <query>     forget facts (SQLite + ChromaDB)
  seed <yaml_path>   bulk-load facts from YAML
  export             dump all facts as YAML
  wipe-vectors       delete ALL vector embeddings
  help               this message
  quit / exit        leave
""")


# ======================================================================
# Main REPL
# ======================================================================

def main():
    parser = argparse.ArgumentParser(description="Kiro Memory Diagnostic Shell")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--command", "-c", default=None, help="Run a single command and exit")
    args = parser.parse_args()

    cfg = load_config(args.config)
    logger = logging.getLogger("memory_cli")
    logger.setLevel(logging.WARNING)

    print("  ⏳ Loading vector store (this takes a moment)...")
    conn, collection = build_components(cfg, logger)
    print("  ✅ Ready.\n")

    if args.command:
        dispatch(args.command, conn, collection, cfg)
        return

    print_help()
    while True:
        try:
            raw = input("  memory> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye.\n")
            break
        if not raw:
            continue
        if raw.lower() in {"quit", "exit", "q"}:
            print("  Bye.\n")
            break
        dispatch(raw, conn, collection, cfg)


def dispatch(raw: str, conn, collection, cfg):
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "stats":
        cmd_stats(conn, collection)
    elif cmd == "facts":
        cmd_facts(conn)
    elif cmd == "turns":
        n = int(arg) if arg.isdigit() else 20
        cmd_turns(conn, n)
    elif cmd == "sessions":
        cmd_sessions(conn)
    elif cmd == "search":
        if not arg:
            print("  Usage: search <query>\n")
        else:
            cmd_search(collection, arg)
    elif cmd == "retrieve":
        if not arg:
            print("  Usage: retrieve <query>\n")
        else:
            cmd_retrieve(cfg, arg)
    elif cmd == "addfact":
        if not arg:
            print("  Usage: addfact <fact text>\n")
        else:
            cmd_addfact(conn, collection, arg)
    elif cmd == "delfact":
        if not arg or not arg.isdigit():
            print("  Usage: delfact <id>\n")
        else:
            cmd_delfact(conn, collection, int(arg))
    elif cmd == "forget":
        if not arg:
            print("  Usage: forget <query>\n")
        else:
            cmd_forget(conn, collection, arg)
    elif cmd == "seed":
        if not arg:
            print("  Usage: seed <path/to/facts.yaml>\n")
        else:
            cmd_seed(conn, collection, arg)
    elif cmd == "export":
        cmd_export(conn)
    elif cmd in {"wipe-vectors", "wipe"}:
        cmd_wipe_vectors(collection)
    elif cmd == "help":
        print_help()
    else:
        print(f"  Unknown command: {cmd}. Type 'help' for commands.\n")


if __name__ == "__main__":
    main()
