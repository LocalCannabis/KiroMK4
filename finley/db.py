"""
finley/db.py — PostgreSQL database access layer for Finley financial data.

All YNAB data is cached in the shared kiro PostgreSQL database so Finley
can answer questions without hitting the API. Financial profile, wellbeing
scores, engagement logs, and knowledge entries also live here.

Uses psycopg2 + ThreadedConnectionPool (same pattern as Jack and Ambient).
Schema is managed by finley/migrate.py — never created inline.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("kiro.finley.db")

# ---------------------------------------------------------------------------
# Milliunit helpers
# ---------------------------------------------------------------------------

def milliunits_to_dollars(milliunits: int) -> float:
    """Convert YNAB milliunits to dollar amount."""
    return milliunits / 1000.0


def format_currency(milliunits: int) -> str:
    """Format milliunits as a human-readable currency string."""
    dollars = abs(milliunits) / 1000.0
    sign = "-" if milliunits < 0 else ""
    return f"{sign}${dollars:,.2f}"


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class FinleyDB:
    """PostgreSQL access for Finley's YNAB cache + financial profiling data."""

    def __init__(self, cfg: Dict[str, Any] | None = None):
        if cfg is None:
            from .config import load_db_config
            cfg = load_db_config()
        # cfg may be the Finley secrets dict (no 'database' key) or the full
        # DB config dict (has 'database' key).  Always load DB creds from
        # jack_config.yaml when 'database' is missing.
        if "database" not in cfg:
            from .config import load_db_config
            db_cfg = load_db_config().get("database", {})
        else:
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
            "Finley DB pool created: %s@%s:%s/%s",
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

    # ------------------------------------------------------------------
    # Upsert methods (used by sync)
    # ------------------------------------------------------------------

    def upsert_accounts(self, accounts: list[dict]) -> int:
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for a in accounts:
                    cur.execute("""
                        INSERT INTO finley_accounts
                            (id, name, type, on_budget, closed, balance,
                             cleared_balance, uncleared_balance, last_updated)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            type = EXCLUDED.type,
                            on_budget = EXCLUDED.on_budget,
                            closed = EXCLUDED.closed,
                            balance = EXCLUDED.balance,
                            cleared_balance = EXCLUDED.cleared_balance,
                            uncleared_balance = EXCLUDED.uncleared_balance,
                            last_updated = EXCLUDED.last_updated
                    """, (a["id"], a["name"], a.get("type", ""),
                          a.get("on_budget", False),
                          a.get("closed", False),
                          a.get("balance", 0),
                          a.get("cleared_balance", 0),
                          a.get("uncleared_balance", 0),
                          now))
            conn.commit()
            return len(accounts)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def upsert_categories(self, categories: list[dict]) -> int:
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for cat in categories:
                    cur.execute("""
                        INSERT INTO finley_categories
                            (id, group_id, group_name, name, budgeted, activity,
                             balance, goal_type, goal_target, goal_target_date, last_updated)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            group_id = EXCLUDED.group_id,
                            group_name = EXCLUDED.group_name,
                            name = EXCLUDED.name,
                            budgeted = EXCLUDED.budgeted,
                            activity = EXCLUDED.activity,
                            balance = EXCLUDED.balance,
                            goal_type = EXCLUDED.goal_type,
                            goal_target = EXCLUDED.goal_target,
                            goal_target_date = EXCLUDED.goal_target_date,
                            last_updated = EXCLUDED.last_updated
                    """, (cat["id"], cat.get("group_id", ""), cat.get("group_name", ""),
                          cat["name"],
                          cat.get("budgeted", 0), cat.get("activity", 0),
                          cat.get("balance", 0),
                          cat.get("goal_type"), cat.get("goal_target"),
                          cat.get("goal_target_date"), now))
            conn.commit()
            return len(categories)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def upsert_transactions(self, transactions: list[dict]) -> int:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for t in transactions:
                    subs = t.get("subtransactions")
                    if subs and not isinstance(subs, str):
                        subs = json.dumps(subs)
                    cur.execute("""
                        INSERT INTO finley_transactions
                            (id, date, amount, memo, payee_id, payee_name,
                             category_id, category_name, account_id, account_name,
                             approved, cleared, flag_color, transfer_account_id,
                             subtransactions)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            date = EXCLUDED.date,
                            amount = EXCLUDED.amount,
                            memo = EXCLUDED.memo,
                            payee_id = EXCLUDED.payee_id,
                            payee_name = EXCLUDED.payee_name,
                            category_id = EXCLUDED.category_id,
                            category_name = EXCLUDED.category_name,
                            account_id = EXCLUDED.account_id,
                            account_name = EXCLUDED.account_name,
                            approved = EXCLUDED.approved,
                            cleared = EXCLUDED.cleared,
                            flag_color = EXCLUDED.flag_color,
                            transfer_account_id = EXCLUDED.transfer_account_id,
                            subtransactions = EXCLUDED.subtransactions
                    """, (t["id"], t["date"], t["amount"],
                          t.get("memo"), t.get("payee_id"), t.get("payee_name"),
                          t.get("category_id"), t.get("category_name"),
                          t.get("account_id"), t.get("account_name"),
                          t.get("approved", False),
                          t.get("cleared", "uncleared"),
                          t.get("flag_color"),
                          t.get("transfer_account_id"),
                          subs))
            conn.commit()
            return len(transactions)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def upsert_scheduled_transactions(self, scheduled: list[dict]) -> int:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for s in scheduled:
                    cur.execute("""
                        INSERT INTO finley_scheduled_transactions
                            (id, date_first, date_next, frequency, amount,
                             payee_name, category_name, memo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            date_first = EXCLUDED.date_first,
                            date_next = EXCLUDED.date_next,
                            frequency = EXCLUDED.frequency,
                            amount = EXCLUDED.amount,
                            payee_name = EXCLUDED.payee_name,
                            category_name = EXCLUDED.category_name,
                            memo = EXCLUDED.memo
                    """, (s["id"], s.get("date_first"), s.get("date_next"),
                          s.get("frequency"), s.get("amount", 0),
                          s.get("payee_name"), s.get("category_name"),
                          s.get("memo")))
            conn.commit()
            return len(scheduled)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def log_sync(self, endpoint: str, server_knowledge: int, records_updated: int):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO finley_sync_log (endpoint, server_knowledge, records_updated) VALUES (%s, %s, %s)",
                    (endpoint, server_knowledge, records_updated),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ------------------------------------------------------------------
    # Insight queue
    # ------------------------------------------------------------------

    def queue_insight(self, message: str, severity: str = "info"):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO finley_insights_queue (message, severity) VALUES (%s, %s)",
                    (message, severity),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_pending_insights(self, limit: int = 5) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, message, severity, created_at FROM finley_insights_queue "
                    "WHERE delivered = FALSE ORDER BY id ASC LIMIT %s",
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def mark_insights_delivered(self, insight_ids: list[int]):
        if not insight_ids:
            return
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE finley_insights_queue SET delivered = TRUE WHERE id = ANY(%s)",
                    (insight_ids,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ------------------------------------------------------------------
    # Read helpers (used by analyzer)
    # ------------------------------------------------------------------

    def get_all_category_names(self) -> list[str]:
        """Return all known category names: both YNAB-defined and classifier-assigned.

        This union ensures that resolve_category() can match classifier labels
        like 'Food', 'Transportation', 'Cannabis', etc. even when YNAB itself
        marks every transaction as 'Uncategorized'.
        """
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT name FROM finley_categories
                    UNION
                    SELECT DISTINCT classified_category
                    FROM finley_transactions
                    WHERE classified_category IS NOT NULL
                      AND classified_category != 'System'
                    ORDER BY name
                """)
                return [r[0] for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_accounts(self, on_budget_only: bool = False) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                sql = "SELECT * FROM finley_accounts WHERE closed = FALSE"
                if on_budget_only:
                    sql += " AND on_budget = TRUE"
                sql += " ORDER BY name"
                cur.execute(sql)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_categories(self) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM finley_categories ORDER BY group_name, name")
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_transactions(
        self,
        since_date: str | None = None,
        until_date: str | None = None,
        category_name: str | None = None,
        payee_name: str | None = None,
        account_name: str | None = None,
        min_amount: int | None = None,
        max_amount: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Flexible transaction query with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []

        if since_date:
            conditions.append("date >= %s")
            params.append(since_date)
        if until_date:
            conditions.append("date <= %s")
            params.append(until_date)
        if category_name:
            # Match against YNAB category_name OR the classifier's result,
            # so queries like 'Food' work even when YNAB shows 'Uncategorized'.
            conditions.append(
                "(category_name = %s OR classified_category = %s)"
            )
            params.extend([category_name, category_name])
        if payee_name:
            conditions.append("payee_name = %s")
            params.append(payee_name)
        if account_name:
            conditions.append("LOWER(account_name) LIKE %s")
            params.append(f"%{account_name.lower()}%")
        if min_amount is not None:
            conditions.append("amount >= %s")
            params.append(min_amount)
        if max_amount is not None:
            conditions.append("amount <= %s")
            params.append(max_amount)

        sql = "SELECT * FROM finley_transactions"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY date DESC"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_scheduled_transactions(self) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM finley_scheduled_transactions ORDER BY date_next")
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_last_sync_time(self) -> str | None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(synced_at) FROM finley_sync_log")
                row = cur.fetchone()
                return row[0].isoformat() if row and row[0] else None
        finally:
            self._put(conn)

    def spending_by_category(self, since_date: str, until_date: str | None = None) -> list[dict]:
        """Aggregate spending (outflows only) grouped by effective category.

        Uses classified_category (payee-rule-based) when YNAB's own
        category_name is NULL or 'Uncategorized', falling back to
        category_name otherwise.  This handles the common case where
        Tim hasn't yet categorised transactions in YNAB.
        """
        sql = """
            SELECT
                COALESCE(
                    classified_category,
                    NULLIF(category_name, 'Uncategorized')
                ) AS category_name,
                SUM(amount)  AS total,
                COUNT(*)     AS txn_count
            FROM finley_transactions
            WHERE amount < 0
              AND date >= %s
              AND COALESCE(payee_name, '') != 'Starting Balance'
              AND COALESCE(classified_category, '') != 'System'
        """
        params: list[Any] = [since_date]
        if until_date:
            sql += " AND date <= %s"
            params.append(until_date)
        sql += """ GROUP BY 1 ORDER BY total ASC"""  # most negative first
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def spending_by_payee(self, since_date: str, until_date: str | None = None) -> list[dict]:
        """Aggregate spending grouped by payee/merchant."""
        sql = """
            SELECT payee_name, SUM(amount) as total, COUNT(*) as txn_count
            FROM finley_transactions
            WHERE amount < 0
              AND date >= %s
              AND COALESCE(payee_name, '') != 'Starting Balance'
              AND COALESCE(classified_category, '') != 'System'
        """
        params: list[Any] = [since_date]
        if until_date:
            sql += " AND date <= %s"
            params.append(until_date)
        sql += " GROUP BY payee_name ORDER BY total ASC"
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def total_spending(self, since_date: str, until_date: str | None = None) -> int:
        """Total outflow (negative amounts) in a period. Returns milliunits (negative)."""
        sql = ("SELECT COALESCE(SUM(amount), 0) as total FROM finley_transactions "
               "WHERE amount < 0 AND date >= %s "
               "AND COALESCE(payee_name, '') != 'Starting Balance' "
               "AND COALESCE(classified_category, '') != 'System'")
        params: list[Any] = [since_date]
        if until_date:
            sql += " AND date <= %s"
            params.append(until_date)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()[0]
        finally:
            self._put(conn)

    def total_income(self, since_date: str, until_date: str | None = None) -> int:
        """Total inflow (positive amounts) in a period. Returns milliunits."""
        sql = ("SELECT COALESCE(SUM(amount), 0) as total FROM finley_transactions "
               "WHERE amount > 0 AND date >= %s")
        params: list[Any] = [since_date]
        if until_date:
            sql += " AND date <= %s"
            params.append(until_date)
        # Exclude transfers (they have a transfer_account_id)
        sql += " AND transfer_account_id IS NULL"
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()[0]
        finally:
            self._put(conn)

    # ------------------------------------------------------------------
    # Classifier columns (used by classifier.py)
    # ------------------------------------------------------------------

    def update_transaction_classification(
        self, txn_id: str,
        classified_category: str,
        classified_subcategory: str | None = None,
        classified_merchant_type: str | None = None,
    ):
        """Write classifier results back to the transaction row."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE finley_transactions
                    SET classified_category = %s,
                        classified_subcategory = %s,
                        classified_merchant_type = %s
                    WHERE id = %s
                """, (classified_category, classified_subcategory,
                      classified_merchant_type, txn_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_unclassified_transactions(self) -> list[dict]:
        """Return transactions that haven't been classified yet."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM finley_transactions WHERE classified_category IS NULL ORDER BY date DESC"
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    # ------------------------------------------------------------------
    # Financial Profile (used by profiler.py)
    # ------------------------------------------------------------------

    def upsert_profile(self, profile_date: str, vital_signs: dict,
                       behavioral: dict, stage: str,
                       account_snapshot: dict | None = None) -> int:
        """Insert or update a daily financial profile snapshot."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO finley_profile
                        (profile_date, vital_signs, behavioral, stage, account_snapshot)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (profile_date) DO UPDATE SET
                        vital_signs = EXCLUDED.vital_signs,
                        behavioral = EXCLUDED.behavioral,
                        stage = EXCLUDED.stage,
                        account_snapshot = EXCLUDED.account_snapshot,
                        created_at = NOW()
                    RETURNING id
                """, (profile_date,
                      json.dumps(vital_signs),
                      json.dumps(behavioral),
                      stage,
                      json.dumps(account_snapshot) if account_snapshot else None))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_latest_profile(self) -> dict | None:
        """Return the most recent financial profile."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM finley_profile ORDER BY profile_date DESC LIMIT 1"
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    def get_profile_history(self, limit: int = 30) -> list[dict]:
        """Return recent profile snapshots for trend analysis."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM finley_profile ORDER BY profile_date DESC LIMIT %s",
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    # ------------------------------------------------------------------
    # CFPB Wellbeing (used by cfpb.py)
    # ------------------------------------------------------------------

    def save_wellbeing(self, responses: dict, raw_score: int,
                       scaled_score: float, scale_version: str = "cfpb_5item_v1") -> int:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO finley_wellbeing
                        (responses, raw_score, scaled_score, scale_version)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (json.dumps(responses), raw_score, scaled_score, scale_version))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_latest_wellbeing(self) -> dict | None:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM finley_wellbeing ORDER BY assessed_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._put(conn)

    def get_wellbeing_history(self, limit: int = 12) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM finley_wellbeing ORDER BY assessed_at DESC LIMIT %s",
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    # ------------------------------------------------------------------
    # Engagements (used by engagement.py)
    # ------------------------------------------------------------------

    def log_engagement(self, trigger_type: str, trigger_detail: dict,
                       message_text: str, delivery_channel: str = "voice") -> int:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO finley_engagements
                        (trigger_type, trigger_detail, message_text, delivery_channel)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (trigger_type, json.dumps(trigger_detail),
                      message_text, delivery_channel))
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def get_recent_engagements(self, hours: int = 24) -> list[dict]:
        """Get engagements within the last N hours (for anti-nagging)."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM finley_engagements
                    WHERE delivered_at >= NOW() - INTERVAL '%s hours'
                    ORDER BY delivered_at DESC
                """, (hours,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def acknowledge_engagement(self, engagement_id: int, response: str | None = None):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE finley_engagements
                    SET acknowledged = TRUE, response_summary = %s
                    WHERE id = %s
                """, (response, engagement_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    # ------------------------------------------------------------------
    # Config-over-code (finley_config table)
    # ------------------------------------------------------------------

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read a single config value from finley_config table."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT config_value FROM finley_config WHERE config_key = %s", (key,)
                )
                row = cur.fetchone()
                if row is None:
                    return default
                val = row[0]
                # Try JSON parse for structured values
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    return val
        finally:
            self._put(conn)

    def set_config(self, key: str, value: Any, description: str | None = None):
        """Set a config value (upserts)."""
        val_str = json.dumps(value) if not isinstance(value, str) else value
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if description:
                    cur.execute("""
                        INSERT INTO finley_config (config_key, config_value, description)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (config_key) DO UPDATE SET
                            config_value = EXCLUDED.config_value,
                            description = EXCLUDED.description,
                            updated_at = NOW()
                    """, (key, val_str, description))
                else:
                    cur.execute("""
                        INSERT INTO finley_config (config_key, config_value)
                        VALUES (%s, %s)
                        ON CONFLICT (config_key) DO UPDATE SET
                            config_value = EXCLUDED.config_value,
                            updated_at = NOW()
                    """, (key, val_str))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)
