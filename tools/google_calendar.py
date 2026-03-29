"""
tools/google_calendar.py — Google Calendar tools for Kiro.

Voice actions:
  - create_calendar_event: "Schedule a meeting with Dave tomorrow at 3pm"
  - list_calendar_events: "What's on my calendar today?" / "What do I have this week?"
  - modify_calendar_event: "Move my dentist appointment to Friday at 2"
  - delete_calendar_event: "Cancel my meeting with Dave"
  - search_calendar_events: "When is my dentist appointment?"
  - check_calendar_availability: "Am I free Thursday afternoon?"
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .google_auth import get_google_service

logger = logging.getLogger("kiro")

_TZ = "America/Vancouver"


def _service():
    return get_google_service("calendar", "v3")


def _parse_time(dt_str: str) -> str:
    """Best-effort ISO format — GPT sends ISO-ish strings already."""
    if "T" in dt_str:
        return dt_str
    return dt_str + "T09:00:00"


def _is_date_only(dt_str: str) -> bool:
    """Check if a string is date-only (no time component)."""
    return "T" not in dt_str and len(dt_str) == 10


def _find_event(summary_query: str, future_only: bool = True, days_ahead: int = 60) -> Optional[Dict]:
    """Find the next upcoming event whose summary matches (case-insensitive substring).
    Returns the raw Google Calendar event dict, or None."""
    try:
        svc = _service()
        now = datetime.now()
        time_min = now.isoformat() + "-08:00" if future_only else (now - timedelta(days=30)).isoformat() + "-08:00"
        time_max = (now + timedelta(days=days_ahead)).isoformat() + "-08:00"

        result = svc.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
            q=summary_query,
        ).execute()
        events = result.get("items", [])
        if events:
            return events[0]
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------

def create_calendar_event(
    summary: str,
    start_time: str,
    end_time: str = "",
    description: str = "",
    location: str = "",
    all_day: bool = False,
) -> str:
    """Create a Google Calendar event. Supports timed and all-day events."""
    try:
        svc = _service()

        if all_day or _is_date_only(start_time):
            # All-day event — use 'date' key instead of 'dateTime'
            start_date = start_time[:10]
            if end_time:
                end_date = end_time[:10]
            else:
                # All-day events need end = start + 1 day
                from dateutil.parser import parse as dt_parse
                end_date = (dt_parse(start_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            event_body: Dict[str, Any] = {
                "summary": summary,
                "start": {"date": start_date},
                "end": {"date": end_date},
            }
        else:
            start_iso = _parse_time(start_time)
            if not end_time:
                from dateutil.parser import parse as dt_parse
                try:
                    start_dt = dt_parse(start_iso)
                    end_dt = start_dt + timedelta(hours=1)
                    end_iso = end_dt.isoformat()
                except Exception:
                    end_iso = start_iso
            else:
                end_iso = _parse_time(end_time)

            event_body = {
                "summary": summary,
                "start": {"dateTime": start_iso, "timeZone": _TZ},
                "end": {"dateTime": end_iso, "timeZone": _TZ},
            }

        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location

        # ── Conflict check before creating ────────────────────────────────
        start_key = event_body["start"]
        end_key = event_body["end"]
        if "dateTime" in start_key:
            conflicts = _get_conflicts(start_key["dateTime"], end_key["dateTime"])
            if conflicts:
                names = ", ".join(conflicts[:3])
                return (
                    f"Heads up — you already have {names} during that time. "
                    f"I went ahead and created {summary} anyway, but you may want to adjust."
                )

        event = svc.events().insert(calendarId="primary", body=event_body).execute()

        if "dateTime" in event_body.get("start", {}):
            return f"Event created: {summary} on {start_iso[:10]} at {start_iso[11:16]}."
        else:
            return f"All-day event created: {summary} on {start_date}."
    except Exception as e:
        logger.error("Calendar create failed: %s", e)
        return f"Sorry, I couldn't create that event: {e}"


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# MODIFY
# ---------------------------------------------------------------------------

def modify_calendar_event(
    search_query: str,
    new_summary: str = "",
    new_start_time: str = "",
    new_end_time: str = "",
    new_description: str = "",
    new_location: str = "",
) -> str:
    """Find a calendar event by name and update its fields.
    Only the provided fields are changed — everything else stays the same."""
    try:
        event = _find_event(search_query)
        if not event:
            return f"I couldn't find an upcoming event matching '{search_query}'."

        svc = _service()
        event_id = event["id"]
        old_summary = event.get("summary", "Untitled")

        # Build patch body — only include fields that are being changed
        patch: Dict[str, Any] = {}
        if new_summary:
            patch["summary"] = new_summary
        if new_start_time:
            if _is_date_only(new_start_time):
                patch["start"] = {"date": new_start_time}
            else:
                patch["start"] = {"dateTime": _parse_time(new_start_time), "timeZone": _TZ}
        if new_end_time:
            if _is_date_only(new_end_time):
                patch["end"] = {"date": new_end_time}
            else:
                patch["end"] = {"dateTime": _parse_time(new_end_time), "timeZone": _TZ}
        if new_description:
            patch["description"] = new_description
        if new_location:
            patch["location"] = new_location

        # If start changed but not end, auto-adjust end to 1 hour after new start
        if "start" in patch and "end" not in patch:
            from dateutil.parser import parse as dt_parse
            if "dateTime" in patch["start"]:
                try:
                    new_start = dt_parse(patch["start"]["dateTime"])
                    patch["end"] = {"dateTime": (new_start + timedelta(hours=1)).isoformat(), "timeZone": _TZ}
                except Exception:
                    pass

        if not patch:
            return f"No changes specified for '{old_summary}'."

        svc.events().patch(calendarId="primary", eventId=event_id, body=patch).execute()

        changes = []
        if new_summary:
            changes.append(f"renamed to {new_summary}")
        if new_start_time:
            changes.append(f"moved to {new_start_time}")
        if new_location:
            changes.append(f"location set to {new_location}")
        if new_description:
            changes.append("description updated")

        return f"Updated '{old_summary}': {', '.join(changes)}."
    except Exception as e:
        logger.error("Calendar modify failed: %s", e)
        return f"Sorry, I couldn't modify that event: {e}"


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

def delete_calendar_event(search_query: str) -> str:
    """Find a calendar event by name and delete it."""
    try:
        event = _find_event(search_query)
        if not event:
            return f"I couldn't find an upcoming event matching '{search_query}'."

        svc = _service()
        summary = event.get("summary", "Untitled")
        start = event["start"].get("dateTime", event["start"].get("date", ""))

        svc.events().delete(calendarId="primary", eventId=event["id"]).execute()

        if "T" in start:
            when = f"on {start[:10]} at {start[11:16]}"
        else:
            when = f"on {start}"
        return f"Deleted '{summary}' {when}."
    except Exception as e:
        logger.error("Calendar delete failed: %s", e)
        return f"Sorry, I couldn't delete that event: {e}"


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------

def search_calendar_events(
    query: str,
    days_ahead: int = 60,
    max_results: int = 5,
) -> str:
    """Search for calendar events by keyword across the next N days."""
    try:
        svc = _service()
        now = datetime.now()
        time_min = now.isoformat() + "-08:00"
        time_max = (now + timedelta(days=days_ahead)).isoformat() + "-08:00"

        result = svc.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
            q=query,
        ).execute()

        events = result.get("items", [])
        if not events:
            return f"No upcoming events matching '{query}' in the next {days_ahead} days."

        lines = []
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date", ""))
            summary = ev.get("summary", "Untitled")
            if "T" in start:
                lines.append(f"{start[:10]} at {start[11:16]} — {summary}")
            else:
                lines.append(f"{start} (all day) — {summary}")

        return f"Found {len(events)} event{'s' if len(events) != 1 else ''}: " + ". ".join(lines) + "."
    except Exception as e:
        logger.error("Calendar search failed: %s", e)
        return f"Sorry, I couldn't search your calendar: {e}"


# ---------------------------------------------------------------------------
# AVAILABILITY / CONFLICT CHECK
# ---------------------------------------------------------------------------

def _get_conflicts(start_iso: str, end_iso: str) -> List[str]:
    """Return list of event summaries that overlap with the given time range."""
    try:
        svc = _service()
        result = svc.events().list(
            calendarId="primary",
            timeMin=start_iso if start_iso.endswith("Z") or "+" in start_iso or "-08" in start_iso else start_iso + "-08:00",
            timeMax=end_iso if end_iso.endswith("Z") or "+" in end_iso or "-08" in end_iso else end_iso + "-08:00",
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return [ev.get("summary", "Untitled") for ev in result.get("items", [])]
    except Exception:
        return []


def check_calendar_availability(
    start_time: str,
    end_time: str = "",
    date: str = "",
) -> str:
    """Check if a time slot is free. Can check a specific range or a whole day."""
    try:
        svc = _service()
        now = datetime.now()

        if date and not start_time:
            # Check the whole day
            time_min = date + "T00:00:00-08:00"
            time_max = date + "T23:59:59-08:00"
            label = date
        else:
            start_iso = _parse_time(start_time)
            if not end_time:
                from dateutil.parser import parse as dt_parse
                try:
                    st = dt_parse(start_iso)
                    end_iso = (st + timedelta(hours=1)).isoformat()
                except Exception:
                    end_iso = start_iso
            else:
                end_iso = _parse_time(end_time)

            time_min = start_iso if "-" in start_iso[10:] or start_iso.endswith("Z") else start_iso + "-08:00"
            time_max = end_iso if "-" in end_iso[10:] or end_iso.endswith("Z") else end_iso + "-08:00"
            label = f"{start_iso[:16]} to {end_iso[11:16]}"

        result = svc.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = result.get("items", [])
        if not events:
            return f"You're free {label}. No conflicts."

        lines = []
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date", ""))
            summary = ev.get("summary", "Untitled")
            if "T" in start:
                lines.append(f"{start[11:16]} — {summary}")
            else:
                lines.append(f"All day — {summary}")

        return f"You have {len(events)} event{'s' if len(events) != 1 else ''} during that time: " + ". ".join(lines) + "."
    except Exception as e:
        logger.error("Calendar availability check failed: %s", e)
        return f"Sorry, I couldn't check your availability: {e}"
