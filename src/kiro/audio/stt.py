"""
Speech-to-Text Module

Transcribes audio to text using:
- faster-whisper (local, GPU-accelerated) - preferred
- Whisper API (OpenAI cloud) - fallback
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import wave
from pathlib import Path
from typing import Literal

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

STTEngine = Literal["faster-whisper", "whisper-api", "auto"]


class SpeechToText:
    """
    Transcribes audio using faster-whisper (local) or Whisper API (cloud).

    Auto-detects GPU availability and selects optimal engine.
    """

    def __init__(
        self,
        engine: STTEngine = "auto",
        model: str = "base",  # For faster-whisper: tiny, base, small, medium, large-v3
        language: str = "en",
        sample_rate: int = 16000,
        api_key: str | None = None,
        device: str = "auto",  # cuda, cpu, or auto
        compute_type: str = "float16",  # float16, int8_float16, int8
    ):
        """
        Initialize STT.

        Args:
            engine: STT engine - "faster-whisper", "whisper-api", or "auto"
            model: Model name (faster-whisper model or "whisper-1" for API)
            language: Expected language code
            sample_rate: Audio sample rate
            api_key: OpenAI API key (for whisper-api)
            device: Device for faster-whisper (cuda/cpu/auto)
            compute_type: Compute type for faster-whisper
        """
        self._engine_choice = engine
        self._model = model
        self.language = language
        self.sample_rate = sample_rate
        self._api_key = api_key
        self._device = device
        self._compute_type = compute_type

        self._running = False
        self._engine: str | None = None
        self._whisper_model = None  # faster-whisper model
        self._openai_client = None  # OpenAI client

    async def start(self) -> None:
        """Initialize the STT engine."""
        if self._running:
            return

        # Determine engine
        if self._engine_choice == "auto":
            self._engine = await self._detect_best_engine()
        else:
            self._engine = self._engine_choice

        # Initialize selected engine
        if self._engine == "faster-whisper":
            await self._init_faster_whisper()
        elif self._engine == "whisper-api":
            await self._init_whisper_api()

        if self._running:
            logger.info(
                "stt_started",
                engine=self._engine,
                model=self._model,
                language=self.language,
            )

    async def _detect_best_engine(self) -> str:
        """Detect best available STT engine."""
        # Try faster-whisper first (GPU preferred)
        try:
            import torch
            if torch.cuda.is_available():
                logger.debug("stt_detected_gpu", device=torch.cuda.get_device_name(0))
                return "faster-whisper"
        except ImportError:
            pass

        # Check if faster-whisper is available at all (CPU)
        try:
            from faster_whisper import WhisperModel
            logger.debug("stt_detected_faster_whisper_cpu")
            return "faster-whisper"
        except ImportError:
            pass

        # Fall back to API if key is available
        if self._api_key or os.environ.get("OPENAI_API_KEY"):
            return "whisper-api"

        logger.warning("stt_no_engine_available")
        return "whisper-api"  # Will fail gracefully if no key

    async def _init_faster_whisper(self) -> None:
        """Initialize faster-whisper model."""
        try:
            from faster_whisper import WhisperModel

            # Determine device
            device = self._device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"

            compute_type = self._compute_type if device == "cuda" else "int8"

            logger.info(
                "stt_loading_model",
                model=self._model,
                device=device,
                compute_type=compute_type,
            )

            # Load model (runs in thread pool to not block)
            loop = asyncio.get_event_loop()
            self._whisper_model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(
                    self._model,
                    device=device,
                    compute_type=compute_type,
                )
            )

            self._running = True
            logger.info("stt_model_loaded", model=self._model, device=device)

        except Exception as e:
            logger.error("stt_faster_whisper_init_failed", error=str(e))
            # Try falling back to API
            if self._api_key or os.environ.get("OPENAI_API_KEY"):
                logger.info("stt_falling_back_to_api")
                self._engine = "whisper-api"
                await self._init_whisper_api()
            else:
                self._running = False

    async def _init_whisper_api(self) -> None:
        """Initialize OpenAI Whisper API client."""
        if not self._api_key:
            self._api_key = os.environ.get("OPENAI_API_KEY")

        if not self._api_key:
            logger.warning(
                "stt_disabled",
                reason="No API key and faster-whisper unavailable"
            )
            self._running = False
            return

        import openai
        self._openai_client = openai.AsyncOpenAI(api_key=self._api_key)
        self._model = "whisper-1"  # Force correct model for API
        self._running = True

    async def stop(self) -> None:
        """Stop the STT service."""
        self._running = False
        self._whisper_model = None
        self._openai_client = None
        logger.info("stt_stopped")

    async def transcribe(self, audio: np.ndarray) -> dict:
        """
        Transcribe audio to text.

        Args:
            audio: Float32 audio samples at configured sample rate

        Returns:
            dict with:
                - text: Transcribed text
                - confidence: Confidence score (if available)
                - duration: Audio duration in seconds
        """
        if not self._running:
            raise RuntimeError("STT not started")

        duration = len(audio) / self.sample_rate
        logger.debug("stt_transcribing", duration=round(duration, 2), engine=self._engine)

        try:
            if self._engine == "faster-whisper":
                return await self._transcribe_faster_whisper(audio, duration)
            else:
                return await self._transcribe_api(audio, duration)
        except Exception as e:
            logger.error("stt_error", error=str(e), engine=self._engine)
            return {
                "text": "",
                "confidence": 0.0,
                "duration": duration,
                "error": str(e),
            }

    async def _transcribe_faster_whisper(self, audio: np.ndarray, duration: float) -> dict:
        """Transcribe using faster-whisper (local)."""
        import time
        start_time = time.perf_counter()

        # Run transcription in thread pool
        loop = asyncio.get_event_loop()
        segments, info = await loop.run_in_executor(
            None,
            lambda: self._whisper_model.transcribe(
                audio,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
        )

        # Collect all segments
        text_parts = []
        total_prob = 0.0
        segment_count = 0

        for segment in segments:
            text_parts.append(segment.text)
            # Handle different faster-whisper versions
            prob = getattr(segment, 'avg_logprob', None) or getattr(segment, 'avg_log_prob', 0.0)
            total_prob += prob
            segment_count += 1

        text = "".join(text_parts).strip()
        confidence = (total_prob / segment_count) if segment_count > 0 else 0.0

        elapsed = time.perf_counter() - start_time
        logger.info(
            "stt_transcribed",
            engine="faster-whisper",
            text=text[:100] + "..." if len(text) > 100 else text,
            duration=round(duration, 2),
            latency=round(elapsed, 3),
        )

        return {
            "text": text,
            "confidence": confidence,
            "duration": duration,
        }

    async def _transcribe_api(self, audio: np.ndarray, duration: float) -> dict:
        """Transcribe using OpenAI Whisper API."""
        import time
        start_time = time.perf_counter()

        # Convert to WAV format for API
        wav_bytes = self._audio_to_wav(audio)

        response = await self._openai_client.audio.transcriptions.create(
            model=self._model,
            file=("audio.wav", wav_bytes, "audio/wav"),
            language=self.language,
            response_format="verbose_json",
        )

        text = response.text.strip()
        confidence = getattr(response, "confidence", None)

        elapsed = time.perf_counter() - start_time
        logger.info(
            "stt_transcribed",
            engine="whisper-api",
            text=text[:100] + "..." if len(text) > 100 else text,
            duration=round(duration, 2),
            latency=round(elapsed, 3),
        )

        return {
            "text": text,
            "confidence": confidence,
            "duration": duration,
        }

    def _audio_to_wav(self, audio: np.ndarray) -> bytes:
        """Convert float32 audio to WAV bytes."""
        # Convert to int16
        audio_int16 = (audio * 32767).astype(np.int16)

        # Write to bytes buffer as WAV
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(self.sample_rate)
            wav.writeframes(audio_int16.tobytes())

        buffer.seek(0)
        return buffer.read()

    @property
    def is_running(self) -> bool:
        """Check if STT is running."""
        return self._running

    @property
    def engine(self) -> str | None:
        """Get the active engine name."""
        return self._engine
