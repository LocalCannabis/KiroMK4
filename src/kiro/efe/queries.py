"""
Query Handlers

Process queries about tasks, reminders, and projects.
Returns natural language responses for TTS.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Sequence

from kiro.efe.models import Task, Reminder, Project, TaskStatus
from kiro.utils.logging import get_logger

if TYPE_CHECKING:
    from kiro.efe.store import EFEStore

logger = get_logger(__name__)


class QueryHandler:
    """
    Handle queries about tasks, reminders, and projects.
    
    Returns natural language responses suitable for TTS.
    """

    def __init__(self, store: "EFEStore"):
        """
        Initialize the query handler.
        
        Args:
            store: The EFE database store
        """
        self.store = store

    def query_all_tasks(self) -> str:
        """Get a summary of all pending tasks."""
        tasks = self.store.get_pending_tasks()
        
        if not tasks:
            return "Your task list is empty. Nice work staying on top of things!"
        
        if len(tasks) == 1:
            return f"You have one task: {tasks[0].title}"
        
        # Build response
        response = f"You have {len(tasks)} tasks. "
        
        # List up to 5 tasks
        task_list = [t.title for t in tasks[:5]]
        response += self._format_list(task_list)
        
        if len(tasks) > 5:
            response += f" And {len(tasks) - 5} more."
        
        return response

    def query_by_context(self, context: str) -> str:
        """
        Find tasks that match a location or context.
        
        Searches task titles for mentions of the context.
        E.g., "Superstore" would match "Buy eggs at Superstore" or
        "Buy eggs the next time I go to Superstore"
        """
        tasks = self.store.get_pending_tasks()
        context_lower = context.lower()
        
        # Find tasks matching this context
        matching = [
            t for t in tasks
            if context_lower in t.title.lower()
        ]
        
        if not matching:
            return f"I don't have anything on your list for {context}."
        
        if len(matching) == 1:
            return f"Yes! You need to: {matching[0].title}"
        
        # Multiple matches
        response = f"You have {len(matching)} things for {context}. "
        task_list = [t.title for t in matching[:5]]
        response += self._format_list(task_list)
        
        if len(matching) > 5:
            response += f" And {len(matching) - 5} more."
        
        return response

    def query_today(self) -> str:
        """Get tasks and reminders for today."""
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        
        # Get today's tasks (tasks with due date today)
        all_tasks = self.store.get_pending_tasks()
        today_tasks = [
            t for t in all_tasks 
            if t.due_date and today_start <= t.due_date < today_end
        ]
        
        # Get pending reminders for today
        all_reminders = self.store.get_pending_reminders()
        today_reminders = [
            r for r in all_reminders
            if today_start <= r.trigger_time < today_end
        ]
        
        # No items today
        if not today_tasks and not today_reminders:
            if all_tasks:
                return f"Nothing scheduled for today specifically, but you have {len(all_tasks)} tasks on your list."
            return "You have nothing scheduled for today. Your day is clear!"
        
        parts = []
        
        # Tasks for today
        if today_tasks:
            if len(today_tasks) == 1:
                parts.append(f"one task due today: {today_tasks[0].title}")
            else:
                task_list = self._format_list([t.title for t in today_tasks[:3]])
                parts.append(f"{len(today_tasks)} tasks due today: {task_list}")
                if len(today_tasks) > 3:
                    parts[-1] += f" and {len(today_tasks) - 3} more"
        
        # Reminders for today
        if today_reminders:
            if len(today_reminders) == 1:
                r = today_reminders[0]
                time_str = r.trigger_time.strftime("%-I:%M %p")
                parts.append(f"one reminder at {time_str}: {r.message}")
            else:
                parts.append(f"{len(today_reminders)} reminders scheduled")
        
        # Combine
        response = "For today, you have "
        if len(parts) == 1:
            response += parts[0]
        else:
            response += parts[0] + ", and " + parts[1]
        
        return response + "."

    def query_project(self, project_name: str) -> str:
        """Get status of a specific project."""
        project = self.store.get_project_by_name(project_name)
        
        if not project:
            # Try partial match
            all_projects = self.store.get_all_projects()
            matches = [
                p for p in all_projects 
                if project_name.lower() in p.name.lower()
            ]
            
            if not matches:
                return f"I don't have a project called {project_name}."
            elif len(matches) == 1:
                project = matches[0]
            else:
                names = [p.name for p in matches]
                return f"I found multiple projects: {self._format_list(names)}. Which one?"
        
        # Build project status
        parts = [f"Project {project.name}"]
        
        if project.current_phase:
            parts.append(f"is in {project.current_phase} phase")
        
        if project.next_step:
            parts.append(f"Next step: {project.next_step}")
        
        # Count tasks
        tasks = self.store.get_all_tasks(project_id=project.id)
        pending = [t for t in tasks if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]
        completed = [t for t in tasks if t.status == TaskStatus.COMPLETED]
        
        if pending:
            parts.append(f"{len(pending)} tasks remaining")
        if completed:
            parts.append(f"{len(completed)} completed")
        
        return ". ".join(parts) + "."

    def query_reminders(self) -> str:
        """Get upcoming reminders."""
        reminders = self.store.get_pending_reminders()
        
        if not reminders:
            return "You have no upcoming reminders."
        
        if len(reminders) == 1:
            r = reminders[0]
            time_str = self._format_reminder_time(r.trigger_time)
            return f"You have one reminder {time_str}: {r.message}"
        
        response = f"You have {len(reminders)} upcoming reminders. "
        
        # List first 3
        for i, r in enumerate(reminders[:3]):
            time_str = self._format_reminder_time(r.trigger_time)
            response += f"{r.message} {time_str}. "
        
        if len(reminders) > 3:
            response += f"And {len(reminders) - 3} more."
        
        return response.strip()

    def confirm_task_created(self, task: Task) -> str:
        """Generate confirmation for a created task."""
        response = f"Got it. I've added '{task.title}' to your list"
        
        if task.due_date:
            due_str = self._format_due_date(task.due_date)
            response += f", due {due_str}"
        
        return response + "."

    def confirm_reminder_created(self, reminder: Reminder) -> str:
        """Generate confirmation for a created reminder."""
        time_str = self._format_reminder_time(reminder.trigger_time)
        return f"I'll remind you {time_str} to {reminder.message}."

    def confirm_task_completed(self, task: Task) -> str:
        """Generate confirmation for a completed task."""
        return f"Nice! I've marked '{task.title}' as done."

    def task_not_found(self, reference: str) -> str:
        """Response when task reference isn't found."""
        return f"I couldn't find a task matching '{reference}' on your list."

    def suggest_matching_tasks(self, reference: str, matches: Sequence[Task]) -> str:
        """Suggest matching tasks when reference is ambiguous."""
        if len(matches) == 1:
            return f"Did you mean '{matches[0].title}'?"
        
        titles = [t.title for t in matches[:3]]
        return f"Which one? I found: {self._format_list(titles)}"

    # =========================================================================
    # Formatting Helpers
    # =========================================================================

    def _format_list(self, items: list[str]) -> str:
        """Format a list with natural language conjunctions."""
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    def _format_reminder_time(self, dt: datetime) -> str:
        """Format reminder time naturally."""
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        
        time_str = dt.strftime("%-I:%M %p").lower()
        
        if dt.date() == now.date():
            # Check if it's soon
            minutes_away = (dt - now).total_seconds() / 60
            if minutes_away < 60:
                return f"in about {int(minutes_away)} minutes"
            return f"today at {time_str}"
        elif dt.date() == tomorrow.date():
            return f"tomorrow at {time_str}"
        elif dt < today + timedelta(days=7):
            day_name = dt.strftime("%A")
            return f"on {day_name} at {time_str}"
        else:
            date_str = dt.strftime("%B %-d")
            return f"on {date_str} at {time_str}"

    def _format_due_date(self, dt: datetime) -> str:
        """Format due date naturally."""
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        
        if dt.date() == now.date():
            return "today"
        elif dt.date() == tomorrow.date():
            return "tomorrow"
        elif dt < today + timedelta(days=7):
            return dt.strftime("%A")
        else:
            return dt.strftime("%B %-d")
