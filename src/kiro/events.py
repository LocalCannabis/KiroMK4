"""
Kiro Event Bus

A simple asyncio-based publish/subscribe event system for inter-component communication.

Usage:
    from kiro.events import EventBus, Event

    bus = EventBus()

    async def handler(event: Event):
        print(f"Received: {event.name} with {event.payload}")

    bus.subscribe("task.created", handler)
    await bus.emit("task.created", {"task_id": "123", "title": "Buy milk"})
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Event:
    """An event that can be emitted and handled."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def __str__(self) -> str:
        return f"Event({self.name}, id={self.event_id[:8]})"


# Type alias for event handlers
EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """
    Async event bus for pub/sub messaging.

    Features:
    - Multiple handlers per event
    - Wildcard subscriptions (e.g., "task.*")
    - Timeout protection for handlers
    - Error isolation (one handler failure doesn't affect others)
    """

    def __init__(
        self,
        max_queue_size: int = 1000,
        handler_timeout: float = 30.0,
    ):
        self._handlers: dict[str, list[EventHandler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self._handler_timeout = handler_timeout
        self._running = False
        self._processor_task: asyncio.Task[None] | None = None

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """
        Subscribe a handler to an event.

        Args:
            event_name: Event name to subscribe to. Use "*" suffix for wildcards.
            handler: Async function that receives Event objects.
        """
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler)
        logger.debug("handler_subscribed", event_name=event_name, handler=handler.__name__)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> bool:
        """
        Unsubscribe a handler from an event.

        Returns True if handler was found and removed.
        """
        if event_name not in self._handlers:
            return False
        try:
            self._handlers[event_name].remove(handler)
            logger.debug("handler_unsubscribed", event_name=event_name, handler=handler.__name__)
            return True
        except ValueError:
            return False

    def _get_handlers(self, event_name: str) -> list[EventHandler]:
        """Get all handlers that match an event name, including wildcards."""
        handlers: list[EventHandler] = []

        # Exact match
        if event_name in self._handlers:
            handlers.extend(self._handlers[event_name])

        # Wildcard matches (e.g., "task.*" matches "task.created")
        parts = event_name.split(".")
        for i in range(len(parts)):
            wildcard = ".".join(parts[: i + 1]) + ".*"
            if wildcard in self._handlers:
                handlers.extend(self._handlers[wildcard])

        # Global wildcard
        if "*" in self._handlers:
            handlers.extend(self._handlers["*"])

        return handlers

    async def emit(self, event_name: str, payload: dict[str, Any] | None = None) -> Event:
        """
        Emit an event to all subscribed handlers.

        In queue mode (started), adds to queue for async processing.
        Otherwise, processes immediately.

        Returns the Event object that was emitted.
        """
        ev = Event(name=event_name, payload=payload or {})
        logger.debug("event_emitted", event_obj=str(ev), payload_keys=list(ev.payload.keys()))

        if self._running:
            await self._queue.put(ev)
        else:
            # Direct processing when not in queue mode
            await self._process_event(ev)

        return ev

    def emit_sync(self, event_name: str, payload: dict[str, Any] | None = None) -> Event:
        """
        Synchronously emit an event (fire-and-forget).

        Useful for emitting from sync code. Event will be processed
        when the event loop runs.
        """
        ev = Event(name=event_name, payload=payload or {})

        if self._running:
            try:
                self._queue.put_nowait(ev)
            except asyncio.QueueFull:
                logger.warning("event_queue_full", event_obj=str(ev))
        else:
            logger.warning("event_bus_not_running", event_obj=str(ev))

        return ev

    async def _process_event(self, ev: Event) -> None:
        """Process a single event by calling all matched handlers."""
        handlers = self._get_handlers(ev.name)

        if not handlers:
            logger.debug("no_handlers", event_obj=str(ev))
            return

        for handler in handlers:
            try:
                await asyncio.wait_for(
                    handler(ev),
                    timeout=self._handler_timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "handler_timeout",
                    event_obj=str(ev),
                    handler=handler.__name__,
                    timeout=self._handler_timeout,
                )
            except Exception as e:
                logger.error(
                    "handler_error",
                    event_obj=str(ev),
                    handler=handler.__name__,
                    error=str(e),
                    exc_info=True,
                )

    async def _process_queue(self) -> None:
        """Background task that processes events from the queue."""
        logger.info("event_processor_started")

        while self._running:
            try:
                # Wait for event with timeout to allow checking _running
                ev = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._process_event(ev)
                self._queue.task_done()
            except asyncio.TimeoutError:
                # Just a check interval, continue
                continue
            except asyncio.CancelledError:
                logger.info("event_processor_cancelled")
                break
            except Exception as e:
                logger.error("event_processor_error", error=str(e), exc_info=True)

        logger.info("event_processor_stopped")

    async def start(self) -> None:
        """Start the event bus processor."""
        if self._running:
            return

        self._running = True
        self._processor_task = asyncio.create_task(self._process_queue())
        logger.info("event_bus_started")

    async def stop(self) -> None:
        """Stop the event bus and process remaining events."""
        if not self._running:
            return

        self._running = False

        # Wait for queue to drain (with timeout)
        if not self._queue.empty():
            logger.info("draining_event_queue", remaining=self._queue.qsize())
            try:
                await asyncio.wait_for(self._queue.join(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("event_queue_drain_timeout")

        # Cancel processor task
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
            self._processor_task = None

        logger.info("event_bus_stopped")

    @property
    def is_running(self) -> bool:
        """Check if the event bus is running."""
        return self._running

    @property
    def queue_size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()


# Global event bus instance (lazy-initialized)
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        from kiro.config import get_config

        config = get_config()
        _event_bus = EventBus(
            max_queue_size=config.events.max_queue_size,
            handler_timeout=config.events.handler_timeout,
        )
    return _event_bus


def reset_event_bus() -> None:
    """Reset the global event bus (useful for testing)."""
    global _event_bus
    _event_bus = None
