"""
jack/db.py — PostgreSQL database access layer for Jack grow state.

All Tier 1 (live grow state) operations. Uses psycopg2 with connection
pooling for the Flask API and checkin engine.

Every reading Tim provides gets logged with a timestamp. No data is ever
silently discarded.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("jack.db")


class JackDB:
    """PostgreSQL access for Jack's grow state data."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
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
            "Jack DB pool created: %s@%s:%s/%s",
            db_cfg.get("user", "kiro"),
            db_cfg.get("host", "localhost"),
            db_cfg.get("port", 5432),
            db_cfg.get("dbname", "kiro"),
        )

    def _conn(self):
        return self._pool.getconn()

    def _put(self, conn):
        self._pool.putconn(conn)

    def close(self):
        self._pool.closeall()

    # =========================================================================
    # Grows
    # =========================================================================
    def create_grow(self, **kwargs) -> Dict[str, Any]:
        """Create a new grow record. Returns the created row as a dict."""
        fields = [
            "strain", "genetics", "source", "medium", "pot_size", "pot_type",
            "plant_count", "start_date", "seed_or_clone", "current_stage",
            "stage_changed", "light_schedule", "target_harvest", "notes",
            "grow_type", "location", "fertility_model",
        ]
        present = {k: v for k, v in kwargs.items() if k in fields and v is not None}
        if "strain" not in present or "start_date" not in present:
            raise ValueError("strain and start_date are required")

        cols = ", ".join(present.keys())
        placeholders = ", ".join(["%s"] * len(present))
        values = list(present.values())

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"INSERT INTO grows ({cols}) VALUES ({placeholders}) RETURNING *",
                    values,
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        finally:
            self._put(conn)

    def get_active_grow(self) -> Optional[Dict[str, Any]]:
        """Get the most recent non-complete grow.
        
        If multiple active grows exist (indoor + outdoor), returns the most
        recently created one. Use get_active_grow_by_type() for explicit control,
        or get_active_grows() to see all.
        """
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM grows WHERE current_stage != 'complete' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            self._put(conn)

    def get_active_grows(self) -> List[Dict[str, Any]]:
        """Get all non-complete grows, newest first."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM grows WHERE current_stage != 'complete' "
                    "ORDER BY created_at DESC"
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_active_grow_by_type(self, grow_type: str) -> Optional[Dict[str, Any]]:
        """Get the most recent non-complete grow of a given type ('indoor' or 'outdoor')."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM grows WHERE current_stage != 'complete' AND grow_type = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (grow_type,),
                )
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            self._put(conn)

    def get_grow(self, grow_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM grows WHERE id = %s", (grow_id,))
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            self._put(conn)

    def update_grow(self, grow_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        """Update a grow record. Returns the updated row."""
        allowed = {
            "strain", "genetics", "source", "medium", "pot_size", "pot_type",
            "plant_count", "current_stage", "stage_changed", "light_schedule",
            "target_harvest", "notes",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_grow(grow_id)

        updates["updated_at"] = datetime.now()
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [grow_id]

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"UPDATE grows SET {set_clause} WHERE id = %s RETURNING *",
                    values,
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
        finally:
            self._put(conn)

    def update_grow_stage(self, grow_id: int, new_stage: str) -> Optional[Dict[str, Any]]:
        """Advance a grow to a new stage with automatic stage_changed date."""
        valid_stages = {
            "seedling", "veg", "transition", "flower", "flush", "dry", "cure", "complete"
        }
        if new_stage not in valid_stages:
            raise ValueError(f"Invalid stage: {new_stage}. Must be one of: {valid_stages}")
        return self.update_grow(grow_id, current_stage=new_stage, stage_changed=date.today())

    def list_grows(self, limit: int = 10) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM grows ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    # =========================================================================
    # Tent Config
    # =========================================================================
    def get_tent_config(self, grow_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get tent config. If grow_id given, prefers that grow's config, falls back to unassociated."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if grow_id is not None:
                    # Try grow-specific first
                    cur.execute(
                        "SELECT * FROM tent_config WHERE grow_id = %s ORDER BY id DESC LIMIT 1",
                        (grow_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return dict(row)
                # Fall back to unassociated / most recent
                cur.execute("SELECT * FROM tent_config ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            self._put(conn)

    def upsert_tent_config(self, grow_id: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        """Create or update tent configuration. Associates with grow_id if provided."""
        existing = self.get_tent_config(grow_id=grow_id) if grow_id else self.get_tent_config()
        conn = self._conn()
        try:
            if existing:
                # Update
                allowed = {
                    "tent_size", "tent_height", "light_model", "light_wattage",
                    "light_spectrum", "fan_model", "filter_model", "humidifier",
                    "dehumidifier", "medium_details", "other_equipment", "grow_id",
                }
                if grow_id is not None and "grow_id" not in kwargs:
                    kwargs["grow_id"] = grow_id
                updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
                updates["updated_at"] = datetime.now()
                set_clause = ", ".join(f"{k} = %s" for k in updates)
                values = list(updates.values()) + [existing["id"]]
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"UPDATE tent_config SET {set_clause} WHERE id = %s RETURNING *",
                        values,
                    )
                    row = cur.fetchone()
            else:
                # Insert
                fields = {k: v for k, v in kwargs.items() if v is not None}
                if grow_id is not None:
                    fields["grow_id"] = grow_id
                cols = ", ".join(fields.keys())
                placeholders = ", ".join(["%s"] * len(fields))
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"INSERT INTO tent_config ({cols}) VALUES ({placeholders}) RETURNING *",
                        list(fields.values()),
                    )
                    row = cur.fetchone()
            conn.commit()
            return dict(row)
        finally:
            self._put(conn)

    # =========================================================================
    # Grow Log Entries
    # =========================================================================
    def log_entry(self, grow_id: int, **kwargs) -> Dict[str, Any]:
        """
        Create a checkin log entry. Computes day_number automatically from
        the grow's start_date.

        All provided fields are logged; missing fields are stored as NULL.
        Checkin data is sacred — nothing is silently discarded.
        """
        grow = self.get_grow(grow_id)
        if not grow:
            raise ValueError(f"Grow {grow_id} not found")

        # Compute day number from start date
        start = grow["start_date"]
        if isinstance(start, str):
            start = date.fromisoformat(start)
        day_number = (date.today() - start).days + 1

        fields = {
            "grow_id": grow_id,
            "day_number": day_number,
            "logged_at": datetime.now(),
        }

        log_fields = [
            "humidity_tent", "humidity_ambient", "temp_canopy_c", "temp_pot_c",
            "temp_ambient_c", "light_distance_cm", "light_schedule",
            "vpd_kpa", "dli_estimate", "soil_moisture", "last_watered",
            "last_feed", "feed_details", "water_ph", "water_volume_ml",
            "plant_observations", "jack_assessment", "jack_confidence",
            "flags", "actions_recommended", "photo_paths",
            # Multi-grow fields (002_multi_grow migration)
            "runoff_ec", "runoff_ph", "brix", "soil_temp_c",
            "pest_notes", "biology_observations", "weather_notes",
        ]
        for f in log_fields:
            if f in kwargs and kwargs[f] is not None:
                fields[f] = kwargs[f]

        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["%s"] * len(fields))
        values = list(fields.values())

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"INSERT INTO grow_log_entries ({cols}) VALUES ({placeholders}) RETURNING *",
                    values,
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        finally:
            self._put(conn)

    def get_recent_logs(self, grow_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """Get the most recent N log entries for a grow, newest first."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM grow_log_entries WHERE grow_id = %s "
                    "ORDER BY logged_at DESC LIMIT %s",
                    (grow_id, limit),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def get_latest_log(self, grow_id: int) -> Optional[Dict[str, Any]]:
        """Get the single most recent log entry."""
        logs = self.get_recent_logs(grow_id, limit=1)
        return logs[0] if logs else None

    def get_logs_since(self, grow_id: int, since: datetime) -> List[Dict[str, Any]]:
        """Get all log entries since a given timestamp."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM grow_log_entries WHERE grow_id = %s AND logged_at >= %s "
                    "ORDER BY logged_at ASC",
                    (grow_id, since),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    # =========================================================================
    # Feeding Schedule
    # =========================================================================
    def get_feeding_schedule(self, grow_id: int, stage: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if stage:
                    cur.execute(
                        "SELECT * FROM feeding_schedule WHERE grow_id = %s AND stage = %s "
                        "ORDER BY created_at",
                        (grow_id, stage),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM feeding_schedule WHERE grow_id = %s ORDER BY created_at",
                        (grow_id,),
                    )
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def add_feeding_schedule(self, grow_id: int, stage: str, **kwargs) -> Dict[str, Any]:
        fields = {"grow_id": grow_id, "stage": stage}
        for k in ["interval_days", "method", "recipe", "notes"]:
            if k in kwargs and kwargs[k] is not None:
                fields[k] = kwargs[k]

        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["%s"] * len(fields))

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"INSERT INTO feeding_schedule ({cols}) VALUES ({placeholders}) RETURNING *",
                    list(fields.values()),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        finally:
            self._put(conn)

    # =========================================================================
    # Strain Profiles
    # =========================================================================
    def get_strain_profile(self, strain_name: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM strain_profiles WHERE LOWER(strain_name) = LOWER(%s) LIMIT 1",
                    (strain_name,),
                )
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            self._put(conn)

    def upsert_strain_profile(self, strain_name: str, **kwargs) -> Dict[str, Any]:
        existing = self.get_strain_profile(strain_name)
        conn = self._conn()
        try:
            if existing:
                allowed = {
                    "breeder", "genetics", "typical_flower_days", "stretch_factor",
                    "known_sensitivities", "ideal_environment", "terpene_profile",
                    "grow_tips", "source_references", "confidence",
                }
                updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
                updates["updated_at"] = datetime.now()
                set_clause = ", ".join(f"{k} = %s" for k in updates)
                values = list(updates.values()) + [existing["id"]]
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"UPDATE strain_profiles SET {set_clause} WHERE id = %s RETURNING *",
                        values,
                    )
                    row = cur.fetchone()
            else:
                fields = {"strain_name": strain_name}
                fields.update({k: v for k, v in kwargs.items() if v is not None})
                cols = ", ".join(fields.keys())
                placeholders = ", ".join(["%s"] * len(fields))
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"INSERT INTO strain_profiles ({cols}) VALUES ({placeholders}) RETURNING *",
                        list(fields.values()),
                    )
                    row = cur.fetchone()
            conn.commit()
            return dict(row)
        finally:
            self._put(conn)

    # =========================================================================
    # Utility: stale data detection
    # =========================================================================
    def get_stale_readings(
        self,
        grow_id: int,
        thresholds: Dict[str, int],
    ) -> Dict[str, Optional[datetime]]:
        """
        Check which readings are stale based on configured hour thresholds.
        Returns a dict mapping field name → last reading timestamp (or None if never recorded).
        Only includes fields that ARE stale (older than threshold).
        """
        latest = self.get_latest_log(grow_id)
        if not latest:
            return {
                "humidity": None,
                "temperature": None,
                "light_distance": None,
                "soil_moisture": None,
                "watering": None,
            }

        now = datetime.now()
        stale = {}

        field_map = {
            "humidity": "humidity_tent",
            "temperature": "temp_canopy_c",
            "light_distance": "light_distance_cm",
            "soil_moisture": "soil_moisture",
            "watering": "last_watered",
        }

        # Check recent logs for the most recent non-null value of each field
        logs = self.get_recent_logs(grow_id, limit=10)
        for field_key, col_name in field_map.items():
            threshold_hours = thresholds.get(field_key, 24)
            last_value_time = None

            for log in logs:
                if log.get(col_name) is not None:
                    last_value_time = log["logged_at"]
                    break

            if last_value_time is None:
                stale[field_key] = None
            elif (now - last_value_time).total_seconds() > threshold_hours * 3600:
                stale[field_key] = last_value_time

        return stale
