#!/usr/bin/env python3
"""
ambient/ingest/ingest_whatsapp.py — WhatsApp message ingestion worker (SQLite bridge).

Reads from the existing whatsapp-web.js Node.js listener's SQLite database
and writes messages as kiro_events with source='whatsapp'.

This is a temporary bridge — reads from SQLite, writes to PostgreSQL.
The WhatsApp SQLite DB location is configurable.

Usage:
    python -m ambient.ingest.ingest_whatsapp
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.ingest.whatsapp")

# Default location for the WhatsApp SQLite DB from the Node.js listener
DEFAULT_WHATSAPP_DB = os.path.expanduser("~/.kiro/whatsapp_messages.db")


class WhatsAppIngestionWorker(BaseWorker):
    """
    Bridge: reads from WhatsApp Node.js listener's SQLite store
    and ingests messages into kiro_events (PostgreSQL).

    Tracks the last seen message ID/timestamp to avoid reprocessing.
    """

    worker_name = "ingest_whatsapp"
    default_interval_seconds = 30  # Check frequently — messages are real-time

    def __init__(self, db_path: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._wa_db_path = db_path or DEFAULT_WHATSAPP_DB
        self._last_seen_ts: Optional[str] = None

    def setup(self) -> None:
        """Verify WhatsApp SQLite DB exists and determine last ingested message."""
        if not Path(self._wa_db_path).exists():
            self.audit_log("WARNING",
                f"WhatsApp SQLite DB not found at {self._wa_db_path}. "
                "Worker will wait for it to appear.")

        # Find last ingested WhatsApp message timestamp
        conn = self.db._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT MAX(occurred_at) FROM kiro_events
                    WHERE source = 'whatsapp'
                """)
                row = cur.fetchone()
                if row and row[0]:
                    self._last_seen_ts = row[0].isoformat()
        finally:
            self.db._put(conn)

        self.audit_log("INFO", f"WhatsApp ingestion initialized (db={self._wa_db_path})")

    def process(self) -> None:
        """Read new messages from WhatsApp SQLite and ingest."""
        if not Path(self._wa_db_path).exists():
            logger.debug("WhatsApp DB not yet available")
            return

        try:
            wa_conn = sqlite3.connect(self._wa_db_path)
            wa_conn.row_factory = sqlite3.Row
        except Exception as e:
            self.audit_log("ERROR", f"Failed to open WhatsApp DB: {e}")
            return

        try:
            cur = wa_conn.cursor()

            # Attempt to read messages — table schema depends on the Node.js listener
            # Common schema: messages(id, chat_id, sender, body, timestamp, ...)
            # We try a flexible query that works with common whatsapp-web.js storage patterns
            try:
                if self._last_seen_ts:
                    cur.execute("""
                        SELECT * FROM messages
                        WHERE timestamp > ?
                        ORDER BY timestamp ASC
                        LIMIT 500
                    """, (self._last_seen_ts,))
                else:
                    # First run — get last 24 hours
                    cur.execute("""
                        SELECT * FROM messages
                        ORDER BY timestamp DESC
                        LIMIT 500
                    """)
            except sqlite3.OperationalError as e:
                # Table might not exist yet or have different schema
                logger.debug("WhatsApp messages table query failed: %s", e)
                return

            rows = cur.fetchall()
            if not rows:
                logger.debug("No new WhatsApp messages")
                return

            ingested = 0
            for row in rows:
                row_dict = dict(row)
                msg_id = row_dict.get("id") or row_dict.get("message_id", "")
                body = row_dict.get("body") or row_dict.get("content", "")
                sender = row_dict.get("sender") or row_dict.get("from", "")
                chat_id = row_dict.get("chat_id") or row_dict.get("group_id", "")
                chat_name = row_dict.get("chat_name") or row_dict.get("group_name", "")
                timestamp = row_dict.get("timestamp", "")

                # Parse timestamp
                try:
                    if isinstance(timestamp, (int, float)):
                        occurred_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    else:
                        occurred_at = datetime.fromisoformat(str(timestamp))
                except (ValueError, TypeError, OSError):
                    occurred_at = datetime.now(timezone.utc)

                source_id = f"wa_{msg_id}" if msg_id else f"wa_{chat_id}_{timestamp}"

                metadata = {
                    "message_id": str(msg_id),
                    "chat_id": str(chat_id),
                    "chat_name": chat_name,
                    "sender": sender,
                    "has_media": bool(row_dict.get("has_media") or row_dict.get("media_url")),
                    "is_group": bool(chat_name),
                }

                event_id = self.db.insert_event(
                    source="whatsapp",
                    source_id=source_id,
                    event_type="message",
                    occurred_at=occurred_at,
                    metadata=metadata,
                    raw_content=body[:5000] if body else None,
                )
                if event_id:
                    ingested += 1

                # Track latest timestamp
                ts_str = occurred_at.isoformat()
                if self._last_seen_ts is None or ts_str > self._last_seen_ts:
                    self._last_seen_ts = ts_str

            if ingested > 0:
                self.audit_log("INFO", f"Ingested {ingested} WhatsApp messages", {
                    "count": ingested,
                })

        finally:
            wa_conn.close()


def main():
    worker = WhatsAppIngestionWorker()
    worker.run()


if __name__ == "__main__":
    main()
