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
    from .google_calendar import create_calendar_event, list_calendar_events
    from .google_sheets import create_budget, add_expense, get_budget_summary, update_budget_item
    from .gmail import read_emails, send_email, draft_email, search_emails
    from .google_docs import create_doc, append_to_doc, read_doc
    _google_available = True
except ImportError as _e:
    import logging as _log
    _log.getLogger('kiro').warning('Google tools unavailable: %s', _e)


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
            "description": "Create a Google Calendar event. Use for scheduling meetings, reminders, appointments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title."},
                    "start_time": {"type": "string", "description": "Start time in ISO format, e.g. 2026-03-12T15:00:00."},
                    "end_time": {"type": "string", "description": "End time in ISO format. If omitted, defaults to 1 hour after start.", "default": ""},
                    "description": {"type": "string", "description": "Optional event description.", "default": ""},
                    "location": {"type": "string", "description": "Optional event location.", "default": ""},
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
    # ── Sheets (Finley's budget) ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_budget",
            "description": "Create a new budget spreadsheet in Google Sheets with default expense categories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Optional title for the spreadsheet.", "default": ""},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_expense",
            "description": "Log an expense in the budget spreadsheet. Tracks spending by category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Expense category, e.g. 'Food & Groceries', 'Transport', 'Entertainment'."},
                    "description": {"type": "string", "description": "What the expense was for."},
                    "amount": {"type": "number", "description": "Amount spent in dollars."},
                },
                "required": ["category", "description", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_budget_summary",
            "description": "Read budget summary — overall spending or for a specific category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Optional category to filter by, e.g. 'Food & Groceries'. Leave empty for overall.", "default": ""},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_budget_item",
            "description": "Update the monthly budget amount for a category, e.g. when rent goes up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Budget category to update."},
                    "monthly_budget": {"type": "number", "description": "New monthly budget amount in dollars."},
                },
                "required": ["category", "monthly_budget"],
            },
        },
    },
    # ── Gmail ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": "Read recent emails from Gmail. Defaults to unread inbox.",
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
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Persona → tool ownership mapping
# Personas see their owned tools PLUS all "shared" tools.
# ---------------------------------------------------------------------------

_PERSONA_TOOLS: Dict[str, List[str]] = {
    "kiro": ["create_calendar_event", "list_calendar_events", "read_emails", "send_email", "draft_email", "search_emails"],
    "finley": ["create_budget", "add_expense", "get_budget_summary", "update_budget_item", "read_emails", "search_emails"],
    "chef": ["create_doc", "append_to_doc", "read_doc"],
    "ops": ["create_doc", "append_to_doc", "read_doc", "read_emails", "send_email"],
    "coach": [],
    "doc": [],
    "sage": [],
    "ruth": [],
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
            )
        if name == "list_calendar_events" and _google_available:
            return list_calendar_events(
                time_range=args.get("time_range", "today"),
                max_results=int(args.get("max_results", 10)),
            )

        # ── Google Sheets (Finley) ────────────────────────────────────────
        if name == "create_budget" and _google_available:
            return create_budget(title=args.get("title", ""))
        if name == "add_expense" and _google_available:
            return add_expense(
                category=args["category"],
                description=args["description"],
                amount=float(args["amount"]),
            )
        if name == "get_budget_summary" and _google_available:
            return get_budget_summary(category=args.get("category", ""))
        if name == "update_budget_item" and _google_available:
            return update_budget_item(
                category=args["category"],
                monthly_budget=float(args["monthly_budget"]),
            )

        # ── Gmail ─────────────────────────────────────────────────────────
        if name == "read_emails" and _google_available:
            return read_emails(
                max_results=int(args.get("max_results", 5)),
                query=args.get("query", "is:unread"),
            )
        if name == "send_email" and _google_available:
            return send_email(to=args["to"], subject=args["subject"], body=args["body"])
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

        return f"Unknown tool: {name}"
