"""
coach/analyzer.py — Task analysis and executive function support.

All functions operate on the PostgreSQL coach tables via CoachDB.
Each returns a dict suitable for GPT to format conversationally.

Design philosophy (from Barkley):
    These are POINT-OF-PERFORMANCE tools — they help Tim where and when
    he's struggling, not in abstract planning sessions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .db import CoachDB

logger = logging.getLogger("kiro.coach.analyzer")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _start_of_week() -> str:
    """Monday of the current week."""
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------

def whats_next(
    db: CoachDB,
    energy: str = "",
    available_minutes: int = 0,
) -> dict:
    """
    Recommend the next task based on energy level and available time.
    If no energy specified, returns the raw priority-ordered list.
    Implements: Barkley's point-of-performance + GTD's engage criteria.
    """
    filters: Dict[str, Any] = {"status": "next"}
    if energy and energy in ("low", "medium", "high"):
        filters["energy_level"] = energy

    tasks = db.list_tasks(**filters, limit=20)

    if available_minutes and available_minutes > 0:
        tasks = [t for t in tasks if
                 t.get("estimated_minutes") is None or
                 t["estimated_minutes"] <= available_minutes]

    if not tasks:
        # Fall back to inbox — maybe there's something quick
        inbox = db.get_inbox()
        if inbox:
            return {
                "recommendation": "empty_next_list",
                "message": (
                    "Your Next Actions list is empty for that energy level. "
                    f"You have {len(inbox)} items in your inbox though — "
                    "want to process those first?"
                ),
                "inbox_count": len(inbox),
                "suggested_tasks": [],
            }
        return {
            "recommendation": "all_clear",
            "message": "Nothing on your plate right now. Enjoy the calm.",
            "suggested_tasks": [],
        }

    # Pick top 3 recommendations
    recommended = tasks[:3]
    return {
        "recommendation": "tasks_available",
        "energy_filter": energy or "any",
        "time_filter_minutes": available_minutes or "any",
        "suggested_tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "project": t.get("project_name"),
                "priority": t.get("priority"),
                "energy": t.get("energy_level"),
                "estimated_minutes": t.get("estimated_minutes"),
                "due_date": str(t["due_date"]) if t.get("due_date") else None,
            }
            for t in recommended
        ],
        "total_matching": len(tasks),
    }


def plan_day(
    db: CoachDB,
    top_tasks: List[int] | None = None,
    energy_forecast: str = "medium",
) -> dict:
    """
    Create or update today's daily plan.
    If top_tasks provided, uses those IDs. Otherwise auto-selects based on
    GTD priority + approaching deadlines.
    Implements: GTD daily review + Barkley's externalize time.
    """
    today = _today()

    if not top_tasks:
        # Auto-select: urgent first, then next actions by priority
        due_soon = db.get_tasks_due_soon(days=2)
        next_actions = db.get_next_actions(limit=10)

        # Merge: due soon tasks first, then fill from next actions
        seen_ids = set()
        selected = []
        for t in due_soon:
            if t["id"] not in seen_ids and len(selected) < 5:
                selected.append(t)
                seen_ids.add(t["id"])
        for t in next_actions:
            if t["id"] not in seen_ids and len(selected) < 5:
                selected.append(t)
                seen_ids.add(t["id"])

        top_tasks = [t["id"] for t in selected[:db.get_config("daily_top_n", 3)]]

    # Fetch full task details for the plan
    plan_tasks = []
    total_minutes = 0
    for tid in top_tasks:
        task = db.get_task(tid)
        if task:
            plan_tasks.append(task)
            if task.get("estimated_minutes"):
                total_minutes += task["estimated_minutes"]

    db.set_daily_plan(today, top_tasks)

    return {
        "plan_date": today,
        "energy_forecast": energy_forecast,
        "top_tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "project": t.get("project_name"),
                "estimated_minutes": t.get("estimated_minutes"),
                "energy": t.get("energy_level"),
            }
            for t in plan_tasks
        ],
        "total_estimated_minutes": total_minutes,
        "task_count": len(plan_tasks),
        "message": (
            f"Your plan for today: {len(plan_tasks)} tasks, "
            f"~{total_minutes} minutes estimated."
            if plan_tasks
            else "No tasks selected for today. Want to pick some?"
        ),
    }


def daily_progress(db: CoachDB) -> dict:
    """
    Compare today's plan to actual progress.
    Implements: Barkley's externalize time + GTD reflect.
    """
    today = _today()
    plan = db.get_daily_plan(today)
    completed_today = db.tasks_completed_today()
    status_counts = db.count_tasks_by_status()

    planned_ids = plan.get("top_tasks", []) if plan else []
    planned_tasks = []
    completed_planned = 0

    for tid in planned_ids:
        task = db.get_task(tid)
        if task:
            planned_tasks.append({
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "done": task["status"] == "done",
            })
            if task["status"] == "done":
                completed_planned += 1

    return {
        "plan_date": today,
        "planned_count": len(planned_ids),
        "completed_planned": completed_planned,
        "total_completed_today": completed_today,
        "planned_tasks": planned_tasks,
        "status_overview": status_counts,
        "progress_pct": (
            round(completed_planned / len(planned_ids) * 100)
            if planned_ids else 0
        ),
        "message": _progress_message(completed_planned, len(planned_ids), completed_today),
    }


def _progress_message(done_planned: int, total_planned: int, total_done: int) -> str:
    """Generate an encouraging, no-shame progress message."""
    if total_planned == 0:
        if total_done > 0:
            return f"No plan set, but you've knocked out {total_done} tasks. Nice."
        return "No plan set for today yet. Want to make one?"

    if done_planned == total_planned:
        extra = total_done - done_planned
        msg = "All planned tasks done!"
        if extra > 0:
            msg += f" Plus {extra} extra. Solid day."
        return msg

    remaining = total_planned - done_planned
    return (
        f"{done_planned}/{total_planned} planned tasks done. "
        f"{remaining} to go — pick the easiest one next."
    )


def inbox_review(db: CoachDB) -> dict:
    """
    Summarise unprocessed captures for GTD clarify/process step.
    Implements: GTD clarify stage.
    """
    captures = db.get_unprocessed_captures()
    inbox_tasks = db.get_inbox()

    return {
        "unprocessed_captures": [
            {
                "id": c["id"],
                "raw_text": c["raw_text"],
                "source": c["source"],
                "captured_at": str(c["created_at"]),
            }
            for c in captures
        ],
        "inbox_tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "created_at": str(t["created_at"]),
            }
            for t in inbox_tasks
        ],
        "capture_count": len(captures),
        "inbox_task_count": len(inbox_tasks),
        "message": (
            f"{len(captures)} captures to process and {len(inbox_tasks)} "
            "tasks in your inbox."
            if captures or inbox_tasks
            else "Inbox zero. Clean slate."
        ),
    }


def weekly_summary(db: CoachDB) -> dict:
    """
    Generate a weekly review summary.
    Implements: GTD weekly review — the 'critical success factor'.
    """
    week_start = _start_of_week()
    week_start_dt = datetime.strptime(week_start, "%Y-%m-%d")
    completed_this_week = db.tasks_completed_since(week_start_dt)
    status_counts = db.count_tasks_by_status()
    project_counts = db.get_project_task_counts()
    last_review = db.get_last_weekly_review()
    inbox_tasks = db.get_inbox()
    waiting = db.get_waiting_for()
    captures = db.get_unprocessed_captures()

    return {
        "week_start": week_start,
        "completed_this_week": completed_this_week,
        "status_overview": status_counts,
        "project_breakdown": project_counts,
        "inbox_count": len(inbox_tasks),
        "waiting_for_count": len(waiting),
        "unprocessed_captures": len(captures),
        "waiting_for": [
            {"id": t["id"], "title": t["title"], "waiting_on": t.get("waiting_on")}
            for t in waiting[:10]
        ],
        "last_review": str(last_review["review_date"]) if last_review else "never",
        "message": _weekly_message(
            completed_this_week, status_counts, len(inbox_tasks),
            len(captures), last_review,
        ),
    }


def _weekly_message(
    completed: int,
    status: dict,
    inbox_count: int,
    capture_count: int,
    last_review: dict | None,
) -> str:
    """Build the weekly review summary message."""
    parts = [f"This week: {completed} tasks completed."]

    active = status.get("next", 0) + status.get("waiting", 0)
    if active:
        parts.append(f"{active} active tasks ({status.get('next', 0)} next, "
                      f"{status.get('waiting', 0)} waiting).")

    someday = status.get("someday", 0)
    if someday:
        parts.append(f"{someday} on the someday/maybe list.")

    if inbox_count or capture_count:
        parts.append(f"{inbox_count + capture_count} items need processing.")

    if not last_review:
        parts.append("No weekly review on record yet — let's change that.")

    return " ".join(parts)


def project_status(db: CoachDB, project_id: int) -> dict:
    """
    Detailed task breakdown for a single project.
    """
    project = db.get_project(project_id)
    if not project:
        return {"error": True, "message": f"No project found with ID {project_id}."}

    tasks = db.list_tasks(project_id=project_id, limit=100)
    by_status: Dict[str, List[dict]] = {}
    for t in tasks:
        s = t["status"]
        if s not in by_status:
            by_status[s] = []
        by_status[s].append({
            "id": t["id"],
            "title": t["title"],
            "priority": t.get("priority"),
            "energy": t.get("energy_level"),
            "due_date": str(t["due_date"]) if t.get("due_date") else None,
        })

    total = len(tasks)
    done = len(by_status.get("done", []))

    return {
        "project_id": project_id,
        "project_name": project["name"],
        "project_status": project["status"],
        "outcome": project.get("description"),
        "total_tasks": total,
        "completed": done,
        "progress_pct": round(done / total * 100) if total else 0,
        "tasks_by_status": by_status,
    }


def task_detail(db: CoachDB, task_id: int) -> dict:
    """Get full detail for a single task with project context."""
    task = db.get_task(task_id)
    if not task:
        return {"error": True, "message": f"No task found with ID {task_id}."}

    result = {
        "id": task["id"],
        "title": task["title"],
        "description": task.get("description"),
        "status": task["status"],
        "priority": task.get("priority"),
        "energy_level": task.get("energy_level"),
        "context": task.get("context"),
        "estimated_minutes": task.get("estimated_minutes"),
        "due_date": str(task["due_date"]) if task.get("due_date") else None,
        "waiting_on": task.get("waiting_on"),
        "created_at": str(task["created_at"]),
    }

    if task.get("project_id"):
        project = db.get_project(task["project_id"])
        if project:
            result["project"] = {
                "id": project["id"],
                "name": project["name"],
                "status": project["status"],
            }

    return result


def energy_check(db: CoachDB, reported_energy: str = "") -> dict:
    """
    Quick energy assessment to guide task selection.
    Implements: Barkley's EF fuel tank concept.
    """
    status_counts = db.count_tasks_by_status()
    completed_today = db.tasks_completed_today()

    # Build energy-appropriate suggestions
    if reported_energy == "low":
        suggestion = (
            "Low energy acknowledged. Let's find something small and easy — "
            "a 5-minute task, or just process your inbox. No heroics needed."
        )
        tasks = db.list_tasks(status="next", energy_level="low", limit=3)
    elif reported_energy == "high":
        suggestion = (
            "High energy — great. Let's tackle something meaningful while "
            "the fuel tank is full. What's the hardest thing on your plate?"
        )
        tasks = db.list_tasks(status="next", energy_level="high", limit=3)
    else:
        suggestion = "How's your energy right now? Low, medium, or high?"
        tasks = db.get_next_actions(limit=3)

    return {
        "reported_energy": reported_energy or "not_specified",
        "suggestion": suggestion,
        "completed_today": completed_today,
        "matching_tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "energy": t.get("energy_level"),
                "estimated_minutes": t.get("estimated_minutes"),
            }
            for t in tasks
        ],
        "status_overview": status_counts,
    }


def capture_and_extract(
    db: CoachDB,
    raw_text: str,
    source: str = "voice",
) -> dict:
    """
    Accept a raw brain dump and store it as a capture.
    Extraction into tasks happens via the LLM layer, not here.
    Implements: GTD capture — get it out of your head NOW.
    """
    capture_id = db.add_capture(raw_text, source)
    total_unprocessed = len(db.get_unprocessed_captures())

    return {
        "capture_id": capture_id,
        "raw_text": raw_text,
        "source": source,
        "message": "Captured. It's out of your head and in the system.",
        "unprocessed_count": total_unprocessed,
    }


def log_weekly_review(db: CoachDB, notes: str = "") -> dict:
    """
    Record that a weekly review was performed.
    Implements: GTD weekly review tracking.
    """
    status_counts = db.count_tasks_by_status()
    week_start_dt = datetime.strptime(_start_of_week(), "%Y-%m-%d")
    completed = db.tasks_completed_since(week_start_dt)

    review_id = db.log_weekly_review(
        inbox_cleared=status_counts.get("inbox", 0) == 0,
        projects_reviewed=True,
        tasks_completed=completed,
        tasks_added=0,
        tasks_dropped=status_counts.get("dropped", 0),
        notes=notes,
    )

    return {
        "review_id": review_id,
        "review_date": _today(),
        "completed_this_week": completed,
        "status_snapshot": status_counts,
        "notes": notes,
        "message": "Weekly review logged. System is up to date.",
    }


# ---------------------------------------------------------------------------
# Tool dispatch map — used by intent_router
# ---------------------------------------------------------------------------

ANALYZER_FUNCTIONS = {
    "whats_next": whats_next,
    "plan_day": plan_day,
    "daily_progress": daily_progress,
    "inbox_review": inbox_review,
    "weekly_summary": weekly_summary,
    "project_status": project_status,
    "task_detail": task_detail,
    "energy_check": energy_check,
    "capture_and_extract": capture_and_extract,
    "log_weekly_review": log_weekly_review,
}
