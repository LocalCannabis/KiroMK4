#!/usr/bin/env python3
"""
ambient/ingest/ingest_grow.py — Grow log ingestion worker.

Watches Jack's grow_log_entries table for new rows and writes them
as kiro_events with source='grow_log'.

Event-driven: polls frequently but only ingests genuinely new entries
by tracking the last seen ID.

Usage:
    python -m ambient.ingest.ingest_grow
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.ingest.grow")


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types from PostgreSQL."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class GrowIngestionWorker(BaseWorker):
    """
    Watches grow_log_entries for new checkins and ingests them
    as kiro_events for ambient processing.

    Unlike other workers, this is semi-event-driven — polls every 60s
    but only creates events for genuinely new rows.
    """

    worker_name = "ingest_grow"
    default_interval_seconds = 60  # Check every minute

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._jack_pool = None
        self._last_seen_id: int = 0

    def setup(self) -> None:
        """Initialize connection to Jack's database."""
        from jack.config import load_jack_config
        cfg = load_jack_config()
        db_cfg = cfg.get("database", {})

        from psycopg2.pool import ThreadedConnectionPool
        self._jack_pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=2,
            host=db_cfg.get("host", "localhost"),
            port=int(db_cfg.get("port", 5432)),
            dbname=db_cfg.get("dbname", "kiro"),
            user=db_cfg.get("user", "kiro"),
            password=db_cfg.get("password", ""),
        )

        # Determine the last ingested grow log entry
        self._last_seen_id = self._get_last_ingested_id()
        self.audit_log("INFO", f"Grow ingestion initialized (last_seen_id={self._last_seen_id})")

    def _get_last_ingested_id(self) -> int:
        """Find the highest grow_log entry ID we've already ingested."""
        conn = self.db._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT MAX(CAST(REPLACE(source_id, 'grow_', '') AS INTEGER))
                    FROM kiro_events
                    WHERE source = 'grow_log'
                """)
                row = cur.fetchone()
                return row[0] or 0 if row else 0
        finally:
            self.db._put(conn)

    def process(self) -> None:
        """Check for new grow_log_entries since last_seen_id."""
        conn = self._jack_pool.getconn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT gle.*, g.strain, g.current_stage, g.start_date, g.grow_type
                    FROM grow_log_entries gle
                    JOIN grows g ON gle.grow_id = g.id
                    WHERE gle.id > %s
                    ORDER BY gle.id ASC
                """, (self._last_seen_id,))
                new_entries = cur.fetchall()
        finally:
            self._jack_pool.putconn(conn)

        if not new_entries:
            logger.debug("No new grow log entries")
            return

        ingested = 0
        for entry in new_entries:
            entry_dict = dict(entry)
            entry_id = entry_dict["id"]

            # Build metadata from key environmental readings
            metadata = {
                "grow_id": entry_dict.get("grow_id"),
                "strain": entry_dict.get("strain"),
                "stage": entry_dict.get("current_stage"),
                "day_number": entry_dict.get("day_number"),
                "grow_type": entry_dict.get("grow_type"),
                # Environmental readings
                "humidity_tent": float(entry_dict["humidity_tent"]) if entry_dict.get("humidity_tent") else None,
                "humidity_ambient": float(entry_dict["humidity_ambient"]) if entry_dict.get("humidity_ambient") else None,
                "temp_canopy_c": float(entry_dict["temp_canopy_c"]) if entry_dict.get("temp_canopy_c") else None,
                "temp_pot_c": float(entry_dict["temp_pot_c"]) if entry_dict.get("temp_pot_c") else None,
                "temp_ambient_c": float(entry_dict["temp_ambient_c"]) if entry_dict.get("temp_ambient_c") else None,
                "vpd_kpa": float(entry_dict["vpd_kpa"]) if entry_dict.get("vpd_kpa") else None,
                "dli_estimate": float(entry_dict["dli_estimate"]) if entry_dict.get("dli_estimate") else None,
                "soil_moisture": entry_dict.get("soil_moisture"),
                "water_ph": float(entry_dict["water_ph"]) if entry_dict.get("water_ph") else None,
                "light_schedule": entry_dict.get("light_schedule"),
                "light_distance_cm": entry_dict.get("light_distance_cm"),
                # Assessment
                "jack_confidence": entry_dict.get("jack_confidence"),
                "flags": entry_dict.get("flags") or [],
                # Extended readings
                "runoff_ec": float(entry_dict["runoff_ec"]) if entry_dict.get("runoff_ec") else None,
                "runoff_ph": float(entry_dict["runoff_ph"]) if entry_dict.get("runoff_ph") else None,
                "brix": float(entry_dict["brix"]) if entry_dict.get("brix") else None,
                "soil_temp_c": float(entry_dict["soil_temp_c"]) if entry_dict.get("soil_temp_c") else None,
            }

            # Raw content includes the full assessment and observations
            raw_parts = []
            if entry_dict.get("plant_observations"):
                raw_parts.append(f"Observations: {entry_dict['plant_observations']}")
            if entry_dict.get("jack_assessment"):
                raw_parts.append(f"Assessment: {entry_dict['jack_assessment']}")
            if entry_dict.get("actions_recommended"):
                raw_parts.append(f"Actions: {entry_dict['actions_recommended']}")
            if entry_dict.get("feed_details"):
                raw_parts.append(f"Feed: {entry_dict['feed_details']}")
            if entry_dict.get("pest_notes"):
                raw_parts.append(f"Pests: {entry_dict['pest_notes']}")
            if entry_dict.get("biology_observations"):
                raw_parts.append(f"Biology: {entry_dict['biology_observations']}")
            if entry_dict.get("weather_notes"):
                raw_parts.append(f"Weather: {entry_dict['weather_notes']}")

            raw_content = "\n".join(raw_parts) if raw_parts else None

            logged_at = entry_dict.get("logged_at", datetime.utcnow())

            event_id = self.db.insert_event(
                source="grow_log",
                source_id=f"grow_{entry_id}",
                event_type="checkin",
                occurred_at=logged_at,
                metadata=metadata,
                raw_content=raw_content,
            )

            if event_id:
                ingested += 1
            self._last_seen_id = max(self._last_seen_id, entry_id)

        if ingested > 0:
            self.audit_log("INFO", f"Ingested {ingested} new grow log entries", {
                "count": ingested,
                "last_id": self._last_seen_id,
            })

    def cleanup(self) -> None:
        """Close Jack's DB pool."""
        if self._jack_pool:
            self._jack_pool.closeall()


def main():
    worker = GrowIngestionWorker()
    worker.run()


if __name__ == "__main__":
    main()
