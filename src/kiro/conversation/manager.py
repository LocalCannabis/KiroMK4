"""
Conversation Manager

Maintains conversation context and builds LLM prompts.
For Phase 2: In-memory context only (persistence comes with Memory system).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterator

import structlog

from kiro.llm.gateway import Message, Role, LLMGateway, LLMResponse
from kiro.intent.router import Intent, IntentCategory

logger = structlog.get_logger(__name__)


# Kiro's core system prompt
KIRO_SYSTEM_PROMPT = """You are Kiro, a voice-first AI assistant designed to be a supportive companion and executive function aid.

Core traits:
- Warm and encouraging, but not saccharine
- Concise and direct — you're voice-first, so brevity matters
- Proactive in offering help when you notice patterns
- Patient and non-judgmental, especially about forgotten tasks or missed commitments
- You remember conversation context and refer back to it naturally

Voice interaction guidelines:
- Keep responses short (1-3 sentences for simple queries)
- Avoid lists unless explicitly asked — they're hard to follow by voice
- Use natural speech patterns, not formal writing
- It's okay to ask clarifying questions
- Acknowledge emotions when relevant

You exist to help your user stay on track, remember commitments, and feel supported. You're not just an assistant — you're a thinking partner.

Current context: You are running on a Linux desktop. The user interacts with you via voice."""


@dataclass
class ConversationTurn:
    """A single turn in a conversation."""

    role: Role
    content: str
    timestamp: float = field(default_factory=time.time)
    intent: Intent | None = None

    def to_message(self) -> Message:
        """Convert to LLM Message."""
        return Message(role=self.role, content=self.content, timestamp=self.timestamp)


@dataclass
class Conversation:
    """An active conversation with context."""

    id: str
    turns: deque[ConversationTurn] = field(default_factory=lambda: deque(maxlen=20))
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def add_turn(self, role: Role, content: str, intent: Intent | None = None) -> None:
        """Add a turn to the conversation."""
        self.turns.append(ConversationTurn(
            role=role,
            content=content,
            intent=intent,
        ))
        self.last_activity = time.time()

    def get_messages(self, max_turns: int | None = None) -> list[Message]:
        """Get conversation as LLM messages."""
        turns = list(self.turns)
        if max_turns:
            turns = turns[-max_turns:]
        return [t.to_message() for t in turns]

    def clear(self) -> None:
        """Clear conversation history."""
        self.turns.clear()

    @property
    def turn_count(self) -> int:
        """Number of turns in conversation."""
        return len(self.turns)

    @property
    def duration(self) -> float:
        """Duration since conversation started."""
        return time.time() - self.created_at


class ConversationManager:
    """
    Manages conversations and generates LLM responses.

    Handles:
    - Conversation context (last N turns)
    - System prompt building
    - LLM request orchestration
    - Response generation
    """

    def __init__(
        self,
        llm_gateway: LLMGateway,
        system_prompt: str = KIRO_SYSTEM_PROMPT,
        max_context_turns: int = 10,
        conversation_timeout: float = 300.0,  # 5 minutes
    ):
        """
        Initialize conversation manager.

        Args:
            llm_gateway: Gateway for LLM calls
            system_prompt: Base system prompt
            max_context_turns: Max turns to include in context
            conversation_timeout: Seconds before conversation resets
        """
        self.llm = llm_gateway
        self.system_prompt = system_prompt
        self.max_context_turns = max_context_turns
        self.conversation_timeout = conversation_timeout

        self._conversation: Conversation | None = None
        self._running = False

    async def start(self) -> None:
        """Start the conversation manager."""
        self._running = True
        logger.info("conversation_manager_started")

    async def stop(self) -> None:
        """Stop the conversation manager."""
        self._running = False
        self._conversation = None
        logger.info("conversation_manager_stopped")

    def get_or_create_conversation(self) -> Conversation:
        """Get current conversation or create new one."""
        now = time.time()

        # Check if current conversation is stale
        if self._conversation:
            if now - self._conversation.last_activity > self.conversation_timeout:
                logger.info(
                    "conversation_timeout",
                    turns=self._conversation.turn_count,
                    duration=round(self._conversation.duration, 1),
                )
                self._conversation = None

        # Create new if needed
        if self._conversation is None:
            from uuid import uuid4
            self._conversation = Conversation(id=str(uuid4()))
            logger.debug("conversation_created", id=self._conversation.id[:8])

        return self._conversation

    async def process_utterance(
        self,
        intent: Intent,
        additional_context: str | None = None,
    ) -> str:
        """
        Process a user utterance and generate response.

        Args:
            intent: Classified intent from user
            additional_context: Optional context to include

        Returns:
            Generated response text
        """
        if not self._running:
            return "I'm not ready yet."

        conversation = self.get_or_create_conversation()

        # Add user turn
        conversation.add_turn(
            role=Role.USER,
            content=intent.transcript,
            intent=intent,
        )

        # Build system prompt with context
        system = self._build_system_prompt(intent, additional_context)

        # Get conversation messages
        messages = conversation.get_messages(self.max_context_turns)

        logger.debug(
            "generating_response",
            turns=len(messages),
            intent_category=intent.category.value,
        )

        # Generate response
        response = await self.llm.generate(
            messages=messages,
            system_prompt=system,
            max_tokens=256,  # Keep responses concise
            temperature=0.7,
        )

        if response.error:
            logger.error("llm_response_error", error=response.error)
            return "I'm having trouble thinking right now. Could you try again?"

        # Add assistant turn
        conversation.add_turn(role=Role.ASSISTANT, content=response.content)

        logger.info(
            "response_generated",
            tokens=response.total_tokens,
            latency_ms=round(response.latency_ms, 1),
        )

        return response.content

    def _build_system_prompt(
        self,
        intent: Intent,
        additional_context: str | None = None,
    ) -> str:
        """Build system prompt with current context."""
        parts = [self.system_prompt]

        # Add intent context
        if intent.category == IntentCategory.CAPTURE:
            parts.append(
                "\nThe user seems to be capturing a task or commitment. "
                "Help them clarify and confirm what they want to remember."
            )
        elif intent.category == IntentCategory.COMMAND:
            parts.append(
                "\nThe user is giving a direct command. "
                "Acknowledge and confirm the action."
            )

        # Add additional context
        if additional_context:
            parts.append(f"\nAdditional context: {additional_context}")

        return "\n".join(parts)

    def reset_conversation(self) -> None:
        """Reset current conversation."""
        if self._conversation:
            logger.info(
                "conversation_reset",
                turns=self._conversation.turn_count,
            )
        self._conversation = None

    @property
    def current_conversation(self) -> Conversation | None:
        """Get current conversation if active."""
        return self._conversation

    @property
    def is_running(self) -> bool:
        """Check if manager is running."""
        return self._running
