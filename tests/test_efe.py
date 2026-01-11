"""
Tests for Executive Function Engine
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

import pytest

from kiro.efe import (
    ExecutiveFunctionEngine,
    EFEStore,
    CapturePipeline,
    CaptureIntent,
    parse_utterance,
    Task,
    TaskStatus,
    Reminder,
    ReminderStatus,
)


class TestCapturePipeline:
    """Tests for intent detection and parsing."""

    def test_task_intent_detection(self):
        """Test that task phrases are detected."""
        test_cases = [
            ("I need to buy milk", CaptureIntent.TASK),
            ("add groceries to my list", CaptureIntent.TASK),
            ("I have to call mom", CaptureIntent.TASK),
            ("don't forget to send the email", CaptureIntent.TASK),
        ]
        
        for text, expected_intent in test_cases:
            result = parse_utterance(text)
            assert result.intent == expected_intent, f"Failed for: {text}"
            assert result.task_title is not None

    def test_reminder_intent_detection(self):
        """Test that reminder phrases are detected."""
        test_cases = [
            "remind me to call mom",
            "remind me tomorrow to water plants",
            "set a reminder for the meeting",
            "alert me about the deadline",
        ]
        
        for text in test_cases:
            result = parse_utterance(text)
            assert result.intent == CaptureIntent.REMINDER, f"Failed for: {text}"
            assert result.reminder_message is not None

    def test_query_intent_detection(self):
        """Test that query phrases are detected."""
        test_cases = [
            ("what's on my list", CaptureIntent.QUERY_TASKS),
            ("show me my tasks", CaptureIntent.QUERY_TASKS),
            ("what do I need to do today", CaptureIntent.QUERY_TASKS),
            ("status of the project", CaptureIntent.QUERY_PROJECT),
        ]
        
        for text, expected_intent in test_cases:
            result = parse_utterance(text)
            assert result.intent == expected_intent, f"Failed for: {text}"

    def test_completion_intent_detection(self):
        """Test that completion phrases are detected."""
        test_cases = [
            "I finished buying groceries",
            "mark groceries as done",
            "check off the milk task",
        ]
        
        for text in test_cases:
            result = parse_utterance(text)
            assert result.intent == CaptureIntent.COMPLETE_TASK, f"Failed for: {text}"
            assert result.task_reference is not None

    def test_unknown_intent(self):
        """Test that non-EFE phrases return UNKNOWN."""
        test_cases = [
            "hello how are you",
            "tell me a joke",
            "what's the weather like",
            "play some music",
        ]
        
        for text in test_cases:
            result = parse_utterance(text)
            assert result.intent == CaptureIntent.UNKNOWN, f"Failed for: {text}"

    def test_time_parsing_relative(self):
        """Test relative time parsing."""
        now = datetime.now()
        
        result = parse_utterance("remind me in 30 minutes to check email")
        assert result.trigger_time is not None
        expected = now + timedelta(minutes=30)
        assert abs((result.trigger_time - expected).total_seconds()) < 5

    def test_time_parsing_tomorrow(self):
        """Test tomorrow time parsing."""
        result = parse_utterance("remind me tomorrow at 3pm to call mom")
        assert result.trigger_time is not None
        
        tomorrow = datetime.now() + timedelta(days=1)
        assert result.trigger_time.date() == tomorrow.date()
        assert result.trigger_time.hour == 15

    def test_time_parsing_tonight(self):
        """Test tonight time parsing."""
        result = parse_utterance("remind me tonight to lock up")
        assert result.trigger_time is not None
        assert result.trigger_time.hour == 20


class TestEFEStore:
    """Tests for database operations."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a temporary store for testing."""
        db_path = str(tmp_path / "test.db")
        return EFEStore(db_path=db_path)

    def test_create_task(self, store):
        """Test task creation."""
        task = store.create_task(title="Buy milk")
        assert task.id is not None
        assert task.title == "Buy milk"
        assert task.status == TaskStatus.PENDING

    def test_get_pending_tasks(self, store):
        """Test getting pending tasks."""
        store.create_task(title="Task 1")
        store.create_task(title="Task 2")
        
        tasks = store.get_pending_tasks()
        assert len(tasks) == 2

    def test_complete_task(self, store):
        """Test task completion."""
        task = store.create_task(title="Test task")
        completed = store.complete_task(task.id)
        
        assert completed.status == TaskStatus.COMPLETED
        assert completed.completed_at is not None
        
        # Should not appear in pending
        pending = store.get_pending_tasks()
        assert len(pending) == 0

    def test_create_reminder(self, store):
        """Test reminder creation."""
        trigger_time = datetime.now() + timedelta(hours=1)
        reminder = store.create_reminder(
            message="Call mom",
            trigger_time=trigger_time,
        )
        
        assert reminder.id is not None
        assert reminder.message == "Call mom"
        assert reminder.status == ReminderStatus.PENDING

    def test_get_due_reminders(self, store):
        """Test getting due reminders."""
        # Create a past reminder (should be due)
        past_time = datetime.now() - timedelta(hours=1)
        store.create_reminder(message="Past", trigger_time=past_time)
        
        # Create a future reminder (should not be due)
        future_time = datetime.now() + timedelta(hours=2)
        store.create_reminder(message="Future", trigger_time=future_time)
        
        due = store.get_due_reminders()
        assert len(due) == 1
        assert due[0].message == "Past"

    def test_acknowledge_reminder(self, store):
        """Test reminder acknowledgment."""
        trigger_time = datetime.now()
        reminder = store.create_reminder(message="Test", trigger_time=trigger_time)
        
        store.trigger_reminder(reminder.id)
        ack = store.acknowledge_reminder(reminder.id)
        
        assert ack.status == ReminderStatus.ACKNOWLEDGED
        assert ack.acknowledged_at is not None


