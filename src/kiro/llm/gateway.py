"""
LLM Gateway

Provider-agnostic interface for large language model interactions.
Supports Claude (Anthropic) and GPT (OpenAI) with automatic fallback.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Protocol

import structlog

logger = structlog.get_logger(__name__)


class Role(str, Enum):
    """Message roles in a conversation."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    """A single message in a conversation."""
    role: Role
    content: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Convert to provider-compatible dict."""
        return {"role": self.role.value, "content": self.content}


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0
    finish_reason: str = "stop"
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    name: str

    async def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a response from the LLM."""
        ...

    async def generate_stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Generate a streaming response from the LLM."""
        ...


class LLMGateway:
    """
    Gateway for LLM interactions.

    Provides:
    - Provider abstraction
    - Automatic fallback on errors
    - Request/response logging
    - Retry with exponential backoff
    """

    def __init__(
        self,
        primary_provider: LLMProvider,
        fallback_provider: LLMProvider | None = None,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        timeout: float = 30.0,
    ):
        """
        Initialize the gateway.

        Args:
            primary_provider: Main LLM provider
            fallback_provider: Backup provider if primary fails
            max_retries: Maximum retry attempts per provider
            retry_delay: Initial delay between retries (exponential backoff)
            timeout: Request timeout in seconds
        """
        self.primary = primary_provider
        self.fallback = fallback_provider
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout

        self._running = False

    async def start(self) -> None:
        """Start the gateway."""
        self._running = True
        providers = [self.primary.name]
        if self.fallback:
            providers.append(self.fallback.name)
        logger.info("llm_gateway_started", providers=providers)

    async def stop(self) -> None:
        """Stop the gateway."""
        self._running = False
        logger.info("llm_gateway_stopped")

    async def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """
        Generate a response from the LLM.

        Tries primary provider first, falls back on failure.
        """
        if not self._running:
            return LLMResponse(
                content="",
                model="",
                provider="",
                error="LLM gateway not running",
            )

        logger.debug(
            "llm_request",
            message_count=len(messages),
            has_system=system_prompt is not None,
            max_tokens=max_tokens,
        )

        # Try primary provider
        response = await self._try_provider(
            self.primary,
            messages,
            system_prompt,
            max_tokens,
            temperature,
            stop_sequences,
        )

        if response.error and self.fallback:
            logger.warning(
                "llm_primary_failed",
                provider=self.primary.name,
                error=response.error,
            )
            # Try fallback
            response = await self._try_provider(
                self.fallback,
                messages,
                system_prompt,
                max_tokens,
                temperature,
                stop_sequences,
            )

        if response.error:
            logger.error(
                "llm_all_providers_failed",
                error=response.error,
            )
        else:
            logger.info(
                "llm_response",
                provider=response.provider,
                model=response.model,
                tokens=response.total_tokens,
                latency_ms=round(response.latency_ms, 1),
            )

        return response

    async def _try_provider(
        self,
        provider: LLMProvider,
        messages: list[Message],
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
        stop_sequences: list[str] | None,
    ) -> LLMResponse:
        """Try a provider with retries."""
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    provider.generate(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stop_sequences=stop_sequences,
                    ),
                    timeout=self.timeout,
                )
                return response

            except asyncio.TimeoutError:
                last_error = f"Request timed out after {self.timeout}s"
                logger.warning(
                    "llm_timeout",
                    provider=provider.name,
                    attempt=attempt + 1,
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "llm_error",
                    provider=provider.name,
                    attempt=attempt + 1,
                    error=last_error,
                )

            # Exponential backoff
            if attempt < self.max_retries:
                delay = self.retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)

        return LLMResponse(
            content="",
            model="",
            provider=provider.name,
            error=last_error,
        )

    @property
    def is_running(self) -> bool:
        """Check if gateway is running."""
        return self._running
