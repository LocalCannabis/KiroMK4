"""
ambient/db.py — PostgreSQL database access layer for the Ambient Intelligence Layer.

Handles all CRUD operations for kiro_events, kiro_insights, kiro_briefings,
kiro_ambient_config, and kiro_ambient_log.

Uses psycopg2 with ThreadedConnectionPool, matching Jack's DB pattern.
Shares the same kiro database.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("ambient.db")


class AmbientDB:
    """PostgreSQL access for the ambient intelligence layer."""

    def __init__(self, db_cfg: Optional[Dict[str, Any]] = None) -> None:
        if db_cfg is None:
            db_cfg = self._load_db_config()
        self._pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            host=db_cfg.get("host", "localhost"),
            port=int(db_cfg.get("port", 5432)),
            dbname=db_cfg.get("dbname", "kiro"),
            user=db_cfg.get("user", "kiro"),
            password=db_cfg.get("password", ""),
        )
        logger.info(
            "Ambient DB pool created: %s@%s:%s/%s",
            db_cfg.get("user", "kiro"),
            db_cfg.get("host", "localhost"),
            db_cfg.get("port", 5432),
            db_cfg.get("dbname", "kiro"),
        )

    @staticmethod
    def _load_db_config() -> Dict[str, Any]:
        """Load DB config from Jack's config (shared database)."""
        try:
            from jack.config import load_jack_config
            cfg = load_jack_config()
            return cfg.get("database", {})
        except Exception:
            return {
                "host": "localhost",
                "port": 5432,
                "dbname": "kiro",
                "user": "kiro",
                "password": "",
            }

    def _conn(self):
        return self._pool.getconn()

    def _put(self, conn):
        self._pool.putconn(conn)

    def close(self):
        self._pool.closeall()

    # =========================================================================
    # Config — kiro_ambient_config
    # =========================================================================

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a config value by key. Returns parsed JSON value."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT config_value FROM kiro_ambient_config WHERE config_key = %s",
                    (key,),
                )
                row = cur.fetchone()
                if row is None:
                    return default
                val = row["config_value"]
                # JSONB comes back as Python object already via psycopg2
                return val
        finally:
            self._put(conn)

    def set_config(self, key: str, value: Any, description: str = None) -> None:
        """Upsert a config value."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kiro_ambient_config (config_key, config_value, description, updated_at)
                    VALUES (%s, %s::jsonb, %s, NOW())
                    ON CONFLICT (config_key) DO UPDATE SET
                        config_value = EXCLUDED.config_value,
                        description = COALESCE(EXCLUDED.description, kiro_ambient_config.description),
                        updated_at = NOW()
                """, (key, json.dumps(value), description))
            conn.commit()
        finally:
            self._put(conn)

    def get_all_config(self) -> Dict[str, Any]:
        """Get all config as a dict."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT config_key, config_value FROM kiro_ambient_config")
                return {row["config_key"]: row["config_value"] for row in cur.fetchall()}
        finally:
            self._put(conn)

    # =========================================================================
    # Events — kiro_events
    # =========================================================================

    def insert_event(
        self,
        source: str,
        source_id: str,
        event_type: str,
        occurred_at: datetime,
        metadata: Dict[str, Any],
        raw_content: Optional[str] = None,
    ) -> Optional[int]:
        """
        Insert a raw event. Returns the event ID, or None if duplicate.

        The UNIQUE(source, source_id) constraint prevents duplicates — on conflict
        we silently skip.
        """
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kiro_events
                        (source, source_id, event_type, occurred_at, metadata, raw_content)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (source, source_id) DO NOTHING
                    RETURNING id
                """, (
                    source, source_id, event_type, occurred_at,
                    json.dumps(metadata), raw_content,
                ))
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_unprocessed_events(self, limit: int = 100, source: Optional[str] = None) -> List[Dict]:
        """Get unprocessed events, optionally filtered by source."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if source:
                    cur.execute("""
                        SELECT * FROM kiro_events
                        WHERE processed = FALSE AND source = %s
                        ORDER BY occurred_at ASC
                        LIMIT %s
                    """, (source, limit))
                else:
                    cur.execute("""
                        SELECT * FROM kiro_events
                        WHERE processed = FALSE
                        ORDER BY occurred_at ASC
                        LIMIT %s
                    """, (limit,))
                return [dict(row) for row in cur.fetchall()]
        finally:
            self._put(conn)

    def mark_event_processed(self, event_id: int, tags: List[str]) -> None:
        """Mark an event as processed with extracted tags."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE kiro_events
                    SET processed = TRUE, processed_at = NOW(), tags = %s
                    WHERE id = %s
                """, (tags, event_id))
            conn.commit()
        finally:
            self._put(conn)

    def get_events_in_window(
        self,
        days: int = 7,
        source: Optional[str] = None,
        processed_only: bool = True,
    ) -> List[Dict]:
        """Get events from the last N days."""
        conn = self._conn()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = ["occurred_at >= %s"]
                params: list = [cutoff]
                if processed_only:
                    conditions.append("processed = TRUE")
                if source:
                    conditions.append("source = %s")
                    params.append(source)
                where = " AND ".join(conditions)
                cur.execute(
                    f"SELECT * FROM kiro_events WHERE {where} ORDER BY occurred_at DESC",
                    params,
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            self._put(conn)

    def get_events_by_tags(self, tags: List[str], days: int = 7) -> List[Dict]:
        """Get events that have any of the specified tags, within a time window."""
        conn = self._conn()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM kiro_events
                    WHERE tags && %s AND occurred_at >= %s
                    ORDER BY occurred_at DESC
                """, (tags, cutoff))
                return [dict(row) for row in cur.fetchall()]
        finally:
            self._put(conn)

    # =========================================================================
    # Insights — kiro_insights
    # =========================================================================

    def insert_insight(
        self,
        insight_type: str,
        summary: str,
        detail: Optional[str] = None,
        persona: Optional[str] = None,
        confidence: str = "medium",
        priority: int = 5,
        source_event_ids: Optional[List[int]] = None,
        related_insight_ids: Optional[List[int]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None,
        evolved_from: Optional[int] = None,
    ) -> int:
        """Insert a new insight. Returns the insight ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kiro_insights
                        (insight_type, summary, detail, persona, confidence, priority,
                         source_event_ids, related_insight_ids, tags, metadata,
                         expires_at, evolved_from)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    RETURNING id
                """, (
                    insight_type, summary, detail, persona, confidence, priority,
                    source_event_ids or [], related_insight_ids or [],
                    tags or [], json.dumps(metadata or {}),
                    expires_at, evolved_from,
                ))
                insight_id = cur.fetchone()[0]
            conn.commit()
            return insight_id
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_unsurfaced_insights(
        self,
        max_priority: int = 6,
        limit: int = 10,
        persona: Optional[str] = None,
    ) -> List[Dict]:
        """Get unsurfaced, non-dismissed insights at or below the priority threshold."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = [
                    "surfaced = FALSE",
                    "dismissed = FALSE",
                    "priority <= %s",
                    "(expires_at IS NULL OR expires_at > NOW())",
                    "superseded_by IS NULL",
                ]
                params: list = [max_priority]
                if persona:
                    conditions.append("persona = %s")
                    params.append(persona)
                params.append(limit)
                where = " AND ".join(conditions)
                cur.execute(
                    f"SELECT * FROM kiro_insights WHERE {where} ORDER BY priority ASC, created_at ASC LIMIT %s",
                    params,
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            self._put(conn)

    def get_recent_insights(
        self,
        hours: int = 24,
        persona: Optional[str] = None,
    ) -> List[Dict]:
        """Get insights created in the last N hours."""
        conn = self._conn()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = ["created_at >= %s"]
                params: list = [cutoff]
                if persona:
                    conditions.append("persona = %s")
                    params.append(persona)
                where = " AND ".join(conditions)
                cur.execute(
                    f"SELECT * FROM kiro_insights WHERE {where} ORDER BY priority ASC, created_at DESC",
                    params,
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            self._put(conn)

    def mark_insights_surfaced(self, insight_ids: List[int], briefing_id: int) -> None:
        """Mark insights as surfaced in a briefing."""
        if not insight_ids:
            return
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE kiro_insights
                    SET surfaced = TRUE, surfaced_at = NOW(), surfaced_in = %s
                    WHERE id = ANY(%s)
                """, (briefing_id, insight_ids))
            conn.commit()
        finally:
            self._put(conn)

    def dismiss_insight(self, insight_id: int) -> None:
        """Mark an insight as dismissed by Tim."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE kiro_insights SET dismissed = TRUE WHERE id = %s",
                    (insight_id,),
                )
            conn.commit()
        finally:
            self._put(conn)

    def supersede_insight(self, old_id: int, new_id: int) -> None:
        """Mark an old insight as superseded by a new one."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE kiro_insights SET superseded_by = %s WHERE id = %s",
                    (new_id, old_id),
                )
            conn.commit()
        finally:
            self._put(conn)

    def find_similar_unsurfaced_insight(
        self,
        insight_type: str,
        tags: List[str],
        persona: Optional[str] = None,
        hours: int = 48,
    ) -> Optional[Dict]:
        """Find an existing unsurfaced insight with overlapping tags (for dedup)."""
        conn = self._conn()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                conditions = [
                    "insight_type = %s",
                    "surfaced = FALSE",
                    "dismissed = FALSE",
                    "superseded_by IS NULL",
                    "created_at >= %s",
                    "tags && %s",
                ]
                params: list = [insight_type, cutoff, tags]
                if persona:
                    conditions.append("persona = %s")
                    params.append(persona)
                where = " AND ".join(conditions)
                cur.execute(
                    f"SELECT * FROM kiro_insights WHERE {where} ORDER BY created_at DESC LIMIT 1",
                    params,
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    def update_insight_events(self, insight_id: int, new_event_ids: List[int]) -> None:
        """Append source event IDs to an existing insight (for dedup/merge)."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE kiro_insights
                    SET source_event_ids = source_event_ids || %s,
                        metadata = metadata || '{"updated": true}'::jsonb
                    WHERE id = %s
                """, (new_event_ids, insight_id))
            conn.commit()
        finally:
            self._put(conn)

    # =========================================================================
    # Briefings — kiro_briefings
    # =========================================================================

    def insert_briefing(
        self,
        briefing_type: str,
        insight_ids: List[int],
        briefing_text: str,
        persona_segments: Optional[Dict] = None,
        delivery_method: str = "voice",
    ) -> int:
        """Insert a briefing and mark included insights as surfaced. Returns briefing ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kiro_briefings
                        (briefing_type, insight_ids, briefing_text, persona_segments, delivery_method)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    RETURNING id
                """, (
                    briefing_type, insight_ids, briefing_text,
                    json.dumps(persona_segments or {}), delivery_method,
                ))
                briefing_id = cur.fetchone()[0]
            conn.commit()

            # Mark all included insights as surfaced
            self.mark_insights_surfaced(insight_ids, briefing_id)

            return briefing_id
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def record_briefing_feedback(
        self,
        briefing_id: int,
        feedback: str,
        notes: Optional[str] = None,
    ) -> None:
        """Record Tim's feedback on a briefing."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE kiro_briefings
                    SET feedback = %s, notes = %s
                    WHERE id = %s
                """, (feedback, notes, briefing_id))
            conn.commit()
        finally:
            self._put(conn)

    def get_last_briefing(self, briefing_type: Optional[str] = None) -> Optional[Dict]:
        """Get the most recent briefing, optionally filtered by type."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if briefing_type:
                    cur.execute("""
                        SELECT * FROM kiro_briefings
                        WHERE briefing_type = %s
                        ORDER BY delivered_at DESC LIMIT 1
                    """, (briefing_type,))
                else:
                    cur.execute("""
                        SELECT * FROM kiro_briefings
                        ORDER BY delivered_at DESC LIMIT 1
                    """)
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    # =========================================================================
    # Content Purging
    # =========================================================================

    def purge_old_content(self, source: str, hours: int = 72) -> int:
        """Purge raw_content from processed events older than N hours. Returns count."""
        conn = self._conn()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE kiro_events
                    SET raw_content = NULL, content_purged = TRUE
                    WHERE source = %s
                      AND processed = TRUE
                      AND content_purged = FALSE
                      AND processed_at < %s
                      AND raw_content IS NOT NULL
                    RETURNING id
                """, (source, cutoff))
                purged = cur.fetchall()
            conn.commit()
            return len(purged)
        finally:
            self._put(conn)

    # =========================================================================
    # Audit Log — kiro_ambient_log
    # =========================================================================

    def log(self, worker: str, level: str, message: str, metadata: Optional[Dict] = None) -> None:
        """Write an audit log entry."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kiro_ambient_log (worker, level, message, metadata)
                    VALUES (%s, %s, %s, %s::jsonb)
                """, (worker, level, message, json.dumps(metadata or {})))
            conn.commit()
        finally:
            self._put(conn)
