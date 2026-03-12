"""
tools/notes_lists.py — Note capture and named list management via SQLite.

Both use kiro.db. Notes are tagged free-form entries. Lists are named
collections (grocery, todo, materials, etc.) with per-item storage.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT    NOT NULL,
    tags       TEXT    DEFAULT '',
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS list_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    list_name  TEXT    NOT NULL,
    item       TEXT    NOT NULL,
    added_at   TEXT    NOT NULL
);
"""


class NotesAndLists:
    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    def add_note(self, content: str, tags: str = "") -> str:
        ts = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO notes (content, tags, created_at) VALUES (?, ?, ?)",
            (content, tags, ts),
        )
        self.conn.commit()
        preview = content[:60] + ("…" if len(content) > 60 else "")
        return f"Note saved: {preview}"

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    def add_item(self, list_name: str, item: str) -> str:
        ts = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO list_items (list_name, item, added_at) VALUES (?, ?, ?)",
            (list_name.lower(), item, ts),
        )
        self.conn.commit()
        return f"Added '{item}' to {list_name}."

    def remove_item(self, list_name: str, item: str) -> str:
        cur = self.conn.execute(
            "DELETE FROM list_items WHERE list_name=? AND LOWER(item) LIKE ?",
            (list_name.lower(), f"%{item.lower()}%"),
        )
        self.conn.commit()
        if cur.rowcount:
            return f"Removed '{item}' from {list_name}."
        return f"Couldn't find '{item}' in {list_name}."

    def get_list(self, list_name: str) -> str:
        rows = self.conn.execute(
            "SELECT item FROM list_items WHERE list_name=? ORDER BY id",
            (list_name.lower(),),
        ).fetchall()
        if not rows:
            return f"The {list_name} list is empty."
        items = [r[0] for r in rows]
        return f"{list_name.capitalize()} list: {', '.join(items)}."

    def clear_list(self, list_name: str) -> str:
        self.conn.execute(
            "DELETE FROM list_items WHERE list_name=?",
            (list_name.lower(),),
        )
        self.conn.commit()
        return f"Cleared the {list_name} list."
