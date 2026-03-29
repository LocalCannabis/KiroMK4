"""
coach/intent_router.py — Dispatches Coach tool calls to analyzer functions.

GPT selects from COACH_TOOL_SCHEMAS (OpenAI function-calling format).
This module executes the selected function against the PostgreSQL DB and
returns a JSON-serializable result for GPT to format conversationally.

Tool naming convention: coach_* (matches persona prefix pattern).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from .analyzer import ANALYZER_FUNCTIONS
from .db import CoachDB

logger = logging.getLogger("kiro.coach.router")

# ---------------------------------------------------------------------------
# Singleton DB (lazy-loaded, reused across calls within a session)
# ---------------------------------------------------------------------------

_db: CoachDB | None = None


def _get_db() -> CoachDB:
    global _db
    if _db is None:
        _db = CoachDB()
    return _db


# ---------------------------------------------------------------------------
# OpenAI function-calling schemas for Coach tools
# ---------------------------------------------------------------------------

COACH_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    # ── Task CRUD ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "coach_add_task",
            "description": (
                "Add a new task. Always define the next physical action — "
                "ambiguity kills momentum. Assign energy level if known."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The task title — a concrete next physical action.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional longer description or notes.",
                    },
                    "project_id": {
                        "type": "integer",
                        "description": "ID of the parent project. Omit for standalone tasks.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["inbox", "next", "waiting", "someday"],
                        "description": "GTD status. Default: 'inbox'.",
                        "default": "inbox",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Priority 1 (highest) to 5 (lowest). Default: 3.",
                        "default": 3,
                    },
                    "energy_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Required energy level for this task.",
                    },
                    "estimated_minutes": {
                        "type": "integer",
                        "description": "Rough time estimate in minutes.",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date in YYYY-MM-DD format. Only for hard deadlines.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Context tags as comma-separated (e.g. 'computer,home').",
                    },
                    "waiting_on": {
                        "type": "string",
                        "description": "Who/what this task is waiting on (for 'waiting' status).",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_list_tasks",
            "description": (
                "List tasks filtered by status, project, and/or energy level. "
                "Use to check what's on Tim's plate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["inbox", "next", "waiting", "someday", "done", "dropped"],
                        "description": "Filter by GTD status.",
                    },
                    "project_id": {
                        "type": "integer",
                        "description": "Filter by project.",
                    },
                    "energy_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Filter by energy level.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results. Default 20.",
                        "default": 20,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_complete_task",
            "description": "Mark a task as done. Celebrate the win — every completion counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID of the task to complete.",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_update_task",
            "description": (
                "Update any field on a task — status, priority, energy, estimate, etc. "
                "Use for GTD clarify/organise: moving from inbox to next, assigning to projects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "ID of the task to update.",
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["inbox", "next", "waiting", "someday", "done", "dropped"],
                    },
                    "priority": {"type": "integer"},
                    "energy_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "estimated_minutes": {"type": "integer"},
                    "due_date": {"type": "string"},
                    "context": {"type": "string"},
                    "waiting_on": {"type": "string"},
                    "project_id": {"type": "integer"},
                },
                "required": ["task_id"],
            },
        },
    },

    # ── Project CRUD ──────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "coach_add_project",
            "description": (
                "Create a new project (anything with more than one step). "
                "Define the desired outcome — what does DONE look like?"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Project name.",
                    },
                    "desired_outcome": {
                        "type": "string",
                        "description": "What does done look like? (GTD Natural Planning step 2)",
                    },
                    "area": {
                        "type": "string",
                        "description": "Life area (e.g. 'work', 'home', 'health', 'finance').",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_list_projects",
            "description": "List all active projects with task counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_completed": {
                        "type": "boolean",
                        "description": "Include completed/dropped projects. Default false.",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_project_status",
            "description": "Get detailed status of a specific project with task breakdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "Project ID.",
                    },
                },
                "required": ["project_id"],
            },
        },
    },

    # ── Capture (brain dump) ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "coach_capture_dump",
            "description": (
                "Quick capture — get something out of Tim's head into the system. "
                "No processing needed right now, just capture it. "
                "GTD rule: your mind is for having ideas, not holding them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_text": {
                        "type": "string",
                        "description": "Whatever Tim said, however messy. Capture verbatim.",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["voice", "text", "ambient"],
                        "description": "How this was captured.",
                        "default": "voice",
                    },
                },
                "required": ["raw_text"],
            },
        },
    },

    # ── Executive function tools ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "coach_whats_next",
            "description": (
                "Recommend the next task based on current energy and available time. "
                "Barkley's point-of-performance: help Tim right where he's stuck."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "energy": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Current energy level.",
                    },
                    "available_minutes": {
                        "type": "integer",
                        "description": "How many minutes Tim has right now.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_plan_day",
            "description": (
                "Create or view today's daily plan. Picks top priority tasks. "
                "Makes the day visible and concrete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Specific task IDs to plan for today. Auto-selects if omitted.",
                    },
                    "energy_forecast": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Expected energy level for today.",
                        "default": "medium",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_daily_progress",
            "description": "Check progress on today's plan vs what's actually done.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_inbox_review",
            "description": (
                "Review the inbox — unprocessed captures and unsorted tasks. "
                "GTD clarify step: for each item, decide the next action."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_weekly_review",
            "description": (
                "Run the weekly review: summarise the week, check all projects, "
                "process inbox, update everything. The non-negotiable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "notes": {
                        "type": "string",
                        "description": "Optional notes about the week.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach_energy_check",
            "description": (
                "Quick energy level check to guide task selection. "
                "Barkley's fuel tank: match the task to the tank."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "energy": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Current energy level.",
                    },
                },
                "required": [],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatch map — tool name → (analyzer function, param keys)
# ---------------------------------------------------------------------------

_DISPATCH_MAP = {
    "coach_whats_next":      ("whats_next",          ["energy", "available_minutes"]),
    "coach_plan_day":        ("plan_day",             ["task_ids", "energy_forecast"]),
    "coach_daily_progress":  ("daily_progress",       []),
    "coach_inbox_review":    ("inbox_review",         []),
    "coach_weekly_review":   ("log_weekly_review",    ["notes"]),  # dual: summary + log
    "coach_project_status":  ("project_status",       ["project_id"]),
    "coach_energy_check":    ("energy_check",         ["energy"]),
    "coach_capture_dump":    ("capture_and_extract",  ["raw_text", "source"]),
}


# ---------------------------------------------------------------------------
# Dispatch — called by ToolRegistry.execute()
# ---------------------------------------------------------------------------

def execute_coach_tool(name: str, args: Dict[str, Any]) -> str:
    """
    Execute a Coach tool call by name.
    Returns a JSON string of the result for GPT consumption.
    """
    db = _get_db()

    # ── Direct CRUD tools (not in ANALYZER_FUNCTIONS) ─────────────
    try:
        if name == "coach_add_task":
            # Build kwargs from args
            kwargs: Dict[str, Any] = {"title": args["title"]}
            for key in ("description", "project_id", "status", "priority",
                        "energy_level", "estimated_minutes", "due_date",
                        "context", "waiting_on"):
                if key in args and args[key] not in ("", None):
                    val = args[key]
                    if key in ("project_id", "priority", "estimated_minutes"):
                        val = int(val)
                    if key == "context" and isinstance(val, str):
                        val = [c.strip() for c in val.split(",")]
                    kwargs[key] = val
            task_id = db.create_task(**kwargs)
            task = db.get_task(task_id)
            return json.dumps({
                "created": True,
                "task_id": task_id,
                "title": task["title"] if task else kwargs["title"],
                "status": task["status"] if task else kwargs.get("status", "inbox"),
                "message": f"Task added: '{kwargs['title']}'",
            })

        elif name == "coach_list_tasks":
            filters = {}
            for key in ("status", "project_id", "energy_level"):
                if key in args and args[key] not in ("", None):
                    val = args[key]
                    if key == "project_id":
                        val = int(val)
                    filters[key] = val
            limit = int(args.get("limit", 20))
            tasks = db.list_tasks(**filters, limit=limit)
            return json.dumps({
                "tasks": [
                    {
                        "id": t["id"],
                        "title": t["title"],
                        "status": t["status"],
                        "project": t.get("project_name"),
                        "priority": t.get("priority"),
                        "energy": t.get("energy_level"),
                        "estimated_minutes": t.get("estimated_minutes"),
                        "due_date": str(t["due_date"]) if t.get("due_date") else None,
                    }
                    for t in tasks
                ],
                "count": len(tasks),
                "filters": filters,
            })

        elif name == "coach_complete_task":
            task_id = int(args["task_id"])
            task = db.get_task(task_id)
            if not task:
                return json.dumps({"error": True, "message": f"No task with ID {task_id}."})
            db.complete_task(task_id)
            completed_today = db.tasks_completed_today()
            return json.dumps({
                "completed": True,
                "task_id": task_id,
                "title": task["title"],
                "completed_today": completed_today,
                "message": f"Done: '{task['title']}'. That's {completed_today} today.",
            })

        elif name == "coach_update_task":
            task_id = int(args.pop("task_id"))
            updates = {}
            for key in ("title", "description", "status", "priority",
                        "energy_level", "estimated_minutes", "due_date",
                        "context", "waiting_on", "project_id"):
                if key in args and args[key] not in ("", None):
                    val = args[key]
                    if key in ("priority", "estimated_minutes", "project_id"):
                        val = int(val)
                    if key == "context" and isinstance(val, str):
                        val = [c.strip() for c in val.split(",")]
                    updates[key] = val
            if not updates:
                return json.dumps({"error": True, "message": "No fields to update."})
            db.update_task(task_id, **updates)
            task = db.get_task(task_id)
            return json.dumps({
                "updated": True,
                "task_id": task_id,
                "title": task["title"] if task else "?",
                "changes": list(updates.keys()),
                "message": f"Updated task {task_id}: {', '.join(updates.keys())}",
            })

        elif name == "coach_add_project":
            pid = db.create_project(
                name=args["name"],
                description=args.get("desired_outcome", ""),
                area=args.get("area", "general"),
            )
            return json.dumps({
                "created": True,
                "project_id": pid,
                "name": args["name"],
                "message": f"Project created: '{args['name']}'",
            })

        elif name == "coach_list_projects":
            include_done = args.get("include_completed", False)
            if include_done:
                # Get all — list both active and completed/dropped
                projects = []
                for st in ("active", "someday", "completed", "dropped"):
                    projects.extend(db.list_projects(status=st))
            else:
                projects = db.list_projects(status="active")

            # Enrich with task counts
            project_counts = db.get_project_task_counts()
            count_map = {pc["id"]: pc for pc in project_counts}

            return json.dumps({
                "projects": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "status": p["status"],
                        "area": p.get("area"),
                        "description": p.get("description"),
                        "task_count": count_map.get(p["id"], {}).get("total_count", 0),
                        "done_count": count_map.get(p["id"], {}).get("done_count", 0),
                    }
                    for p in projects
                ],
                "count": len(projects),
            })

        # ── Dispatch-map tools (analyzer functions) ───────────────
        elif name in _DISPATCH_MAP:
            func_name, param_keys = _DISPATCH_MAP[name]
            func = ANALYZER_FUNCTIONS[func_name]
            kwargs = {"db": db}
            for key in param_keys:
                if key in args and args[key] not in ("", None):
                    val = args[key]
                    if key in ("project_id", "available_minutes"):
                        val = int(val)
                    if key == "task_ids" and isinstance(val, str):
                        val = [int(x.strip()) for x in val.split(",")]
                    # plan_day expects 'top_tasks' not 'task_ids'
                    if key == "task_ids":
                        kwargs["top_tasks"] = val
                    elif key == "energy":
                        kwargs["reported_energy" if func_name == "energy_check" else "energy"] = val
                    else:
                        kwargs[key] = val
            result = func(**kwargs)
            return json.dumps(result, default=str)

        else:
            return json.dumps({"error": True, "message": f"Unknown Coach tool: {name}"})

    except Exception as exc:
        logger.error("Coach tool %s failed: %s", name, exc, exc_info=True)
        return json.dumps({"error": True, "message": f"Tool error: {exc}"})
