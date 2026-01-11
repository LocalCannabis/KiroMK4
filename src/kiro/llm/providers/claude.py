"""
Claude (Anthropic) LLM Provider

Implementation for Anthropic's Claude models.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

import structlog

from kiro.llm.gateway import LLMResponse, Message, Role
from kiro.llm.providers.base import BaseLLMProvider

logger = structlog.get_logger(__name__)


class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude provider."""

    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
    ):
        """
        Initialize Claude provider.

        Args:
            api_key: Anthropic API key
            model: Claude model (claude-sonnet-4-20250514, claude-3-haiku, etc.)
        """
        super().__init__(api_key=api_key, model=model)
        self._client = None

    def _get_client(self):
        """Lazy-load Anthropic client."""
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a response using Claude."""
        start_time = time.perf_counter()

        try:
            client = self._get_client()

            # Convert messages to Anthropic format
            anthropic_messages = []
            for msg in messages:
                if msg.role == Role.SYSTEM:
                    # Claude handles system prompt separately
                    if system_prompt is None:
                        system_prompt = msg.content
                    continue
                anthropic_messages.append({
                    "role": msg.role.value,
                    "content": msg.content,
                })

            # Build request kwargs
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": anthropic_messages,
            }

            if system_prompt:
                kwargs["system"] = system_prompt

            if stop_sequences:
                kwargs["stop_sequences"] = stop_sequences

            # Make request
            response = await client.messages.create(**kwargs)

            latency_ms = (time.perf_counter() - start_time) * 1000

            # Extract content
            content = ""
            if response.content:
                content = response.content[0].text

            return LLMResponse(
                content=content,
                model=response.model,
                provider=self.name,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=latency_ms,
                finish_reason=response.stop_reason or "stop",
            )

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error("claude_error", error=str(e), exc_info=True)
            return LLMResponse(
                content="",
                model=self.model,
                provider=self.name,
                latency_ms=latency_ms,
                error=str(e),
            )

    async def generate_stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Generate a streaming response using Claude."""
        try:
            client = self._get_client()

            # Convert messages
            anthropic_messages = []
            for msg in messages:
                if msg.role == Role.SYSTEM:
                    if system_prompt is None:
                        system_prompt = msg.content
                    continue
                anthropic_messages.append({
                    "role": msg.role.value,
                    "content": msg.content,
                })

            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": anthropic_messages,
            }

            if system_prompt:
                kwargs["system"] = system_prompt

            if stop_sequences:
                kwargs["stop_sequences"] = stop_sequences

            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text

        except Exception as e:
            logger.error("claude_stream_error", error=str(e))
            raise
