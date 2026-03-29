"""
tools/registry.py — OpenAI function-calling schemas and tool dispatch.

Usage in KiroOrchestrator:
    registry = ToolRegistry(cfg, notification_queue, logger)
    schemas   = registry.schemas()           # pass to OpenAI as 'tools='
    result    = registry.execute(name, args) # call after model picks a tool
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Dict, List

from .timer import set_timer
from .calculator import calculate
from .weather import get_weather
from .notes_lists import NotesAndLists

# Google integrations (lazy-loaded to avoid auth prompts on import)
_google_available = False
try:
    from .google_calendar import (create_calendar_event, list_calendar_events,
                                    modify_calendar_event, delete_calendar_event,
                                    search_calendar_events, check_calendar_availability)
    from .google_sheets import (read_sheet_range, write_sheet_cells, append_sheet_row,
                                create_spreadsheet, export_ynab_summary)
    from .gmail import (read_emails, send_email, draft_email, search_emails,
                        read_email_content, reply_to_email)
    from .google_docs import create_doc, append_to_doc, read_doc
    from .google_drive import search_drive, list_drive_folder, get_file_info
    _google_available = True
except ImportError as _e:
    import logging as _log
    _log.getLogger('kiro').warning('Google tools unavailable: %s', _e)

# Finley YNAB integration (lazy-loaded)
_finley_available = False
try:
    from finley.intent_router import FINLEY_TOOL_SCHEMAS, execute_finley_tool
    _finley_available = True
except ImportError as _e:
    import logging as _log2
    _log2.getLogger('kiro').warning('Finley YNAB tools unavailable: %s', _e)

# Jack master grower integration (lazy-loaded)
_jack_available = False
try:
    from jack.intent_router import JACK_TOOL_SCHEMAS, execute_jack_tool
    _jack_available = True
except ImportError as _e:
    import logging as _log3
    _log3.getLogger('kiro').warning('Jack grow tools unavailable: %s', _e)

# Coach executive function integration (lazy-loaded)
_coach_available = False
try:
    from coach.intent_router import COACH_TOOL_SCHEMAS, execute_coach_tool
    _coach_available = True
except ImportError as _e:
    import logging as _log4
    _log4.getLogger('kiro').warning('Coach tools unavailable: %s', _e)


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Set a countdown timer that will notify Tim when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Duration in seconds. Convert minutes/hours as needed.",
                    },
                    "label": {
                        "type": "string",
                        "description": "What the timer is for, e.g. 'check the soil' or 'pasta'.",
                        "default": "",
                    },
                },
                "required": ["seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Save a note or piece of information for Tim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The note content."},
                    "tags": {
                        "type": "string",
                        "description": "Optional comma-separated tags, e.g. 'grow,cannabis'.",
                        "default": "",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_add",
            "description": "Add an item to a named list (grocery, todo, materials, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                    "item": {"type": "string", "description": "Item to add."},
                },
                "required": ["list_name", "item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_remove",
            "description": "Remove an item from a named list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                    "item": {"type": "string", "description": "Item to remove."},
                },
                "required": ["list_name", "item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_get",
            "description": "Read out everything on a named list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                },
                "required": ["list_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_clear",
            "description": "Clear all items from a named list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                },
                "required": ["list_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a math expression or unit conversion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A math expression, e.g. '2 * 8 + 3' or 'sqrt(144)'.",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions in Vancouver, BC.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Google tool schemas — only registered if google libs are available
# ---------------------------------------------------------------------------

_GOOGLE_SCHEMAS: List[Dict[str, Any]] = [
    # ── Calendar ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a Google Calendar event. Use for scheduling meetings, reminders, appointments. Supports timed and all-day events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title."},
                    "start_time": {"type": "string", "description": "Start time in ISO format (e.g. 2026-03-12T15:00:00) or date-only for all-day events (e.g. 2026-03-12)."},
                    "end_time": {"type": "string", "description": "End time in ISO format. If omitted, defaults to 1 hour after start.", "default": ""},
                    "description": {"type": "string", "description": "Optional event description.", "default": ""},
                    "location": {"type": "string", "description": "Optional event location.", "default": ""},
                    "all_day": {"type": "boolean", "description": "Set to true for all-day events.", "default": False},
                },
                "required": ["summary", "start_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "List upcoming Google Calendar events. Answers 'what's on my calendar?' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_range": {"type": "string", "description": "One of: 'today', 'tomorrow', 'week', or an ISO date like '2026-03-15'.", "default": "today"},
                    "max_results": {"type": "integer", "description": "Max events to return.", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_calendar_event",
            "description": "Modify an existing calendar event. Find it by name and update any fields (time, title, location, description). Use for rescheduling, renaming, or updating events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "Name or keyword to find the event, e.g. 'dentist' or 'meeting with Dave'."},
                    "new_summary": {"type": "string", "description": "New event title. Leave empty to keep current.", "default": ""},
                    "new_start_time": {"type": "string", "description": "New start time in ISO format. Leave empty to keep current.", "default": ""},
                    "new_end_time": {"type": "string", "description": "New end time in ISO format. Leave empty to auto-adjust.", "default": ""},
                    "new_description": {"type": "string", "description": "New description. Leave empty to keep current.", "default": ""},
                    "new_location": {"type": "string", "description": "New location. Leave empty to keep current.", "default": ""},
                },
                "required": ["search_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": "Delete/cancel a calendar event. Finds the event by name and removes it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "Name or keyword to find the event to delete."},
                },
                "required": ["search_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_calendar_events",
            "description": "Search for calendar events by keyword. Use when Tim asks 'when is my dentist appointment?' or 'do I have anything with Dave coming up?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword to search for in event titles."},
                    "days_ahead": {"type": "integer", "description": "How many days ahead to search.", "default": 60},
                    "max_results": {"type": "integer", "description": "Max events to return.", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_calendar_availability",
            "description": "Check if a time slot is free or busy. Use when Tim asks 'am I free Thursday afternoon?' or 'do I have anything on the 15th?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "string", "description": "Start of the time range to check (ISO format).", "default": ""},
                    "end_time": {"type": "string", "description": "End of the time range to check (ISO format).", "default": ""},
                    "date": {"type": "string", "description": "Check an entire day (ISO date like '2026-03-15'). Use instead of start/end for whole-day checks.", "default": ""},
                },
                "required": [],
            },
        },
    },
    # ── Gmail ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": "Read recent emails from Gmail. Defaults to unread inbox. Returns sender and subject for each.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Max emails to read.", "default": 5},
                    "query": {"type": "string", "description": "Gmail search query, e.g. 'is:unread', 'from:dave@example.com'.", "default": "is:unread"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_email_content",
            "description": "Read the full body/content of a specific email. Use when Tim asks 'what did that email say?' or 'read me the email from Dave'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "Gmail search query to find the specific email, e.g. 'from:dave subject:meeting' or 'invoice from amazon'."},
                    "max_chars": {"type": "integer", "description": "Max characters to return from the body.", "default": 1500},
                },
                "required": ["search_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email via Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email body text."},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_email",
            "description": "Reply to an existing email thread. Finds the email by search query and sends a threaded reply preserving conversation context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "Gmail search to find the email to reply to, e.g. 'from:dave subject:meeting'."},
                    "body": {"type": "string", "description": "The reply message text."},
                },
                "required": ["search_query", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_email",
            "description": "Create a draft email in Gmail (not sent yet).",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email body text."},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": "Search Gmail for emails matching a query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query, e.g. 'invoice from:amazon'."},
                    "max_results": {"type": "integer", "description": "Max results.", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    # ── Google Docs ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_doc",
            "description": "Create a new Google Doc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title."},
                    "initial_content": {"type": "string", "description": "Optional initial content to write.", "default": ""},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_doc",
            "description": "Append text to an existing Google Doc (found by title).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the document to append to."},
                    "content": {"type": "string", "description": "Text to append."},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_doc",
            "description": "Read the content of a Google Doc (found by title).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the document to read."},
                },
                "required": ["title"],
            },
        },
    },
    # ── Google Drive ──────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_drive",
            "description": "Search Google Drive for files by name. Optionally filter by type (doc, sheet, slides, pdf, folder).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "File name or keyword to search for."},
                    "max_results": {"type": "integer", "description": "Max results to return.", "default": 5},
                    "file_type": {"type": "string", "description": "Optional filter: 'doc', 'sheet', 'slides', 'pdf', 'folder'.", "default": ""},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_drive_folder",
            "description": "List files in a Google Drive folder. If no folder specified, lists recent files in root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "Name of the folder to list. Leave empty for Drive root.", "default": ""},
                    "max_results": {"type": "integer", "description": "Max files to list.", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": "Get metadata about a file in Google Drive — type, size, last modified, owner, shared status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {"type": "string", "description": "Name of the file to look up."},
                },
                "required": ["file_name"],
            },
        },
    },
    # ── Generic Sheets ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_sheet_range",
            "description": "Read a range of cells from any Google Sheet (found by name). Use for reading data from spreadsheets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_name": {"type": "string", "description": "Name of the Google Sheet to read from."},
                    "range_notation": {"type": "string", "description": "A1 notation range, e.g. 'A1:D10'.", "default": "A1:Z20"},
                    "sheet_tab": {"type": "string", "description": "Optional tab/sheet name within the spreadsheet.", "default": ""},
                },
                "required": ["spreadsheet_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_sheet_cells",
            "description": "Write values to cells in a Google Sheet. Use pipe | to separate rows and comma , to separate cells.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_name": {"type": "string", "description": "Name of the Google Sheet."},
                    "range_notation": {"type": "string", "description": "Starting cell or range, e.g. 'A1' or 'B5:D5'."},
                    "values": {"type": "string", "description": "Data to write. Pipe-separated rows, comma-separated cells. E.g. 'Name,Score|Alice,95|Bob,87'."},
                    "sheet_tab": {"type": "string", "description": "Optional tab/sheet name.", "default": ""},
                },
                "required": ["spreadsheet_name", "range_notation", "values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_sheet_row",
            "description": "Append a row to the bottom of a Google Sheet. Good for logging data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_name": {"type": "string", "description": "Name of the Google Sheet."},
                    "values": {"type": "string", "description": "Comma-separated cell values for the new row, e.g. '2025-07-15, Workout, 45 min'."},
                    "sheet_tab": {"type": "string", "description": "Optional tab/sheet name.", "default": ""},
                },
                "required": ["spreadsheet_name", "values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_spreadsheet",
            "description": "Create a new Google Spreadsheet with optional header row.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title for the new spreadsheet."},
                    "headers": {"type": "string", "description": "Optional comma-separated column headers, e.g. 'Date, Category, Amount, Notes'.", "default": ""},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_ynab_summary",
            "description": "Export a snapshot of Tim's YNAB budget data to a Google Sheet. Creates tabs for Accounts, Budget, and Recent Transactions. YNAB remains the single source of truth — the sheet is a read-only backup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet_name": {"type": "string", "description": "Name for the export spreadsheet.", "default": "Kiro Financial Summary"},
                },
                "required": [],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Persona → tool ownership mapping
# Personas see their owned tools PLUS all "shared" tools.
# ---------------------------------------------------------------------------

_PERSONA_TOOLS: Dict[str, List[str]] = {
    "kiro": [
        # Calendar (full CRUD + search + availability)
        "create_calendar_event", "list_calendar_events", "modify_calendar_event",
        "delete_calendar_event", "search_calendar_events", "check_calendar_availability",
        # Email (full suite)
        "read_emails", "read_email_content", "send_email", "reply_to_email", "draft_email", "search_emails",
        # Drive
        "search_drive", "list_drive_folder", "get_file_info",
        # Docs
        "create_doc", "append_to_doc", "read_doc",
        # Generic Sheets
        "read_sheet_range", "write_sheet_cells", "append_sheet_row", "create_spreadsheet",
    ],
    "finley": [
        # Sheets (generic + YNAB export)
        "read_sheet_range", "write_sheet_cells", "append_sheet_row", "create_spreadsheet",
        "export_ynab_summary",
        # Email (read + search for invoice lookups etc.)
        "read_emails", "read_email_content", "search_emails",
        # Calendar (scheduling financial reviews, bill due dates)
        "list_calendar_events", "search_calendar_events",
        # YNAB tools
        "ynab_spending_by_category", "ynab_spending_by_payee", "ynab_spending_trend",
        "ynab_daily_spending_rate", "ynab_top_transactions", "ynab_budget_vs_actual",
        "ynab_overspent_categories", "ynab_remaining_budget", "ynab_days_until_broke",
        "ynab_account_balances", "ynab_net_worth", "ynab_credit_card_balances",
        "ynab_upcoming_bills", "ynab_recurring_summary", "ynab_income_vs_expenses",
    ],
    "chef": [
        # Docs (recipes)
        "create_doc", "append_to_doc", "read_doc",
        # Calendar (meal planning, dinner party scheduling)
        "create_calendar_event", "list_calendar_events", "search_calendar_events",
        # Sheets (grocery lists, meal plans)
        "read_sheet_range", "append_sheet_row",
    ],
    "ops": [
        # Docs (runbooks, notes)
        "create_doc", "append_to_doc", "read_doc",
        # Email (deployments, alerts)
        "read_emails", "read_email_content", "send_email", "reply_to_email", "search_emails",
        # Calendar (standups, deployments)
        "create_calendar_event", "list_calendar_events", "modify_calendar_event", "search_calendar_events",
        # Drive (finding project files)
        "search_drive", "list_drive_folder", "get_file_info",
        # Sheets (project tracking)
        "read_sheet_range", "write_sheet_cells", "append_sheet_row", "create_spreadsheet",
    ],
    "coach": [
        # Coach executive function tools (GTD + Barkley + Atomic Habits)
        "coach_add_task", "coach_list_tasks", "coach_complete_task", "coach_update_task",
        "coach_add_project", "coach_list_projects", "coach_project_status",
        "coach_capture_dump", "coach_whats_next", "coach_plan_day",
        "coach_daily_progress", "coach_inbox_review", "coach_weekly_review",
        "coach_energy_check",
        # Calendar (scheduling, reminders)
        "create_calendar_event", "list_calendar_events", "modify_calendar_event",
        "search_calendar_events", "check_calendar_availability",
        # Sheets (tracking, logs)
        "read_sheet_range", "append_sheet_row", "create_spreadsheet",
        # Docs (plans, reviews)
        "create_doc", "append_to_doc", "read_doc",
    ],
    "doc": [
        # Calendar (check-in reminders, therapy appointments)
        "create_calendar_event", "list_calendar_events", "search_calendar_events",
        # Docs (journaling, reflections)
        "create_doc", "append_to_doc", "read_doc",
    ],
    "sage": [
        # Docs (debate notes, reading lists)
        "create_doc", "append_to_doc", "read_doc",
        # Drive (finding reference files)
        "search_drive", "get_file_info",
    ],
    "ruth": [
        # Calendar (awareness of Tim's schedule for context)
        "list_calendar_events", "search_calendar_events",
        # Email (awareness of what's happening)
        "read_emails", "read_email_content",
        # Docs (letters, reflections)
        "create_doc", "append_to_doc", "read_doc",
    ],
    "lisa": [
        # Calendar (hangout planning, event awareness)
        "create_calendar_event", "list_calendar_events", "search_calendar_events",
        "check_calendar_availability",
        # Email (keeping tabs, fun forwarding)
        "read_emails", "read_email_content", "search_emails",
        # Docs (brainstorm docs, idea dumps)
        "create_doc", "append_to_doc", "read_doc",
        # Drive (finding interesting files)
        "search_drive",
    ],
    "jack": [
        # Jack grow management tools
        "jack_log_checkin", "jack_get_grow_status", "jack_get_recent_logs",
        "jack_update_grow_stage", "jack_log_watering", "jack_get_feeding_schedule",
        "jack_compute_vpd", "jack_compute_dli",
        "jack_create_grow", "jack_setup_tent", "jack_list_grows",
    ],
}

# Tools every persona can access regardless of ownership
_SHARED_TOOLS = {"set_timer", "add_note", "list_add", "list_remove", "list_get", "list_clear", "calculate", "get_weather"}


class ToolRegistry:
    def __init__(
        self,
        cfg: Dict[str, Any],
        notification_queue: queue.Queue,
        logger: logging.Logger,
    ) -> None:
        self.logger = logger
        self._nq = notification_queue
        self._enabled = cfg.get("tools", {}).get("enabled", False)
        self._allow_list = set(cfg.get("tools", {}).get("allow_list", []))

        db_path = cfg.get("memory", {}).get("sqlite_path", "./data/kiro.db")
        self._nl = NotesAndLists(db_path)

        # Merge Google schemas into the main list if available
        self._all_schemas = list(_SCHEMAS)
        if _google_available:
            self._all_schemas.extend(_GOOGLE_SCHEMAS)
            self.logger.info("Google tools loaded: calendar, sheets, gmail, docs")

        # Merge Finley YNAB schemas if available
        if _finley_available:
            self._all_schemas.extend(FINLEY_TOOL_SCHEMAS)
            self.logger.info("Finley YNAB tools loaded: %d financial analysis tools", len(FINLEY_TOOL_SCHEMAS))

        # Merge Jack grow management schemas if available
        if _jack_available:
            self._all_schemas.extend(JACK_TOOL_SCHEMAS)
            self.logger.info("Jack grow tools loaded: %d grow management tools", len(JACK_TOOL_SCHEMAS))

        # Merge Coach executive function schemas if available
        if _coach_available:
            self._all_schemas.extend(COACH_TOOL_SCHEMAS)
            self.logger.info("Coach tools loaded: %d executive function tools", len(COACH_TOOL_SCHEMAS))

        if self._enabled:
            self.logger.info("Tools enabled: %s", sorted(self._allow_list))
        else:
            self.logger.info("Tools disabled (tools.enabled=false in config).")

    def schemas(self, persona: str = "kiro") -> List[Dict[str, Any]]:
        """Return OpenAI-formatted tool schemas filtered by allow_list and persona ownership."""
        if not self._enabled:
            return []

        persona_owned = set(_PERSONA_TOOLS.get(persona, []))
        visible = _SHARED_TOOLS | persona_owned

        def _allowed(tool_name: str) -> bool:
            # Must be in the config allow_list AND visible to this persona
            in_allow = tool_name in self._allow_list or any(
                kw in tool_name for kw in self._allow_list
            )
            return in_allow and tool_name in visible

        return [s for s in self._all_schemas if _allowed(s["function"]["name"])]

    def execute(self, name: str, args: Dict[str, Any]) -> str:
        """Dispatch a tool call by name. Returns a result string."""
        self.logger.info("Tool call: %s(%s)", name, args)
        try:
            return self._dispatch(name, args)
        except Exception as exc:
            self.logger.error("Tool '%s' failed: %s", name, exc)
            return f"Tool error: {exc}"

    def _dispatch(self, name: str, args: Dict[str, Any]) -> str:
        # ── Core tools ────────────────────────────────────────────────────
        if name == "set_timer":
            return set_timer(
                seconds=int(args.get("seconds", 60)),
                label=args.get("label", ""),
                notification_queue=self._nq,
                logger=self.logger,
            )
        if name == "add_note":
            return self._nl.add_note(args["content"], args.get("tags", ""))
        if name == "list_add":
            return self._nl.add_item(args["list_name"], args["item"])
        if name == "list_remove":
            return self._nl.remove_item(args["list_name"], args["item"])
        if name == "list_get":
            return self._nl.get_list(args["list_name"])
        if name == "list_clear":
            return self._nl.clear_list(args["list_name"])
        if name == "calculate":
            return calculate(args["expression"])
        if name == "get_weather":
            return get_weather()

        # ── Google Calendar ───────────────────────────────────────────────
        if name == "create_calendar_event" and _google_available:
            return create_calendar_event(
                summary=args["summary"],
                start_time=args["start_time"],
                end_time=args.get("end_time", ""),
                description=args.get("description", ""),
                location=args.get("location", ""),
                all_day=bool(args.get("all_day", False)),
            )
        if name == "list_calendar_events" and _google_available:
            return list_calendar_events(
                time_range=args.get("time_range", "today"),
                max_results=int(args.get("max_results", 10)),
            )
        if name == "modify_calendar_event" and _google_available:
            return modify_calendar_event(
                search_query=args["search_query"],
                new_summary=args.get("new_summary", ""),
                new_start_time=args.get("new_start_time", ""),
                new_end_time=args.get("new_end_time", ""),
                new_description=args.get("new_description", ""),
                new_location=args.get("new_location", ""),
            )
        if name == "delete_calendar_event" and _google_available:
            return delete_calendar_event(search_query=args["search_query"])
        if name == "search_calendar_events" and _google_available:
            return search_calendar_events(
                query=args["query"],
                days_ahead=int(args.get("days_ahead", 60)),
                max_results=int(args.get("max_results", 5)),
            )
        if name == "check_calendar_availability" and _google_available:
            return check_calendar_availability(
                start_time=args.get("start_time", ""),
                end_time=args.get("end_time", ""),
                date=args.get("date", ""),
            )

        # ── Gmail ─────────────────────────────────────────────────────────
        if name == "read_emails" and _google_available:
            return read_emails(
                max_results=int(args.get("max_results", 5)),
                query=args.get("query", "is:unread"),
            )
        if name == "read_email_content" and _google_available:
            return read_email_content(
                search_query=args["search_query"],
                max_chars=int(args.get("max_chars", 1500)),
            )
        if name == "send_email" and _google_available:
            return send_email(to=args["to"], subject=args["subject"], body=args["body"])
        if name == "reply_to_email" and _google_available:
            return reply_to_email(search_query=args["search_query"], body=args["body"])
        if name == "draft_email" and _google_available:
            return draft_email(to=args["to"], subject=args["subject"], body=args["body"])
        if name == "search_emails" and _google_available:
            return search_emails(query=args["query"], max_results=int(args.get("max_results", 5)))

        # ── Google Docs ───────────────────────────────────────────────────
        if name == "create_doc" and _google_available:
            return create_doc(title=args["title"], initial_content=args.get("initial_content", ""))
        if name == "append_to_doc" and _google_available:
            return append_to_doc(title=args["title"], content=args["content"])
        if name == "read_doc" and _google_available:
            return read_doc(title=args["title"])

        # ── Google Drive ──────────────────────────────────────────────────
        if name == "search_drive" and _google_available:
            return search_drive(
                query=args["query"],
                max_results=int(args.get("max_results", 5)),
                file_type=args.get("file_type", ""),
            )
        if name == "list_drive_folder" and _google_available:
            return list_drive_folder(
                folder_name=args.get("folder_name", ""),
                max_results=int(args.get("max_results", 10)),
            )
        if name == "get_file_info" and _google_available:
            return get_file_info(file_name=args["file_name"])

        # ── Generic Sheets ────────────────────────────────────────────────
        if name == "read_sheet_range" and _google_available:
            return read_sheet_range(
                spreadsheet_name=args["spreadsheet_name"],
                range_notation=args.get("range_notation", "A1:Z20"),
                sheet_tab=args.get("sheet_tab", ""),
            )
        if name == "write_sheet_cells" and _google_available:
            return write_sheet_cells(
                spreadsheet_name=args["spreadsheet_name"],
                range_notation=args["range_notation"],
                values=args["values"],
                sheet_tab=args.get("sheet_tab", ""),
            )
        if name == "append_sheet_row" and _google_available:
            return append_sheet_row(
                spreadsheet_name=args["spreadsheet_name"],
                values=args["values"],
                sheet_tab=args.get("sheet_tab", ""),
            )
        if name == "create_spreadsheet" and _google_available:
            return create_spreadsheet(
                title=args["title"],
                headers=args.get("headers", ""),
            )
        if name == "export_ynab_summary" and _google_available:
            return export_ynab_summary(
                sheet_name=args.get("sheet_name", "Kiro Financial Summary"),
            )

        # ── Finley YNAB + profiling tools ─────────────────────────────────
        if (name.startswith("ynab_") or name.startswith("finley_")) and _finley_available:
            return execute_finley_tool(name, args)

        # ── Jack grow management tools ─────────────────────────────────────
        if name.startswith("jack_") and _jack_available:
            return execute_jack_tool(name, args)

        # ── Coach executive function tools ─────────────────────────────────
        if name.startswith("coach_") and _coach_available:
            return execute_coach_tool(name, args)

        return f"Unknown tool: {name}"
