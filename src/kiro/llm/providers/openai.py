"""
OpenAI LLM Provider

Implementation for OpenAI's GPT models.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

import structlog

from kiro.llm.gateway import LLMResponse, Message, Role
from kiro.llm.providers.base import BaseLLMProvider

logger = structlog.get_logger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI GPT provider."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
    ):
        """
        Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key
            model: GPT model (gpt-4o, gpt-4o-mini, etc.)
        """
        super().__init__(api_key=api_key, model=model)
        self._client = None

    def _get_client(self):
        """Lazy-load OpenAI client."""
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a response using GPT."""
        start_time = time.perf_counter()

        try:
            client = self._get_client()

            # Convert messages to OpenAI format
            openai_messages = []

            # Add system prompt first if provided
            if system_prompt:
                openai_messages.append({
                    "role": "system",
                    "content": system_prompt,
                })

            for msg in messages:
                openai_messages.append({
                    "role": msg.role.value,
                    "content": msg.content,
                })

            # Build request kwargs
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": openai_messages,
            }

            if stop_sequences:
                kwargs["stop"] = stop_sequences

            # Make request
            response = await client.chat.completions.create(**kwargs)

            latency_ms = (time.perf_counter() - start_time) * 1000

            # Extract content
            choice = response.choices[0]
            content = choice.message.content or ""

            return LLMResponse(
                content=content,
                model=response.model,
                provider=self.name,
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
                latency_ms=latency_ms,
                finish_reason=choice.finish_reason or "stop",
            )

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error("openai_error", error=str(e), exc_info=True)
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
        """Generate a streaming response using GPT."""
        try:
            client = self._get_client()

            # Convert messages
            openai_messages = []

            if system_prompt:
                openai_messages.append({
                    "role": "system",
                    "content": system_prompt,
                })

            for msg in messages:
                openai_messages.append({
                    "role": msg.role.value,
                    "content": msg.content,
                })

            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": openai_messages,
                "stream": True,
            }

            if stop_sequences:
                kwargs["stop"] = stop_sequences

            stream = await client.chat.completions.create(**kwargs)

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error("openai_stream_error", error=str(e))
            raise
