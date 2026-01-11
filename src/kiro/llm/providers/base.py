"""
Base LLM Provider

Abstract base class for LLM provider implementations.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import AsyncIterator

from kiro.llm.gateway import LLMResponse, Message


class BaseLLMProvider(ABC):
    """Base class for LLM providers."""

    name: str = "base"

    def __init__(self, api_key: str, model: str):
        """
        Initialize provider.

        Args:
            api_key: API key for the provider
            model: Model identifier
        """
        self.api_key = api_key
        self.model = model

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a response from the LLM."""
        pass

    async def generate_stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Generate a streaming response. Default implementation uses non-streaming."""
        response = await self.generate(
            messages=messages,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
        )
        if response.content:
            yield response.content
