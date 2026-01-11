"""
EFE Data Models

SQLAlchemy models for tasks, reminders, projects, and raw captures.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class TaskStatus(enum.Enum):
    """Task lifecycle status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DEFERRED = "deferred"


class TaskPriority(enum.Enum):
    """Task priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ReminderStatus(enum.Enum):
    """Reminder status."""
    PENDING = "pending"
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    SNOOZED = "snoozed"
    CANCELLED = "cancelled"


class RecurrenceType(enum.Enum):
    """Recurrence patterns for reminders."""
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class Project(Base):
    """
    A project or area of focus.
    
    Projects group related tasks and provide context for conversations.
    """
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    current_phase: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    next_step: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="project")

    def __repr__(self) -> str:
        return f"<Project {self.name!r}>"


class Task(Base):
    """
    A task or todo item.
    
    Tasks can be standalone or belong to a project.
    """
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.PENDING
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority), default=TaskPriority.NORMAL
    )
    
    # Timing
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Organization
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=True
    )
    context_tags: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )  # Comma-separated: "@home,@errands"
    
    # Source tracking
    source_utterance: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capture_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("captures.id"), nullable=True
    )
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="tasks")
    capture: Mapped[Optional["Capture"]] = relationship("Capture", back_populates="task")

    def __repr__(self) -> str:
        return f"<Task {self.title!r} [{self.status.value}]>"

    @property
    def is_overdue(self) -> bool:
        """Check if task is past due date."""
        if self.due_date and self.status == TaskStatus.PENDING:
            return datetime.utcnow() > self.due_date
        return False


class Reminder(Base):
    """
    A time-triggered reminder.
    
    Reminders fire at a specific time and can optionally recur.
    """
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus), default=ReminderStatus.PENDING
    )
    
    # Timing
    trigger_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Recurrence
    recurrence: Mapped[RecurrenceType] = mapped_column(
        Enum(RecurrenceType), default=RecurrenceType.NONE
    )
    recurrence_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Snooze tracking
    snooze_count: Mapped[int] = mapped_column(Integer, default=0)
    snoozed_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Source tracking
    source_utterance: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capture_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("captures.id"), nullable=True
    )
    
    # Optional task association
    task_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=True
    )
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    capture: Mapped[Optional["Capture"]] = relationship("Capture", back_populates="reminder")
    task: Mapped[Optional["Task"]] = relationship("Task")

    def __repr__(self) -> str:
        return f"<Reminder {self.message[:30]!r} at {self.trigger_time}>"

    @property
    def is_due(self) -> bool:
        """Check if reminder should fire now."""
        if self.status != ReminderStatus.PENDING:
            return False
        if self.snoozed_until and datetime.utcnow() < self.snoozed_until:
            return False
        return datetime.utcnow() >= self.trigger_time


class Capture(Base):
    """
    Raw captured utterance before processing.
    
    Captures store the original speech for debugging and reprocessing.
    """
    __tablename__ = "captures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Processing status
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # What was created from this capture
    converted_to: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # "task", "reminder", "both", "none"
    
    # Intent detection
    detected_intent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(nullable=True)
    
    # Extracted entities (JSON string)
    entities_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    task: Mapped[Optional["Task"]] = relationship(
        "Task", back_populates="capture", uselist=False
    )
    reminder: Mapped[Optional["Reminder"]] = relationship(
        "Reminder", back_populates="capture", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Capture {self.raw_text[:30]!r}>"
