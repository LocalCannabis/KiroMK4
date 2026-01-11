"""
LLM Providers

Implementations for various LLM providers.
"""

from kiro.llm.providers.base import BaseLLMProvider
from kiro.llm.providers.claude import ClaudeProvider
from kiro.llm.providers.openai import OpenAIProvider


def get_provider(
    provider_name: str,
    api_key: str,
    model: str | None = None,
) -> BaseLLMProvider:
    """
    Get an LLM provider by name.

    Args:
        provider_name: 'claude' or 'openai'
        api_key: API key for the provider
        model: Optional model override

    Returns:
        Configured provider instance
    """
    providers = {
        "claude": ClaudeProvider,
        "anthropic": ClaudeProvider,
        "openai": OpenAIProvider,
        "gpt": OpenAIProvider,
    }

    provider_class = providers.get(provider_name.lower())
    if not provider_class:
        raise ValueError(
            f"Unknown provider: {provider_name}. "
            f"Available: {list(providers.keys())}"
        )

    kwargs = {"api_key": api_key}
    if model:
        kwargs["model"] = model

    return provider_class(**kwargs)


__all__ = [
    "BaseLLMProvider",
    "ClaudeProvider",
    "OpenAIProvider",
    "get_provider",
]
