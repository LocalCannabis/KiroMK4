"""
Audio Pipeline Orchestrator

Coordinates the full audio flow:
Microphone → Wake Word → Recording → VAD → STT → Events
"""

from __future__ import annotations

import asyncio
from enum import Enum, auto
from typing import TYPE_CHECKING

import numpy as np
import structlog

from kiro.audio.capture import AudioCapture
from kiro.audio.wake_word import WakeWordDetector
from kiro.audio.vad import VoiceActivityDetector
from kiro.audio.stt import SpeechToText
from kiro.events import Event, EventBus

if TYPE_CHECKING:
    from kiro.config import KiroConfig

logger = structlog.get_logger(__name__)


class PipelineState(Enum):
    """States of the audio pipeline."""

    IDLE = auto()           # Waiting for wake word
    LISTENING = auto()      # Recording utterance
    PROCESSING = auto()     # Transcribing audio


class AudioPipeline:
    """
    Orchestrates the complete audio pipeline.

    State machine:
    1. IDLE: Listen for wake word
    2. LISTENING: Record speech until silence
    3. PROCESSING: Transcribe and emit result
    4. Back to IDLE
    """

    def __init__(
        self,
        event_bus: EventBus,
        sample_rate: int = 16000,
        chunk_duration: float = 0.1,
        audio_device: str | int | None = None,
        wake_word_threshold: float = 0.5,
        wake_word_refractory: float = 2.0,
        vad_aggressiveness: int = 2,
        vad_min_speech: float = 0.25,
        vad_max_silence: float = 0.8,
        # STT options
        stt_engine: str = "auto",
        stt_model: str = "base",
        stt_language: str = "en",
        stt_device: str = "auto",
        stt_compute_type: str = "float16",
        openai_api_key: str | None = None,
    ):
        """Initialize the audio pipeline with all components."""
        self.event_bus = event_bus
        self.sample_rate = sample_rate

        # Initialize components
        self._capture = AudioCapture(
            sample_rate=sample_rate,
            channels=1,
            chunk_duration=chunk_duration,
            device=audio_device,
        )

        self._wake_word = WakeWordDetector(
            threshold=wake_word_threshold,
            refractory_period=wake_word_refractory,
            sample_rate=sample_rate,
        )

        self._vad = VoiceActivityDetector(
            sample_rate=sample_rate,
            aggressiveness=vad_aggressiveness,
            min_speech_duration=vad_min_speech,
            max_silence_duration=vad_max_silence,
        )

        self._stt = SpeechToText(
            engine=stt_engine,
            model=stt_model,
            language=stt_language,
            sample_rate=sample_rate,
            api_key=openai_api_key,
            device=stt_device,
            compute_type=stt_compute_type,
        )

        # State
        self._state = PipelineState.IDLE
        self._running = False
        self._audio_buffer: list[np.ndarray] = []
        self._task: asyncio.Task | None = None

    @classmethod
    def from_config(cls, config: "KiroConfig", event_bus: EventBus) -> "AudioPipeline":
        """Create pipeline from Kiro config."""
        import os

        # Get audio config (use defaults if not configured)
        audio_cfg = getattr(config, "audio", None)
        stt_cfg = getattr(audio_cfg, "stt", None) if audio_cfg else None
        vad_cfg = getattr(audio_cfg, "vad", None) if audio_cfg else None
        wake_cfg = getattr(audio_cfg, "wake_word", None) if audio_cfg else None

        return cls(
            event_bus=event_bus,
            sample_rate=getattr(audio_cfg, "sample_rate", 16000) if audio_cfg else 16000,
            chunk_duration=getattr(audio_cfg, "chunk_duration", 0.1) if audio_cfg else 0.1,
            audio_device=getattr(audio_cfg, "input_device", None) if audio_cfg else None,
            # Wake word
            wake_word_threshold=getattr(wake_cfg, "threshold", 0.5) if wake_cfg else 0.5,
            wake_word_refractory=getattr(wake_cfg, "refractory_period", 2.0) if wake_cfg else 2.0,
            # VAD
            vad_aggressiveness=getattr(vad_cfg, "aggressiveness", 2) if vad_cfg else 2,
            vad_min_speech=getattr(vad_cfg, "min_speech_duration", 0.25) if vad_cfg else 0.25,
            vad_max_silence=getattr(vad_cfg, "max_silence_duration", 0.5) if vad_cfg else 0.5,
            # STT
            stt_engine=getattr(stt_cfg, "engine", "auto") if stt_cfg else "auto",
            stt_model=getattr(stt_cfg, "model", "base") if stt_cfg else "base",
            stt_language=getattr(stt_cfg, "language", "en") if stt_cfg else "en",
            stt_device=getattr(stt_cfg, "device", "auto") if stt_cfg else "auto",
            stt_compute_type=getattr(stt_cfg, "compute_type", "float16") if stt_cfg else "float16",
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
        )

    async def start(self) -> None:
        """Start the audio pipeline."""
        if self._running:
            return

        logger.info("audio_pipeline_starting")

        # Start all components
        await self._capture.start()
        await self._wake_word.start()
        await self._vad.start()
        await self._stt.start()

        self._running = True
        self._state = PipelineState.IDLE

        # Start processing loop
        self._task = asyncio.create_task(self._process_loop())

        logger.info("audio_pipeline_started", message="Listening for wake word...")

    async def stop(self) -> None:
        """Stop the audio pipeline."""
        if not self._running:
            return

        logger.info("audio_pipeline_stopping")
        self._running = False

        # Cancel processing task first
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None

        # Stop all components in reverse order
        try:
            await asyncio.wait_for(self._stt.stop(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("stt_stop_timeout")
        
        try:
            await asyncio.wait_for(self._vad.stop(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("vad_stop_timeout")
        
        try:
            await asyncio.wait_for(self._wake_word.stop(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("wake_word_stop_timeout")
        
        try:
            await asyncio.wait_for(self._capture.stop(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("capture_stop_timeout")

        logger.info("audio_pipeline_stopped")

    async def _process_loop(self) -> None:
        """Main processing loop."""
        logger.debug("audio_process_loop_started")
        
        chunk_count = 0
        last_level_log = 0

        while self._running:
            try:
                # Get audio chunk with short timeout for responsiveness
                chunk = await self._capture.get_chunk(timeout=0.1)
                if chunk is None:
                    # Check if we should still be running
                    if not self._running:
                        break
                    continue

                chunk_count += 1
                
                # Log audio level periodically (every ~5 seconds)
                if chunk_count - last_level_log >= 50:
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    peak = float(np.max(np.abs(chunk)))
                    logger.debug(
                        "audio_level",
                        rms=round(rms, 4),
                        peak=round(peak, 4),
                        chunks=chunk_count,
                    )
                    last_level_log = chunk_count

                # Process based on current state
                if self._state == PipelineState.IDLE:
                    await self._handle_idle(chunk)
                elif self._state == PipelineState.LISTENING:
                    await self._handle_listening(chunk)
                # PROCESSING state is handled inline

            except asyncio.CancelledError:
                logger.debug("audio_process_loop_cancelled")
                break
            except Exception as e:
                logger.error("audio_pipeline_error", error=str(e), exc_info=True)
                if not self._running:
                    break
                await asyncio.sleep(0.1)  # Brief pause on error

        logger.debug("audio_process_loop_stopped")

    async def _handle_idle(self, chunk: np.ndarray) -> None:
        """Handle audio in IDLE state - looking for wake word."""
        detection = self._wake_word.process(chunk)

        if detection:
            logger.info("wake_word_triggered", score=round(detection["score"], 3))

            # Emit event
            await self.event_bus.emit("audio.wake_word_detected", {
                "model": detection["model"],
                "score": detection["score"],
            })

            # Transition to listening
            self._state = PipelineState.LISTENING
            self._audio_buffer = []

            # Get padding audio (before wake word)
            padding = self._vad.get_padding_audio()
            if padding is not None:
                self._audio_buffer.append(padding)

            self._vad.reset()

            await self.event_bus.emit("audio.utterance_started", {})

    async def _handle_listening(self, chunk: np.ndarray) -> None:
        """Handle audio in LISTENING state - recording utterance."""
        # Buffer audio
        self._audio_buffer.append(chunk)

        # Check VAD
        vad_result = self._vad.process(chunk)

        if vad_result["end_of_speech"]:
            logger.info(
                "utterance_complete",
                duration=round(vad_result["speech_duration"], 2),
                chunks=len(self._audio_buffer),
            )

            # Transition to processing
            self._state = PipelineState.PROCESSING

            # Combine audio
            full_audio = np.concatenate(self._audio_buffer)
            self._audio_buffer = []

            # Transcribe
            await self._transcribe_and_emit(full_audio)

            # Back to idle
            self._wake_word.reset()
            self._vad.reset()
            self._state = PipelineState.IDLE
            logger.info("listening_for_wake_word")

        elif len(self._audio_buffer) > 300:  # ~30 seconds at 100ms chunks
            # Safety: max recording length
            logger.warning("utterance_too_long", chunks=len(self._audio_buffer))
            self._audio_buffer = []
            self._vad.reset()
            self._state = PipelineState.IDLE

    async def _transcribe_and_emit(self, audio: np.ndarray) -> None:
        """Transcribe audio and emit event with result."""
        result = await self._stt.transcribe(audio)

        if result.get("error"):
            await self.event_bus.emit("audio.transcription_error", {
                "error": result["error"],
                "duration": result["duration"],
            })
        elif result["text"]:
            await self.event_bus.emit("audio.utterance_complete", {
                "transcript": result["text"],
                "confidence": result.get("confidence"),
                "duration": result["duration"],
            })
            logger.info(
                "transcript_ready",
                text=result["text"][:80] + "..." if len(result["text"]) > 80 else result["text"],
            )
        else:
            logger.debug("empty_transcript")

    @property
    def state(self) -> PipelineState:
        """Get current pipeline state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if pipeline is running."""
        return self._running
