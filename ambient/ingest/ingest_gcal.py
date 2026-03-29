#!/usr/bin/env python3
"""
ambient/ingest/ingest_gcal.py — Google Calendar ingestion worker.

Polls the Google Calendar API every 15 minutes and writes events
as kiro_events with source='gcal'.

Uses the existing Google Auth infrastructure from tools/google_auth.py.

Usage:
    python -m ambient.ingest.ingest_gcal
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.ingest.gcal")


class GCalIngestionWorker(BaseWorker):
    """
    Polls Google Calendar for upcoming and recently modified events.

    Looks ahead 7 days and back 1 day to catch modifications.
    Each calendar event becomes a kiro_event.
    """

    worker_name = "ingest_gcal"
    default_interval_seconds = 900  # 15 minutes

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._service = None

    def setup(self) -> None:
        """Initialize Google Calendar API service."""
        from tools.google_auth import get_google_service
        self._service = get_google_service("calendar", "v3")

        # Load polling interval from ambient config
        polling = self.db.get_config("stream_polling", {})
        if isinstance(polling, dict) and "gcal" in polling:
            self._interval = int(polling["gcal"])

        self.audit_log("INFO", "Google Calendar ingestion initialized")

    def process(self) -> None:
        """Fetch calendar events for the upcoming window."""
        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(days=1)).isoformat()
        time_max = (now + timedelta(days=7)).isoformat()

        try:
            events_result = self._service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=100,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
        except Exception as e:
            self.audit_log("ERROR", f"Google Calendar API error: {e}")
            return

        events = events_result.get("items", [])
        ingested = 0

        for event in events:
            event_id = event.get("id", "")
            updated = event.get("updated", "")

            # Use event ID + updated timestamp for dedup (catches modifications)
            source_id = f"gcal_{event_id}_{updated[:19]}" if updated else f"gcal_{event_id}"

            # Parse start/end times
            start = event.get("start", {})
            end = event.get("end", {})
            start_dt = start.get("dateTime") or start.get("date", "")
            end_dt = end.get("dateTime") or end.get("date", "")

            # Parse occurred_at from start time
            try:
                if "T" in start_dt:
                    occurred_at = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                else:
                    occurred_at = datetime.strptime(start_dt, "%Y-%m-%d")
            except (ValueError, TypeError):
                occurred_at = now

            attendees = event.get("attendees", [])
            metadata = {
                "calendar_event_id": event_id,
                "summary": event.get("summary", ""),
                "description": event.get("description", ""),
                "location": event.get("location", ""),
                "start": start_dt,
                "end": end_dt,
                "all_day": "date" in start and "dateTime" not in start,
                "status": event.get("status", ""),
                "creator": event.get("creator", {}).get("email", ""),
                "organizer": event.get("organizer", {}).get("email", ""),
                "attendees": [
                    {
                        "email": a.get("email", ""),
                        "name": a.get("displayName", ""),
                        "response": a.get("responseStatus", ""),
                    }
                    for a in attendees
                ],
                "recurring_event_id": event.get("recurringEventId"),
                "html_link": event.get("htmlLink", ""),
            }

            raw_content = f"{event.get('summary', '')} — {event.get('description', '')}"

            eid = self.db.insert_event(
                source="gcal",
                source_id=source_id,
                event_type="calendar_event",
                occurred_at=occurred_at,
                metadata=metadata,
                raw_content=raw_content.strip() or None,
            )
            if eid:
                ingested += 1

        if ingested > 0:
            self.audit_log("INFO", f"Ingested {ingested} calendar events", {
                "count": ingested,
                "total_fetched": len(events),
            })
        else:
            logger.debug("No new calendar events (fetched %d)", len(events))


def main():
    worker = GCalIngestionWorker()
    worker.run()


if __name__ == "__main__":
    main()
