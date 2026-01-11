"""
EFE Database Store

CRUD operations for tasks, reminders, projects, and captures.
Uses SQLAlchemy async sessions for non-blocking database access.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from kiro.efe.models import (
    Base,
    Capture,
    Project,
    Reminder,
    ReminderStatus,
    Task,
    TaskPriority,
    TaskStatus,
)


class EFEStore:
    """
    Database store for Executive Function Engine.
    
    Provides CRUD operations for tasks, reminders, projects, and captures.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the store.
        
        Args:
            db_path: Path to SQLite database. Defaults to ~/.kiro/efe.db
        """
        if db_path is None:
            db_dir = Path.home() / ".kiro"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "efe.db")
        
        self.db_path = db_path
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # Create tables if they don't exist
        Base.metadata.create_all(self.engine)

    def _get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()

    # =========================================================================
    # Task Operations
    # =========================================================================

    def create_task(
        self,
        title: str,
        description: Optional[str] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        due_date: Optional[datetime] = None,
        project_id: Optional[str] = None,
        context_tags: Optional[str] = None,
        source_utterance: Optional[str] = None,
        capture_id: Optional[str] = None,
    ) -> Task:
        """Create a new task."""
        with self._get_session() as session:
            task = Task(
                title=title,
                description=description,
                priority=priority,
                due_date=due_date,
                project_id=project_id,
                context_tags=context_tags,
                source_utterance=source_utterance,
                capture_id=capture_id,
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        with self._get_session() as session:
            return session.get(Task, task_id)

    def get_all_tasks(
        self,
        status: Optional[TaskStatus] = None,
        project_id: Optional[str] = None,
        include_completed: bool = False,
    ) -> Sequence[Task]:
        """Get all tasks, optionally filtered."""
        with self._get_session() as session:
            query = select(Task)
            
            if status:
                query = query.where(Task.status == status)
            elif not include_completed:
                query = query.where(
                    Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
                )
            
            if project_id:
                query = query.where(Task.project_id == project_id)
            
            query = query.order_by(Task.created_at.desc())
            return session.scalars(query).all()

    def get_pending_tasks(self) -> Sequence[Task]:
        """Get all pending (not completed/cancelled) tasks."""
        return self.get_all_tasks(include_completed=False)

    def complete_task(self, task_id: str) -> Optional[Task]:
        """Mark a task as completed."""
        with self._get_session() as session:
            task = session.get(Task, task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now()
                session.commit()
                session.refresh(task)
            return task

    def update_task_status(self, task_id: str, status: TaskStatus) -> Optional[Task]:
        """Update task status."""
        with self._get_session() as session:
            task = session.get(Task, task_id)
            if task:
                task.status = status
                if status == TaskStatus.COMPLETED:
                    task.completed_at = datetime.utcnow()
                session.commit()
                session.refresh(task)
            return task

    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        with self._get_session() as session:
            task = session.get(Task, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    # =========================================================================
    # Reminder Operations
    # =========================================================================

    def create_reminder(
        self,
        message: str,
        trigger_time: datetime,
        recurrence: str = "none",
        source_utterance: Optional[str] = None,
        capture_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Reminder:
        """Create a new reminder."""
        from kiro.efe.models import RecurrenceType
        
        with self._get_session() as session:
            reminder = Reminder(
                message=message,
                trigger_time=trigger_time,
                recurrence=RecurrenceType(recurrence),
                source_utterance=source_utterance,
                capture_id=capture_id,
                task_id=task_id,
            )
            session.add(reminder)
            session.commit()
            session.refresh(reminder)
            return reminder

    def get_reminder(self, reminder_id: str) -> Optional[Reminder]:
        """Get a reminder by ID."""
        with self._get_session() as session:
            return session.get(Reminder, reminder_id)

    def get_pending_reminders(self) -> Sequence[Reminder]:
        """Get all pending reminders."""
        with self._get_session() as session:
            query = select(Reminder).where(
                Reminder.status == ReminderStatus.PENDING
            ).order_by(Reminder.trigger_time)
            return session.scalars(query).all()

    def get_due_reminders(self) -> Sequence[Reminder]:
        """Get reminders that should fire now."""
        now = datetime.now()
        with self._get_session() as session:
            query = select(Reminder).where(
                Reminder.status == ReminderStatus.PENDING,
                Reminder.trigger_time <= now,
            )
            # Filter snoozed reminders
            results = []
            for reminder in session.scalars(query).all():
                if reminder.snoozed_until is None or now >= reminder.snoozed_until:
                    results.append(reminder)
            return results

    def trigger_reminder(self, reminder_id: str) -> Optional[Reminder]:
        """Mark a reminder as triggered."""
        with self._get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                reminder.status = ReminderStatus.TRIGGERED
                reminder.triggered_at = datetime.now()
                session.commit()
                session.refresh(reminder)
            return reminder

    def acknowledge_reminder(self, reminder_id: str) -> Optional[Reminder]:
        """Mark a reminder as acknowledged."""
        with self._get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                reminder.status = ReminderStatus.ACKNOWLEDGED
                reminder.acknowledged_at = datetime.now()
                session.commit()
                session.refresh(reminder)
            return reminder

    def snooze_reminder(
        self, reminder_id: str, minutes: int = 10
    ) -> Optional[Reminder]:
        """Snooze a reminder for N minutes."""
        with self._get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                reminder.status = ReminderStatus.SNOOZED
                reminder.snoozed_until = datetime.now() + timedelta(minutes=minutes)
                reminder.snooze_count += 1
                session.commit()
                session.refresh(reminder)
            return reminder

    def unsnooze_reminder(self, reminder_id: str) -> Optional[Reminder]:
        """Reset a snoozed reminder to pending."""
        with self._get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder and reminder.status == ReminderStatus.SNOOZED:
                reminder.status = ReminderStatus.PENDING
                reminder.snoozed_until = None
                session.commit()
                session.refresh(reminder)
            return reminder

    def delete_reminder(self, reminder_id: str) -> bool:
        """Delete a reminder."""
        with self._get_session() as session:
            reminder = session.get(Reminder, reminder_id)
            if reminder:
                session.delete(reminder)
                session.commit()
                return True
            return False

    # =========================================================================
    # Project Operations
    # =========================================================================

    def create_project(
        self,
        name: str,
        description: Optional[str] = None,
    ) -> Project:
        """Create a new project."""
        with self._get_session() as session:
            project = Project(name=name, description=description)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project

    def get_project(self, project_id: str) -> Optional[Project]:
        """Get a project by ID."""
        with self._get_session() as session:
            return session.get(Project, project_id)

    def get_project_by_name(self, name: str) -> Optional[Project]:
        """Get a project by name (case-insensitive)."""
        with self._get_session() as session:
            query = select(Project).where(Project.name.ilike(name))
            return session.scalars(query).first()

    def get_all_projects(self, include_inactive: bool = False) -> Sequence[Project]:
        """Get all projects."""
        with self._get_session() as session:
            query = select(Project)
            if not include_inactive:
                query = query.where(Project.status == "active")
            query = query.order_by(Project.name)
            return session.scalars(query).all()

    def update_project_phase(
        self, project_id: str, phase: str, next_step: Optional[str] = None
    ) -> Optional[Project]:
        """Update project phase and optionally next step."""
        with self._get_session() as session:
            project = session.get(Project, project_id)
            if project:
                project.current_phase = phase
                if next_step:
                    project.next_step = next_step
                session.commit()
                session.refresh(project)
            return project

    # =========================================================================
    # Capture Operations
    # =========================================================================

    def create_capture(
        self,
        raw_text: str,
        detected_intent: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Capture:
        """Create a new capture."""
        with self._get_session() as session:
            capture = Capture(
                raw_text=raw_text,
                detected_intent=detected_intent,
                confidence=confidence,
            )
            session.add(capture)
            session.commit()
            session.refresh(capture)
            return capture

    def mark_capture_processed(
        self,
        capture_id: str,
        converted_to: Optional[str] = None,
        entities_json: Optional[str] = None,
    ) -> Optional[Capture]:
        """Mark a capture as processed."""
        with self._get_session() as session:
            capture = session.get(Capture, capture_id)
            if capture:
                capture.processed = True
                capture.processed_at = datetime.now()
                capture.converted_to = converted_to
                if entities_json:
                    capture.entities_json = entities_json
                session.commit()
                session.refresh(capture)
            return capture

    def get_unprocessed_captures(self) -> Sequence[Capture]:
        """Get all unprocessed captures."""
        with self._get_session() as session:
            query = select(Capture).where(
                Capture.processed == False
            ).order_by(Capture.timestamp)
            return session.scalars(query).all()
