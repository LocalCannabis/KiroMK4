"""
ambient/llm.py — OpenRouter LLM client for the Ambient Intelligence Layer.

Cost-conscious model routing: different tasks use different model tiers.
All calls go through OpenRouter for unified billing and model access.

Uses the OpenAI-compatible SDK pointed at https://openrouter.ai/api/v1.
Model IDs use OpenRouter's format: e.g. 'anthropic/claude-3.5-haiku'
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger("ambient.llm")

# Default models per task — overridden by kiro_ambient_config.model_routing
# Use OpenRouter model IDs (anthropic/claude-X.Y-name format)
DEFAULT_MODELS = {
    "tagger": "anthropic/claude-3.5-haiku",    # cheap + fast for high-volume tagging
    "patterns": "anthropic/claude-sonnet-4.5",  # reasoning for pattern detection
    "bridger": "anthropic/claude-sonnet-4.5",   # cross-domain synthesis
    "knowledge": "anthropic/claude-sonnet-4.5", # knowledge evaluation
    "briefing": "anthropic/claude-sonnet-4.5",  # voice + personality
    "alert": "anthropic/claude-sonnet-4.5",     # urgency judgment
}


class AmbientLLM:
    """
    OpenRouter-backed LLM client with task-based model routing.

    Each processing task (tagger, patterns, bridger, etc.) can use a
    different model tier for cost control.
    """

    def __init__(self, model_routing: Optional[Dict[str, str]] = None) -> None:
        # Prefer OpenRouter key; OPENAI_API_KEY only works if you also override the base_url
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if api_key:
            base_url = "https://openrouter.ai/api/v1"
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            base_url = "https://api.openai.com/v1"
            if api_key:
                logger.warning(
                    "Using OPENAI_API_KEY — Claude model IDs won't work. "
                    "Set OPENROUTER_API_KEY for full ambient model routing."
                )
        if not api_key:
            raise RuntimeError(
                "No API key found. Set OPENROUTER_API_KEY in ~/.kiro/ambient.env."
            )

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        logger.info("AmbientLLM using %s", base_url)
        self._models = {**DEFAULT_MODELS, **(model_routing or {})}
        logger.info("AmbientLLM initialized with models: %s", self._models)

    def complete(
        self,
        task: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1000,
        json_mode: bool = False,
    ) -> str:
        """
        Send a completion request using the model assigned to the given task.

        Args:
            task: One of 'tagger', 'patterns', 'bridger', 'knowledge', 'briefing', 'alert'
            system_prompt: System message
            user_prompt: User message
            temperature: LLM temperature (lower = more deterministic)
            max_tokens: Max response tokens
            json_mode: If True, request JSON response format

        Returns:
            The LLM response text.
        """
        model = self._models.get(task, self._models.get("tagger"))
        logger.debug("LLM request: task=%s model=%s tokens=%d", task, model, max_tokens)

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            logger.debug("LLM response: %d chars", len(content))
            return content
        except Exception as e:
            logger.error("LLM request failed (task=%s, model=%s): %s", task, model, e)
            raise

    def complete_json(
        self,
        task: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
    ) -> Dict[str, Any]:
        """Send a completion request expecting JSON response. Returns parsed dict."""
        raw = self.complete(
            task=task,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            logger.warning("Failed to parse JSON response, attempting extraction")
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
            raise
