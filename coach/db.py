"""
coach/db.py — PostgreSQL database access layer for Coach executive function data.

Projects, tasks, captures, daily plans, weekly reviews, and knowledge
all live in the shared kiro PostgreSQL database.

Uses psycopg2 + ThreadedConnectionPool (same pattern as Finley and Jack).
Schema is managed by coach/migrate.py — never created inline.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("kiro.coach.db")


class CoachDB:
    """PostgreSQL access for Coach's executive function support data."""

    def __init__(self, cfg: Dict[str, Any] | None = None):
        if cfg is None:
            from .config import load_db_config
            cfg = load_db_config()
        db_cfg = cfg.get("database", {})
        self._pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            host=db_cfg.get("host", "localhost"),
            port=int(db_cfg.get("port", 5432)),
            dbname=db_cfg.get("dbname", "kiro"),
            user=db_cfg.get("user", "kiro"),
            password=db_cfg.get("password", ""),
        )
        logger.info(
            "Coach DB pool created: %s@%s:%s/%s",
            db_cfg.get("user", "kiro"),
            db_cfg.get("host", "localhost"),
            db_cfg.get("port", 5432),
            db_cfg.get("dbname", "kiro"),
        )

    def _conn(self):
        """Get a connection from the pool."""
        return self._pool.getconn()

    def _put(self, conn):
        """Return a connection to the pool."""
        self._pool.putconn(conn)

    def close(self):
        """Shutdown the connection pool."""
        self._pool.closeall()

    # ═══════════════════════════════════════════════════════════════════
    # PROJECTS
    # ═══════════════════════════════════════════════════════════════════

    def create_project(
        self,
        name: str,
        description: str = "",
        priority: int = 3,
        area: str = "general",
        status: str = "active",
    ) -> int:
        """Create a new project. Returns the project ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO coach_projects
                        (name, description, priority, area, status)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (name, description, priority, area, status))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Get a single project by ID."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM coach_projects WHERE id = %s", (project_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    def list_projects(self, status: str = "active") -> List[Dict[str, Any]]:
        """List projects by status."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM coach_projects
                    WHERE status = %s
                    ORDER BY priority ASC, updated_at DESC
                """, (status,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def update_project(self, project_id: int, **kwargs) -> bool:
        """Update project fields. Returns True if updated."""
        allowed = {"name", "description", "status", "priority", "area", "notes"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        if updates.get("status") == "completed":
            updates["completed_at"] = datetime.now()
        updates["updated_at"] = datetime.now()

        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [project_id]

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE coach_projects SET {set_clause} WHERE id = %s",
                    values,
                )
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ═══════════════════════════════════════════════════════════════════
    # TASKS
    # ═══════════════════════════════════════════════════════════════════

    def create_task(
        self,
        title: str,
        project_id: Optional[int] = None,
        status: str = "inbox",
        priority: int = 3,
        energy_level: str = "medium",
        context: Optional[List[str]] = None,
        estimated_minutes: Optional[int] = None,
        due_date: Optional[str] = None,
        notes: str = "",
    ) -> int:
        """Create a new task. Returns the task ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO coach_tasks
                        (title, project_id, status, priority, energy_level,
                         context, estimated_minutes, due_date, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (title, project_id, status, priority, energy_level,
                      context or [], estimated_minutes, due_date, notes))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get a single task by ID."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM coach_tasks WHERE id = %s", (task_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    def list_tasks(
        self,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
        energy_level: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List tasks with optional filters."""
        conditions = []
        params: list = []

        if status:
            conditions.append("t.status = %s")
            params.append(status)
        if project_id is not None:
            conditions.append("t.project_id = %s")
            params.append(project_id)
        if energy_level:
            conditions.append("t.energy_level = %s")
            params.append(energy_level)

        where = " AND ".join(conditions) if conditions else "TRUE"
        params.append(limit)

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT t.*, p.name AS project_name
                    FROM coach_tasks t
                    LEFT JOIN coach_projects p ON t.project_id = p.id
                    WHERE {where}
                    ORDER BY
                        CASE t.status
                            WHEN 'next' THEN 1
                            WHEN 'inbox' THEN 2
                            WHEN 'waiting' THEN 3
                            WHEN 'someday' THEN 4
                            ELSE 5
                        END,
                        t.priority ASC,
                        t.due_date ASC NULLS LAST,
                        t.updated_at DESC
                    LIMIT %s
                """, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def update_task(self, task_id: int, **kwargs) -> bool:
        """Update task fields. Returns True if updated."""
        allowed = {
            "title", "project_id", "status", "priority", "energy_level",
            "context", "estimated_minutes", "due_date", "waiting_on", "notes",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        if updates.get("status") == "done":
            updates["completed_at"] = datetime.now()
        updates["updated_at"] = datetime.now()

        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [task_id]

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE coach_tasks SET {set_clause} WHERE id = %s",
                    values,
                )
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def complete_task(self, task_id: int) -> bool:
        """Mark a task done."""
        return self.update_task(task_id, status="done")

    def get_next_actions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get all tasks with status='next', ordered by priority."""
        return self.list_tasks(status="next", limit=limit)

    def get_inbox(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get unprocessed inbox items."""
        return self.list_tasks(status="inbox", limit=limit)

    def get_tasks_due_soon(self, days: int = 3) -> List[Dict[str, Any]]:
        """Get tasks with due dates in the next N days."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT t.*, p.name AS project_name
                    FROM coach_tasks t
                    LEFT JOIN coach_projects p ON t.project_id = p.id
                    WHERE t.due_date IS NOT NULL
                      AND t.due_date <= CURRENT_DATE + %s
                      AND t.status NOT IN ('done', 'dropped')
                    ORDER BY t.due_date ASC, t.priority ASC
                """, (days,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_waiting_for(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get tasks blocked on someone else."""
        return self.list_tasks(status="waiting", limit=limit)

    # ═══════════════════════════════════════════════════════════════════
    # CAPTURES — Voice dumps and quick text before processing
    # ═══════════════════════════════════════════════════════════════════

    def add_capture(self, raw_text: str, source: str = "voice") -> int:
        """Store a raw capture. Returns the capture ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO coach_captures (raw_text, source)
                    VALUES (%s, %s) RETURNING id
                """, (raw_text, source))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_unprocessed_captures(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get captures that haven't been processed into tasks yet."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM coach_captures
                    WHERE processed = FALSE
                    ORDER BY created_at ASC LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def mark_capture_processed(self, capture_id: int, task_ids: List[int]) -> None:
        """Mark a capture as processed, linking to extracted task IDs."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE coach_captures
                    SET processed = TRUE, extracted_tasks = %s
                    WHERE id = %s
                """, (task_ids, capture_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ═══════════════════════════════════════════════════════════════════
    # DAILY PLANS
    # ═══════════════════════════════════════════════════════════════════

    def set_daily_plan(
        self,
        plan_date: Optional[str] = None,
        task_ids: Optional[List[int]] = None,
        energy_start: Optional[str] = None,
    ) -> int:
        """Create or update today's plan. Returns plan ID."""
        if plan_date is None:
            plan_date = date.today().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO coach_daily_plans (plan_date, top_tasks, energy_start)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plan_date) DO UPDATE
                    SET top_tasks = EXCLUDED.top_tasks,
                        energy_start = COALESCE(EXCLUDED.energy_start, coach_daily_plans.energy_start),
                        updated_at = NOW()
                    RETURNING id
                """, (plan_date, task_ids or [], energy_start))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_daily_plan(self, plan_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get the plan for a given date (default: today)."""
        if plan_date is None:
            plan_date = date.today().isoformat()
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM coach_daily_plans WHERE plan_date = %s
                """, (plan_date,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    # ═══════════════════════════════════════════════════════════════════
    # WEEKLY REVIEWS
    # ═══════════════════════════════════════════════════════════════════

    def log_weekly_review(
        self,
        inbox_cleared: bool = False,
        projects_reviewed: bool = False,
        tasks_completed: int = 0,
        tasks_added: int = 0,
        tasks_dropped: int = 0,
        notes: str = "",
    ) -> int:
        """Record a weekly review. Returns review ID."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO coach_weekly_reviews
                        (review_date, inbox_cleared, projects_reviewed,
                         tasks_completed, tasks_added, tasks_dropped, notes)
                    VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (inbox_cleared, projects_reviewed,
                      tasks_completed, tasks_added, tasks_dropped, notes))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_last_weekly_review(self) -> Optional[Dict[str, Any]]:
        """Get the most recent weekly review."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM coach_weekly_reviews
                    ORDER BY review_date DESC LIMIT 1
                """)
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    # ═══════════════════════════════════════════════════════════════════
    # CONFIG (from coach_config table)
    # ═══════════════════════════════════════════════════════════════════

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a config value by key."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM coach_config WHERE key = %s", (key,)
                )
                row = cur.fetchone()
                if row:
                    return json.loads(row[0]) if isinstance(row[0], str) else row[0]
                return default
        finally:
            self._put(conn)

    def set_config(self, key: str, value: Any, description: str = "") -> None:
        """Set a config value."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO coach_config (key, value, description, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                """, (key, json.dumps(value), description))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ═══════════════════════════════════════════════════════════════════
    # STATS — For analyzer and briefing use
    # ═══════════════════════════════════════════════════════════════════

    def count_tasks_by_status(self) -> Dict[str, int]:
        """Count tasks grouped by status."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT status, COUNT(*) FROM coach_tasks
                    GROUP BY status ORDER BY status
                """)
                return {r[0]: r[1] for r in cur.fetchall()}
        finally:
            self._put(conn)

    def tasks_completed_since(self, since: datetime) -> int:
        """Count tasks completed after a given datetime."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM coach_tasks
                    WHERE status = 'done' AND completed_at >= %s
                """, (since,))
                return cur.fetchone()[0]
        finally:
            self._put(conn)

    def tasks_completed_today(self) -> int:
        """Count tasks completed today."""
        return self.tasks_completed_since(
            datetime.combine(date.today(), datetime.min.time())
        )

    def get_project_task_counts(self) -> List[Dict[str, Any]]:
        """Get task counts per project (for project status overview)."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        p.id, p.name, p.status AS project_status, p.priority,
                        COUNT(t.id) FILTER (WHERE t.status = 'next') AS next_count,
                        COUNT(t.id) FILTER (WHERE t.status = 'inbox') AS inbox_count,
                        COUNT(t.id) FILTER (WHERE t.status = 'waiting') AS waiting_count,
                        COUNT(t.id) FILTER (WHERE t.status = 'done') AS done_count,
                        COUNT(t.id) AS total_count
                    FROM coach_projects p
                    LEFT JOIN coach_tasks t ON t.project_id = p.id
                    WHERE p.status = 'active'
                    GROUP BY p.id
                    ORDER BY p.priority ASC, p.name
                """)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)
