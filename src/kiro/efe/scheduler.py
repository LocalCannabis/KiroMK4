"""
Reminder Scheduler

Background scheduler that checks for due reminders and triggers them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Optional, Awaitable

from kiro.efe.models import Reminder, ReminderStatus, RecurrenceType
from kiro.utils.logging import get_logger

if TYPE_CHECKING:
    from kiro.efe.store import EFEStore

logger = get_logger(__name__)


class ReminderScheduler:
    """
    Background scheduler for reminder triggering.
    
    Periodically checks for due reminders and fires them via callback.
    """

    def __init__(
        self,
        store: "EFEStore",
        on_reminder: Optional[Callable[[Reminder], Awaitable[None]]] = None,
        check_interval: float = 30.0,
    ):
        """
        Initialize the scheduler.
        
        Args:
            store: The EFE database store
            on_reminder: Async callback when a reminder fires
            check_interval: Seconds between reminder checks
        """
        self.store = store
        self.on_reminder = on_reminder
        self.check_interval = check_interval
        
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the scheduler background task."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Reminder scheduler started (interval: {self.check_interval}s)")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Reminder scheduler stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_reminders()
            except Exception as e:
                logger.error(f"Error checking reminders: {e}")
            
            await asyncio.sleep(self.check_interval)

    async def _check_reminders(self) -> None:
        """Check for and fire due reminders."""
        due_reminders = self.store.get_due_reminders()
        
        for reminder in due_reminders:
            logger.info(f"Firing reminder: {reminder.message}")
            
            # Mark as triggered
            self.store.trigger_reminder(reminder.id)
            
            # Fire callback
            if self.on_reminder:
                try:
                    await self.on_reminder(reminder)
                except Exception as e:
                    logger.error(f"Error in reminder callback: {e}")
            
            # Handle recurrence
            if reminder.recurrence != RecurrenceType.NONE:
                await self._schedule_next_occurrence(reminder)

    async def _schedule_next_occurrence(self, reminder: Reminder) -> None:
        """Create next occurrence for recurring reminder."""
        next_time = self._calculate_next_time(
            reminder.trigger_time, reminder.recurrence
        )
        
        # Check if past recurrence end
        if reminder.recurrence_end and next_time > reminder.recurrence_end:
            logger.debug(f"Recurring reminder {reminder.id} past end date")
            return
        
        # Create new reminder
        self.store.create_reminder(
            message=reminder.message,
            trigger_time=next_time,
            recurrence=reminder.recurrence.value,
            source_utterance=reminder.source_utterance,
        )
        logger.info(f"Scheduled next occurrence at {next_time}")

    def _calculate_next_time(
        self, current_time: datetime, recurrence: RecurrenceType
    ) -> datetime:
        """Calculate next trigger time based on recurrence."""
        if recurrence == RecurrenceType.DAILY:
            return current_time + timedelta(days=1)
        elif recurrence == RecurrenceType.WEEKLY:
            return current_time + timedelta(weeks=1)
        elif recurrence == RecurrenceType.MONTHLY:
            # Add roughly a month (30 days)
            # For exact monthly, we'd need dateutil.relativedelta
            return current_time + timedelta(days=30)
        elif recurrence == RecurrenceType.YEARLY:
            return current_time + timedelta(days=365)
        else:
            return current_time

    async def check_now(self) -> int:
        """
        Manually check for due reminders now.
        
        Returns:
            Number of reminders fired
        """
        due = self.store.get_due_reminders()
        count = len(due)
        
        for reminder in due:
            logger.info(f"Manual trigger: {reminder.message}")
            self.store.trigger_reminder(reminder.id)
            
            if self.on_reminder:
                try:
                    await self.on_reminder(reminder)
                except Exception as e:
                    logger.error(f"Error in reminder callback: {e}")
        
        return count

    def get_next_reminder(self) -> Optional[Reminder]:
        """Get the next upcoming reminder."""
        reminders = self.store.get_pending_reminders()
        if reminders:
            return reminders[0]  # Already ordered by trigger_time
        return None

    def time_until_next(self) -> Optional[timedelta]:
        """Get time until next reminder."""
        next_reminder = self.get_next_reminder()
        if next_reminder:
            return next_reminder.trigger_time - datetime.now()
        return None
