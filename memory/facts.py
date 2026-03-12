"""
memory/facts.py — SQLite-backed L2 fact store and L0/L1 turn log.

Tables:
  facts   — explicit "Remember that..." facts + system-seeded Tim facts
  turns   — raw conversation turns (user + assistant) per session
"""

from __future__ import annotations

import logging
import sqlite3
from typing import List

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT    NOT NULL,
    source     TEXT    DEFAULT 'user',
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    timestamp  TEXT    NOT NULL
);
"""


class FactStore:
    def __init__(self, db_path: str, logger: logging.Logger) -> None:
        self.logger = logger
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.logger.info("FactStore ready: %s", db_path)

    def add_fact(self, text: str, source: str = "user", timestamp: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO facts (text, source, created_at) VALUES (?, ?, ?)",
            (text, source, timestamp),
        )
        self.conn.commit()
        return cur.lastrowid

    def store_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        timestamp: str,
    ) -> None:
        self.conn.executemany(
            "INSERT INTO turns (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            [
                (session_id, "user", user_text, timestamp),
                (session_id, "assistant", assistant_text, timestamp),
            ],
        )
        self.conn.commit()

    def delete_matching(self, query: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM facts WHERE LOWER(text) LIKE ?",
            (f"%{query.lower()}%",),
        )
        self.conn.commit()
        return cur.rowcount

    def get_all_facts(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT text FROM facts ORDER BY created_at DESC"
        ).fetchall()
        return [r[0] for r in rows]

    def get_recent_turns(self, session_id: str, n: int = 6) -> List[dict]:
        """Return last n turns (user+assistant pairs) for a session."""
        rows = self.conn.execute(
            "SELECT role, content FROM turns WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, n),
        ).fetchall()
        rows.reverse()
        return [{"role": r[0], "content": r[1]} for r in rows]
