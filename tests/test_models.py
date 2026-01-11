"""
Tests for Kiro database models.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from kiro.models import (
    Base,
    Task,
    Project,
    Reminder,
    Commitment,
    Capture,
    Measurement,
    Episode,
    Fact,
    init_database,
    close_database,
    get_session,
)
from sqlalchemy import select


@pytest.fixture
async def db():
    """Set up in-memory SQLite database for testing."""
    await init_database("sqlite+aiosqlite:///:memory:", echo=False)
    yield
    await close_database()


class TestTaskModel:
    """Tests for Task model."""

    @pytest.mark.asyncio
    async def test_create_task(self, db):
        async with get_session() as session:
            task = Task(title="Buy groceries", priority=2)
            session.add(task)
            await session.commit()

            result = await session.execute(select(Task))
            tasks = result.scalars().all()

            assert len(tasks) == 1
            assert tasks[0].title == "Buy groceries"
            assert tasks[0].priority == 2
            assert tasks[0].status == "pending"
            assert tasks[0].id is not None

    @pytest.mark.asyncio
    async def test_task_with_deadline(self, db):
        deadline = datetime.now() + timedelta(days=7)
        async with get_session() as session:
            task = Task(title="Submit report", deadline=deadline)
            session.add(task)
            await session.commit()

            result = await session.execute(select(Task))
            task = result.scalar_one()
            assert task.deadline is not None


class TestProjectModel:
    """Tests for Project model."""

    @pytest.mark.asyncio
    async def test_create_project(self, db):
        async with get_session() as session:
            project = Project(name="Home Renovation", goal="Finish by summer")
            session.add(project)
            await session.commit()

            result = await session.execute(select(Project))
            proj = result.scalar_one()
            assert proj.name == "Home Renovation"
            assert proj.status == "active"

    @pytest.mark.asyncio
    async def test_project_with_tasks(self, db):
        async with get_session() as session:
            project = Project(name="Test Project")
            task1 = Task(title="Task 1", project=project)
            task2 = Task(title="Task 2", project=project)
            session.add_all([project, task1, task2])
            await session.commit()

            result = await session.execute(select(Project))
            proj = result.scalar_one()
            # Access tasks through relationship
            tasks = await proj.awaitable_attrs.tasks
            assert len(tasks) == 2


class TestReminderModel:
    """Tests for Reminder model."""

    @pytest.mark.asyncio
    async def test_create_reminder(self, db):
        remind_at = datetime.now() + timedelta(hours=2)
        async with get_session() as session:
            reminder = Reminder(message="Call mom", remind_at=remind_at)
            session.add(reminder)
            await session.commit()

            result = await session.execute(select(Reminder))
            rem = result.scalar_one()
            assert rem.message == "Call mom"
            assert rem.acknowledged is False

    @pytest.mark.asyncio
    async def test_recurring_reminder(self, db):
        async with get_session() as session:
            reminder = Reminder(
                message="Take medication",
                remind_at=datetime.now(),
                recurring="daily",
            )
            session.add(reminder)
            await session.commit()

            result = await session.execute(select(Reminder))
            rem = result.scalar_one()
            assert rem.recurring == "daily"


class TestCommitmentModel:
    """Tests for Commitment model."""

    @pytest.mark.asyncio
    async def test_create_commitment(self, db):
        async with get_session() as session:
            commitment = Commitment(
                description="Review code by Friday",
                person="Alice",
                deadline=datetime.now() + timedelta(days=3),
            )
            session.add(commitment)
            await session.commit()

            result = await session.execute(select(Commitment))
            com = result.scalar_one()
            assert com.person == "Alice"
            assert com.status == "active"


class TestCaptureModel:
    """Tests for Capture model."""

    @pytest.mark.asyncio
    async def test_create_capture(self, db):
        async with get_session() as session:
            capture = Capture(content="Random idea about the project")
            session.add(capture)
            await session.commit()

            result = await session.execute(select(Capture))
            cap = result.scalar_one()
            assert cap.source == "voice"
            assert cap.processed is False


class TestMeasurementModel:
    """Tests for Measurement model."""

    @pytest.mark.asyncio
    async def test_create_measurement(self, db):
        async with get_session() as session:
            measurement = Measurement(
                metric_name="weight",
                value=175.5,
                unit="lbs",
            )
            session.add(measurement)
            await session.commit()

            result = await session.execute(select(Measurement))
            meas = result.scalar_one()
            assert meas.metric_name == "weight"
            assert meas.value == 175.5


class TestMemoryModels:
    """Tests for memory system models."""

    @pytest.mark.asyncio
    async def test_create_episode(self, db):
        async with get_session() as session:
            episode = Episode(
                summary="User asked about weather",
                intent="query.weather",
            )
            session.add(episode)
            await session.commit()

            result = await session.execute(select(Episode))
            ep = result.scalar_one()
            assert ep.intent == "query.weather"

    @pytest.mark.asyncio
    async def test_create_fact(self, db):
        async with get_session() as session:
            fact = Fact(
                category="preferences",
                key="coffee_preference",
                value="black, no sugar",
                confidence=0.9,
            )
            session.add(fact)
            await session.commit()

            result = await session.execute(select(Fact))
            f = result.scalar_one()
            assert f.category == "preferences"
            assert f.confidence == 0.9