class TestExecutiveFunctionEngine:
    """Integration tests for the full EFE."""

    @pytest.fixture
    def efe(self, tmp_path):
        """Create a temporary EFE for testing."""
        db_path = str(tmp_path / "test.db")
        return ExecutiveFunctionEngine(db_path=db_path)

    @pytest.mark.asyncio
    async def test_process_task(self, efe):
        """Test processing a task creation utterance."""
        await efe.start()
        try:
            response = await efe.process("I need to buy milk")
            assert response is not None
            assert "buy milk" in response.lower() or "Buy milk" in response
            
            tasks = efe.list_tasks()
            assert len(tasks) == 1
            assert "milk" in tasks[0].title.lower()
        finally:
            await efe.stop()

    @pytest.mark.asyncio
    async def test_process_reminder(self, efe):
        """Test processing a reminder creation utterance."""
        await efe.start()
        try:
            response = await efe.process("remind me tomorrow to call mom")
            assert response is not None
            assert "remind" in response.lower()
            
            reminders = efe.list_reminders()
            assert len(reminders) == 1
        finally:
            await efe.stop()

    @pytest.mark.asyncio
    async def test_process_query(self, efe):
        """Test processing a query utterance."""
        await efe.start()
        try:
            # Add a task first
            await efe.process("I need to buy groceries")
            
            # Query it
            response = await efe.process("what's on my list")
            assert response is not None
            assert "groceries" in response.lower()
        finally:
            await efe.stop()

    @pytest.mark.asyncio
    async def test_process_completion(self, efe):
        """Test processing a task completion utterance."""
        await efe.start()
        try:
            # Add a task
            await efe.process("I need to buy groceries")
            assert len(efe.list_tasks()) == 1
            
            # Complete it
            response = await efe.process("I finished buying groceries")
            assert response is not None
            assert "done" in response.lower() or "marked" in response.lower()
            
            # Should be empty now
            assert len(efe.list_tasks()) == 0
        finally:
            await efe.stop()

    @pytest.mark.asyncio
    async def test_unknown_intent_returns_none(self, efe):
        """Test that unknown intents return None for LLM fallback."""
        await efe.start()
        try:
            response = await efe.process("tell me a joke")
            assert response is None
            
            response = await efe.process("what's the weather")
            assert response is None
        finally:
            await efe.stop()

    @pytest.mark.asyncio
    async def test_is_efe_intent(self, efe):
        """Test the quick intent check method."""
        assert efe.is_efe_intent("remind me to call mom") is True
        assert efe.is_efe_intent("I need to buy milk") is True
        assert efe.is_efe_intent("what's on my list") is True
        assert efe.is_efe_intent("tell me a joke") is False
        assert efe.is_efe_intent("hello") is False
