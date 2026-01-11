"""
Voice Pipeline

Complete voice interaction pipeline:
Audio → STT → Intent → LLM → TTS → Audio

Orchestrates all subsystems for end-to-end voice conversations.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import structlog

from kiro.audio.pipeline import AudioPipeline, PipelineState
from kiro.audio.tts import TextToSpeech
from kiro.conversation.manager import ConversationManager
from kiro.events import Event, EventBus
from kiro.intent.router import IntentRouter, IntentCategory
from kiro.llm.gateway import LLMGateway
from kiro.llm.providers import get_provider

if TYPE_CHECKING:
    from kiro.config import KiroConfig

logger = structlog.get_logger(__name__)


class VoicePipeline:
    """
    Full voice interaction pipeline.

    Connects audio capture → STT → intent → LLM → TTS
    with event-driven coordination.
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: "KiroConfig",
    ):
        """
        Initialize the voice pipeline.

        Args:
            event_bus: Event bus for coordination
            config: Kiro configuration
        """
        self.event_bus = event_bus
        self.config = config

        # Get API keys from environment
        self._openai_key = os.environ.get("OPENAI_API_KEY")
        self._anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

        # Components (initialized in start())
        self._audio: AudioPipeline | None = None
        self._tts: TextToSpeech | None = None
        self._llm: LLMGateway | None = None
        self._intent: IntentRouter | None = None
        self._conversation: ConversationManager | None = None

        self._running = False

    async def start(self) -> None:
        """Start all pipeline components."""
        if self._running:
            return

        logger.info("voice_pipeline_initializing")

        # Initialize Audio Pipeline
        self._audio = AudioPipeline(
            event_bus=self.event_bus,
            sample_rate=self.config.audio.sample_rate,
            chunk_duration=self.config.audio.chunk_duration,
            audio_device=self.config.audio.input_device,
            wake_word_threshold=self.config.audio.wake_word.threshold,
            wake_word_refractory=self.config.audio.wake_word.refractory_period,
            vad_aggressiveness=self.config.audio.vad.aggressiveness,
            vad_min_speech=self.config.audio.vad.min_speech_duration,
            vad_max_silence=self.config.audio.vad.max_silence_duration,
            # STT config
            stt_engine=self.config.audio.stt.engine,
            stt_model=self.config.audio.stt.model,
            stt_language=self.config.audio.stt.language,
            stt_device=self.config.audio.stt.device,
            stt_compute_type=self.config.audio.stt.compute_type,
            openai_api_key=self._openai_key,
        )

        # Initialize TTS
        tts_cfg = self.config.audio.tts
        self._tts = TextToSpeech(
            piper_model=tts_cfg.piper_model,
            piper_path=tts_cfg.piper_path,
            models_dir=tts_cfg.models_dir,
            openai_api_key=self._openai_key,
            openai_voice=tts_cfg.openai_voice,
        )

        # Initialize LLM Gateway
        self._llm = self._create_llm_gateway()

        # Initialize Intent Router
        self._intent = IntentRouter()

        # Initialize Conversation Manager
        self._conversation = ConversationManager(
            llm_gateway=self._llm,
        )

        # Register event handlers
        self._register_handlers()

        # Start all components
        await self._audio.start()
        await self._tts.start()
        await self._llm.start()
        await self._intent.start()
        await self._conversation.start()

        # Set up intent handlers
        self._setup_intent_handlers()

        self._running = True
        logger.info("voice_pipeline_started")

    def _create_llm_gateway(self) -> LLMGateway:
        """Create LLM gateway with available providers."""
        primary = None
        fallback = None

        # Prefer Claude if available
        if self._anthropic_key:
            primary = get_provider("claude", self._anthropic_key)
            logger.info("llm_provider_configured", provider="claude", role="primary")

        if self._openai_key:
            if primary is None:
                primary = get_provider("openai", self._openai_key)
                logger.info("llm_provider_configured", provider="openai", role="primary")
            else:
                fallback = get_provider("openai", self._openai_key)
                logger.info("llm_provider_configured", provider="openai", role="fallback")

        if primary is None:
            # Create a dummy provider that returns error
            logger.warning("no_llm_api_keys", message="Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
            primary = _DummyProvider()

        return LLMGateway(
            primary_provider=primary,
            fallback_provider=fallback,
        )

    def _register_handlers(self) -> None:
        """Register event handlers."""
        self.event_bus.subscribe("audio.utterance_complete", self._on_utterance_complete)
        self.event_bus.subscribe("audio.wake_word_detected", self._on_wake_word)

    def _setup_intent_handlers(self) -> None:
        """Set up intent category handlers."""
        # Default handler: route to conversation
        self._intent.set_default_handler(self._handle_conversation)

        # Control handler
        self._intent.register_handler(
            IntentCategory.CONTROL,
            self._handle_control,
        )

    async def _on_wake_word(self, event: Event) -> None:
        """Handle wake word detection."""
        # Cancel any ongoing TTS playback
        if self._tts and self._tts.is_playing:
            logger.debug("interrupting_tts_for_wake_word")
            await self._tts.cancel_playback()

    async def _on_utterance_complete(self, event: Event) -> None:
        """Handle completed utterance."""
        transcript = event.payload.get("transcript", "").strip()
        confidence = event.payload.get("confidence")

        if not transcript:
            logger.debug("empty_transcript_ignored")
            return

        logger.info("processing_utterance", transcript=transcript[:80])

        # Classify intent
        intent = self._intent.classify(transcript, confidence)

        await self.event_bus.emit("intent.classified", {
            "category": intent.category.value,
            "confidence": intent.confidence,
            "transcript": transcript,
        })

        # Route to handler
        response = await self._intent.route(intent)

        if response:
            # Speak response
            await self._speak_response(response)

    async def _handle_conversation(self, intent) -> str:
        """Handle conversational intents via LLM."""
        return await self._conversation.process_utterance(intent)

    async def _handle_control(self, intent) -> str:
        """Handle control intents (stop, cancel, etc.)."""
        action = intent.entities.get("action", "")

        if action == "stop":
            if self._tts and self._tts.is_playing:
                await self._tts.cancel_playback()
                return ""  # Silent acknowledgment
            return "Okay."

        if action == "pause":
            return "Pausing."

        return "Okay."

    async def _speak_response(self, text: str) -> None:
        """Speak a response via TTS."""
        if not text or not self._tts:
            return

        logger.debug("speaking_response", text_length=len(text))

        await self.event_bus.emit("tts.started", {"text": text[:100]})

        success = await self._tts.speak_blocking(text)

        await self.event_bus.emit("tts.completed", {"success": success})

    async def stop(self) -> None:
        """Stop all pipeline components."""
        if not self._running:
            return

        logger.info("voice_pipeline_stopping")
        self._running = False

        # Unsubscribe handlers
        # Note: EventBus handles cleanup on stop

        # Stop components in reverse order
        if self._conversation:
            await self._conversation.stop()

        if self._intent:
            await self._intent.stop()

        if self._llm:
            await self._llm.stop()

        if self._tts:
            await self._tts.stop()

        if self._audio:
            await self._audio.stop()

        logger.info("voice_pipeline_stopped")

    @property
    def is_running(self) -> bool:
        """Check if pipeline is running."""
        return self._running


class _DummyProvider:
    """Dummy LLM provider when no API keys are configured."""

    name = "dummy"

    async def generate(self, messages, **kwargs):
        from kiro.llm.gateway import LLMResponse
        return LLMResponse(
            content="I need an API key to think. Please set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
            model="none",
            provider="dummy",
            error="No API key configured",
        )
