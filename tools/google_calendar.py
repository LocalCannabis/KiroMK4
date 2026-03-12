"""
tools/google_calendar.py — Google Calendar tools for Kiro.

Voice actions:
  - create_calendar_event: "Schedule a meeting with Dave tomorrow at 3pm"
  - list_calendar_events: "What's on my calendar today?" / "What do I have this week?"
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .google_auth import get_google_service

logger = logging.getLogger("kiro")


def _service():
    return get_google_service("calendar", "v3")


def _parse_time(dt_str: str) -> str:
    """Best-effort ISO format — GPT sends ISO-ish strings already."""
    # If it's already good ISO, return as-is
    if "T" in dt_str:
        return dt_str
    # If just a date like "2026-03-12", add a time
    return dt_str + "T09:00:00"


def create_calendar_event(
    summary: str,
    start_time: str,
    end_time: str = "",
    description: str = "",
    location: str = "",
) -> str:
    """Create a Google Calendar event. Returns confirmation string."""
    try:
        svc = _service()

        start_iso = _parse_time(start_time)
        if not end_time:
            # Default 1 hour duration
            from dateutil.parser import parse as dt_parse
            try:
                start_dt = dt_parse(start_iso)
                end_dt = start_dt + timedelta(hours=1)
                end_iso = end_dt.isoformat()
            except Exception:
                end_iso = start_iso  # fallback
        else:
            end_iso = _parse_time(end_time)

        event_body: Dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start_iso, "timeZone": "America/Vancouver"},
            "end": {"dateTime": end_iso, "timeZone": "America/Vancouver"},
        }
        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location

        event = svc.events().insert(calendarId="primary", body=event_body).execute()
        return f"Event created: {summary} on {start_iso[:10]} at {start_iso[11:16]}."
    except Exception as e:
        logger.error("Calendar create failed: %s", e)
        return f"Sorry, I couldn't create that event: {e}"


def list_calendar_events(
    time_range: str = "today",
    max_results: int = 10,
) -> str:
    """List upcoming calendar events. time_range: 'today', 'tomorrow', 'week', or ISO date."""
    try:
        svc = _service()
        now = datetime.now()

        if time_range == "today":
            time_min = now.replace(hour=0, minute=0, second=0).isoformat() + "-08:00"
            time_max = now.replace(hour=23, minute=59, second=59).isoformat() + "-08:00"
            label = "today"
        elif time_range == "tomorrow":
            tmrw = now + timedelta(days=1)
            time_min = tmrw.replace(hour=0, minute=0, second=0).isoformat() + "-08:00"
            time_max = tmrw.replace(hour=23, minute=59, second=59).isoformat() + "-08:00"
            label = "tomorrow"
        elif time_range == "week":
            time_min = now.isoformat() + "-08:00"
            end = now + timedelta(days=7)
            time_max = end.replace(hour=23, minute=59, second=59).isoformat() + "-08:00"
            label = "this week"
        else:
            # Assume ISO date
            time_min = time_range + "T00:00:00-08:00"
            time_max = time_range + "T23:59:59-08:00"
            label = time_range

        result = svc.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = result.get("items", [])
        if not events:
            return f"You have nothing scheduled {label}."

        lines = []
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date", ""))
            summary = ev.get("summary", "Untitled")
            if "T" in start:
                time_part = start[11:16]
                lines.append(f"{time_part} — {summary}")
            else:
                lines.append(f"All day — {summary}")

        header = f"You have {len(events)} event{'s' if len(events) != 1 else ''} {label}: "
        return header + ". ".join(lines) + "."
    except Exception as e:
        logger.error("Calendar list failed: %s", e)
        return f"Sorry, I couldn't read your calendar: {e}"
