"""
Intent Router

Classifies transcripts into categories and routes to handlers.
For Phase 2, we route everything to the LLM (smart routing comes later).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Awaitable

import structlog

logger = structlog.get_logger(__name__)


class IntentCategory(str, Enum):
    """Categories of user intent."""

    CONVERSATION = "conversation"  # General chat, questions
    COMMAND = "command"            # Direct actions (set timer, play music)
    CAPTURE = "capture"            # Task/commitment capture
    QUERY = "query"                # Information retrieval
    CONTROL = "control"            # System control (stop, cancel, mute)
    UNKNOWN = "unknown"            # Could not classify


@dataclass
class Intent:
    """A classified intent from user speech."""

    category: IntentCategory
    transcript: str
    confidence: float = 1.0
    entities: dict = field(default_factory=dict)
    raw_confidence: float | None = None  # From STT

    def __str__(self) -> str:
        return f"Intent({self.category.value}: {self.transcript[:50]}...)"


# Handler type: async function that takes Intent and returns response string
IntentHandler = Callable[[Intent], Awaitable[str]]


class IntentRouter:
    """
    Routes intents to appropriate handlers.

    For now, uses simple keyword matching for commands and control.
    Everything else goes to LLM for conversational response.
    """

    # Control phrases that should be handled immediately
    CONTROL_PATTERNS = [
        (r"\b(stop|cancel|shut up|be quiet|never ?mind)\b", "stop"),
        (r"\b(pause|wait|hold on)\b", "pause"),
        (r"\b(mute|unmute)\b", "mute"),
        (r"\b(louder|quieter|volume)\b", "volume"),
    ]

    # Command patterns for direct actions
    COMMAND_PATTERNS = [
        (r"\bset (?:a )?timer\b", "timer"),
        (r"\bset (?:an )?alarm\b", "alarm"),
        (r"\bplay\b.*\b(music|song|playlist)\b", "music"),
        (r"\bwhat(?:'s| is) the time\b", "time"),
        (r"\bwhat(?:'s| is) the date\b", "date"),
    ]

    # Capture patterns (task/commitment language)
    CAPTURE_PATTERNS = [
        (r"\bremind me\b", "reminder"),
        (r"\b(add|create) (?:a )?task\b", "task"),
        (r"\bi need to\b", "task"),
        (r"\bdon't let me forget\b", "reminder"),
        (r"\bi(?:'ll| will) (?:do|finish|complete)\b", "commitment"),
        (r"\bi promise\b", "commitment"),
    ]

    def __init__(self):
        """Initialize the router."""
        self._handlers: dict[IntentCategory, IntentHandler] = {}
        self._default_handler: IntentHandler | None = None
        self._running = False

        # Compile patterns
        self._control_re = [
            (re.compile(p, re.IGNORECASE), action)
            for p, action in self.CONTROL_PATTERNS
        ]
        self._command_re = [
            (re.compile(p, re.IGNORECASE), action)
            for p, action in self.COMMAND_PATTERNS
        ]
        self._capture_re = [
            (re.compile(p, re.IGNORECASE), action)
            for p, action in self.CAPTURE_PATTERNS
        ]

    async def start(self) -> None:
        """Start the router."""
        self._running = True
        logger.info("intent_router_started")

    async def stop(self) -> None:
        """Stop the router."""
        self._running = False
        logger.info("intent_router_stopped")

    def register_handler(
        self,
        category: IntentCategory,
        handler: IntentHandler,
    ) -> None:
        """Register a handler for an intent category."""
        self._handlers[category] = handler
        logger.debug("handler_registered", category=category.value)

    def set_default_handler(self, handler: IntentHandler) -> None:
        """Set the default handler for unregistered categories."""
        self._default_handler = handler

    def classify(self, transcript: str, stt_confidence: float | None = None) -> Intent:
        """
        Classify a transcript into an intent.

        Args:
            transcript: The transcribed speech
            stt_confidence: Confidence from STT (if available)

        Returns:
            Classified Intent
        """
        text = transcript.strip().lower()

        # Check control patterns first (highest priority)
        for pattern, action in self._control_re:
            if pattern.search(text):
                return Intent(
                    category=IntentCategory.CONTROL,
                    transcript=transcript,
                    confidence=0.9,
                    entities={"action": action},
                    raw_confidence=stt_confidence,
                )

        # Check command patterns
        for pattern, action in self._command_re:
            if pattern.search(text):
                return Intent(
                    category=IntentCategory.COMMAND,
                    transcript=transcript,
                    confidence=0.8,
                    entities={"action": action},
                    raw_confidence=stt_confidence,
                )

        # Check capture patterns
        for pattern, action in self._capture_re:
            if pattern.search(text):
                return Intent(
                    category=IntentCategory.CAPTURE,
                    transcript=transcript,
                    confidence=0.7,
                    entities={"capture_type": action},
                    raw_confidence=stt_confidence,
                )

        # Check if it's a question
        if text.endswith("?") or text.startswith(("what", "who", "where", "when", "why", "how")):
            return Intent(
                category=IntentCategory.QUERY,
                transcript=transcript,
                confidence=0.6,
                raw_confidence=stt_confidence,
            )

        # Default: conversation
        return Intent(
            category=IntentCategory.CONVERSATION,
            transcript=transcript,
            confidence=0.5,
            raw_confidence=stt_confidence,
        )

    async def route(self, intent: Intent) -> str:
        """
        Route an intent to its handler.

        Args:
            intent: The classified intent

        Returns:
            Response string from handler
        """
        if not self._running:
            return "I'm not ready yet."

        logger.info(
            "intent_routed",
            category=intent.category.value,
            confidence=round(intent.confidence, 2),
        )

        # Get handler
        handler = self._handlers.get(intent.category)
        if handler is None:
            handler = self._default_handler

        if handler is None:
            logger.warning("no_handler", category=intent.category.value)
            return "I'm not sure how to handle that."

        try:
            response = await handler(intent)
            return response
        except Exception as e:
            logger.error(
                "handler_error",
                category=intent.category.value,
                error=str(e),
                exc_info=True,
            )
            return "I had trouble processing that."

    async def classify_and_route(
        self,
        transcript: str,
        stt_confidence: float | None = None,
    ) -> str:
        """
        Classify transcript and route to handler.

        Convenience method combining classify() and route().
        """
        intent = self.classify(transcript, stt_confidence)
        return await self.route(intent)

    @property
    def is_running(self) -> bool:
        """Check if router is running."""
        return self._running
