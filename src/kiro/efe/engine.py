"""
Executive Function Engine

Main coordinator for ADHD support features: task capture, reminders,
and project tracking.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, Optional, Awaitable

from kiro.efe.capture import CapturePipeline, CaptureIntent, ParsedCapture
from kiro.efe.models import Reminder, Task, TaskStatus
from kiro.efe.queries import QueryHandler
from kiro.efe.scheduler import ReminderScheduler
from kiro.efe.store import EFEStore
from kiro.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutiveFunctionEngine:
    """
    Executive Function Engine (EFE).
    
    Provides ADHD support through:
    - Task capture and tracking
    - Reminder management
    - Project status tracking
    - Query handling for voice interface
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        on_speak: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """
        Initialize the EFE.
        
        Args:
            db_path: Path to SQLite database (default: ~/.kiro/efe.db)
            on_speak: Async callback to speak responses via TTS
        """
        self.store = EFEStore(db_path=db_path)
        self.capture = CapturePipeline()
        self.queries = QueryHandler(self.store)
        self.scheduler = ReminderScheduler(
            self.store,
            on_reminder=self._on_reminder_triggered,
        )
        
        self._on_speak = on_speak
        self._running = False

    async def start(self) -> None:
        """Start the EFE (including reminder scheduler)."""
        if self._running:
            return
        
        await self.scheduler.start()
        self._running = True
        logger.info("Executive Function Engine started")

    async def stop(self) -> None:
        """Stop the EFE."""
        await self.scheduler.stop()
        self._running = False
        logger.info("Executive Function Engine stopped")

    async def process(self, text: str) -> Optional[str]:
        """
        Process an utterance and return a response.
        
        This is the main entry point for the voice pipeline.
        
        Args:
            text: Transcribed speech
            
        Returns:
            Response text for TTS, or None if not an EFE intent
        """
        # Parse the utterance
        parsed = self.capture.parse(text)
        
        if parsed.intent == CaptureIntent.UNKNOWN:
            return None  # Not an EFE intent, let LLM handle it
        
        logger.info(f"EFE intent: {parsed.intent.value} ({parsed.confidence:.0%})")
        
        # Handle based on intent
        if parsed.intent == CaptureIntent.TASK:
            return await self._handle_task(parsed, text)
        
        elif parsed.intent == CaptureIntent.REMINDER:
            return await self._handle_reminder(parsed, text)
        
        elif parsed.intent == CaptureIntent.QUERY_TASKS:
            return self.queries.query_all_tasks()
        
        elif parsed.intent == CaptureIntent.QUERY_TODAY:
            return self.queries.query_today()
        
        elif parsed.intent == CaptureIntent.QUERY_PROJECT:
            if parsed.project_name:
                return self.queries.query_project(parsed.project_name)
            return "Which project would you like to know about?"
        
        elif parsed.intent == CaptureIntent.COMPLETE_TASK:
            return await self._handle_completion(parsed)
        
        return None

    def is_efe_intent(self, text: str) -> bool:
        """
        Quick check if text contains an EFE intent.
        
        Use this for routing decisions before full processing.
        """
        parsed = self.capture.parse(text)
        return parsed.intent != CaptureIntent.UNKNOWN

    async def _handle_task(self, parsed: ParsedCapture, raw_text: str) -> str:
        """Handle task creation intent."""
        if not parsed.task_title:
            return "I heard you want to add a task, but I didn't catch what it was."
        
        # Create capture record first
        capture = self.store.create_capture(
            raw_text=raw_text,
            detected_intent="task",
            confidence=parsed.confidence,
        )
        
        # Create the task
        task = self.store.create_task(
            title=parsed.task_title,
            source_utterance=raw_text,
            capture_id=capture.id,
        )
        
        # Mark capture as processed
        self.store.mark_capture_processed(
            capture.id, converted_to="task"
        )
        
        logger.info(f"Created task: {task.title} (id={task.id})")
        return self.queries.confirm_task_created(task)

    async def _handle_reminder(self, parsed: ParsedCapture, raw_text: str) -> str:
        """Handle reminder creation intent."""
        if not parsed.reminder_message:
            return "I heard you want a reminder, but I didn't catch what for."
        
        trigger_time = parsed.trigger_time
        if not trigger_time:
            # Default to 1 hour from now if no time specified
            from datetime import timedelta
            trigger_time = datetime.now() + timedelta(hours=1)
            logger.debug("No time specified, defaulting to 1 hour")
        
        # Create capture record
        capture = self.store.create_capture(
            raw_text=raw_text,
            detected_intent="reminder",
            confidence=parsed.confidence,
        )
        
        # Create the reminder
        reminder = self.store.create_reminder(
            message=parsed.reminder_message,
            trigger_time=trigger_time,
            source_utterance=raw_text,
            capture_id=capture.id,
        )
        
        # Mark capture as processed
        self.store.mark_capture_processed(
            capture.id, converted_to="reminder"
        )
        
        logger.info(f"Created reminder: {reminder.message} at {trigger_time}")
        return self.queries.confirm_reminder_created(reminder)

    async def _handle_completion(self, parsed: ParsedCapture) -> str:
        """Handle task completion intent."""
        reference = parsed.task_reference
        if not reference:
            return "Which task did you complete?"
        
        # Search for matching task
        tasks = self.store.get_pending_tasks()
        
        # Normalize reference: remove common verb forms
        reference_normalized = self._normalize_task_reference(reference)
        
        # Exact match (normalized)
        for task in tasks:
            task_normalized = self._normalize_task_reference(task.title)
            if task_normalized == reference_normalized:
                self.store.complete_task(task.id)
                return self.queries.confirm_task_completed(task)
        
        # Partial match - check if key words overlap
        matches = []
        ref_words = set(reference_normalized.split())
        for task in tasks:
            task_words = set(self._normalize_task_reference(task.title).split())
            # Match if significant overlap or one contains the other
            if ref_words & task_words or reference_normalized in task.title.lower() or task.title.lower() in reference_normalized:
                matches.append(task)
        
        if len(matches) == 1:
            self.store.complete_task(matches[0].id)
            return self.queries.confirm_task_completed(matches[0])
        elif len(matches) > 1:
            return self.queries.suggest_matching_tasks(reference, matches)
        else:
            return self.queries.task_not_found(reference)
    
    def _normalize_task_reference(self, text: str) -> str:
        """Normalize task reference for matching."""
        import re
        text = text.lower().strip()
        # Remove common verb prefixes (buying -> buy, called -> call)
        text = re.sub(r'\b(buying|bought)\b', 'buy', text)
        text = re.sub(r'\b(calling|called)\b', 'call', text)
        text = re.sub(r'\b(getting|got)\b', 'get', text)
        text = re.sub(r'\b(making|made)\b', 'make', text)
        text = re.sub(r'\b(doing|did|done)\b', 'do', text)
        # Remove articles and common words
        text = re.sub(r'\b(the|a|an|my|some)\b', '', text)
        # Collapse whitespace
        text = ' '.join(text.split())
        return text

    async def _on_reminder_triggered(self, reminder: Reminder) -> None:
        """Callback when a reminder fires."""
        message = f"Hey! Just a reminder: {reminder.message}"
        logger.info(f"Reminder triggered: {message}")
        
        if self._on_speak:
            try:
                await self._on_speak(message)
            except Exception as e:
                logger.error(f"Failed to speak reminder: {e}")

    # =========================================================================
    # Direct API Methods (for debugging/testing)
    # =========================================================================

    def add_task(self, title: str, **kwargs) -> Task:
        """Directly add a task (bypassing voice parsing)."""
        return self.store.create_task(title=title, **kwargs)

    def add_reminder(
        self, message: str, trigger_time: datetime, **kwargs
    ) -> Reminder:
        """Directly add a reminder (bypassing voice parsing)."""
        return self.store.create_reminder(
            message=message, trigger_time=trigger_time, **kwargs
        )

    def list_tasks(self) -> list[Task]:
        """Get all pending tasks."""
        return list(self.store.get_pending_tasks())

    def list_reminders(self) -> list[Reminder]:
        """Get all pending reminders."""
        return list(self.store.get_pending_reminders())

    def complete_task(self, task_id: str) -> Optional[Task]:
        """Mark a task as complete by ID."""
        return self.store.complete_task(task_id)

    def acknowledge_reminder(self, reminder_id: str) -> Optional[Reminder]:
        """Acknowledge a triggered reminder."""
        return self.store.acknowledge_reminder(reminder_id)

    def snooze_reminder(self, reminder_id: str, minutes: int = 10) -> Optional[Reminder]:
        """Snooze a reminder."""
        return self.store.snooze_reminder(reminder_id, minutes)
