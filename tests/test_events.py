"""
Tests for Kiro event bus.
"""

import asyncio

import pytest

from kiro.events import Event, EventBus, get_event_bus, reset_event_bus


class TestEvent:
    """Tests for Event dataclass."""

    def test_event_creation(self):
        event = Event(name="test.event", payload={"key": "value"})
        assert event.name == "test.event"
        assert event.payload == {"key": "value"}
        assert event.timestamp > 0
        assert len(event.event_id) == 36  # UUID length

    def test_event_str(self):
        event = Event(name="test.event")
        assert "test.event" in str(event)


class TestEventBus:
    """Tests for EventBus class."""

    @pytest.fixture
    def bus(self):
        return EventBus(max_queue_size=100, handler_timeout=5.0)

    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self, bus: EventBus):
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("test.event", handler)
        await bus.emit("test.event", {"data": 123})

        assert len(received) == 1
        assert received[0].name == "test.event"
        assert received[0].payload == {"data": 123}

    @pytest.mark.asyncio
    async def test_multiple_handlers(self, bus: EventBus):
        results = []

        async def handler1(event: Event):
            results.append("handler1")

        async def handler2(event: Event):
            results.append("handler2")

        bus.subscribe("test.event", handler1)
        bus.subscribe("test.event", handler2)
        await bus.emit("test.event")

        assert "handler1" in results
        assert "handler2" in results

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self, bus: EventBus):
        received = []

        async def handler(event: Event):
            received.append(event.name)

        bus.subscribe("task.*", handler)
        await bus.emit("task.created")
        await bus.emit("task.updated")
        await bus.emit("other.event")

        assert "task.created" in received
        assert "task.updated" in received
        assert "other.event" not in received

    @pytest.mark.asyncio
    async def test_global_wildcard(self, bus: EventBus):
        received = []

        async def handler(event: Event):
            received.append(event.name)

        bus.subscribe("*", handler)
        await bus.emit("any.event")
        await bus.emit("another.event")

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus: EventBus):
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe("test.event", handler)
        assert bus.unsubscribe("test.event", handler) is True
        await bus.emit("test.event")

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_not_found(self, bus: EventBus):
        async def handler(event: Event):
            pass

        assert bus.unsubscribe("nonexistent", handler) is False

    @pytest.mark.asyncio
    async def test_handler_error_isolation(self, bus: EventBus):
        results = []

        async def bad_handler(event: Event):
            raise ValueError("Handler error")

        async def good_handler(event: Event):
            results.append("success")

        bus.subscribe("test.event", bad_handler)
        bus.subscribe("test.event", good_handler)
        await bus.emit("test.event")

        # Good handler should still run despite bad handler error
        assert "success" in results

    @pytest.mark.asyncio
    async def test_handler_timeout(self, bus: EventBus):
        bus._handler_timeout = 0.1  # Very short timeout

        async def slow_handler(event: Event):
            await asyncio.sleep(10)  # Way longer than timeout

        results = []

        async def fast_handler(event: Event):
            results.append("fast")

        bus.subscribe("test.event", slow_handler)
        bus.subscribe("test.event", fast_handler)
        await bus.emit("test.event")

        # Fast handler should complete, slow one times out
        assert "fast" in results

    @pytest.mark.asyncio
    async def test_queue_mode(self, bus: EventBus):
        received = []

        async def handler(event: Event):
            received.append(event.name)

        bus.subscribe("test.event", handler)

        await bus.start()
        assert bus.is_running

        await bus.emit("test.event", {"seq": 1})
        await bus.emit("test.event", {"seq": 2})

        # Give queue time to process
        await asyncio.sleep(0.1)

        await bus.stop()
        assert not bus.is_running

        assert len(received) == 2


class TestEventBusGlobal:
    """Tests for global event bus instance."""

    def setup_method(self):
        reset_event_bus()

    def test_get_event_bus(self):
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_reset_event_bus(self):
        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()
        assert bus1 is not bus2
