"""
Text-to-Speech Module

Converts text responses to spoken audio using Piper TTS (local)
with fallback to cloud providers.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class TextToSpeech:
    """
    Text-to-speech using Piper (local) with cloud fallback.

    Piper provides fast, high-quality local TTS without API costs.
    Falls back to OpenAI TTS if Piper is unavailable.
    """

    def __init__(
        self,
        piper_model: str | Path | None = None,
        piper_path: str | Path | None = None,
        models_dir: str | Path | None = None,
        sample_rate: int = 22050,
        openai_api_key: str | None = None,
        openai_voice: str = "nova",
    ):
        """
        Initialize TTS.

        Args:
            piper_model: Piper voice model name or path to .onnx file
            piper_path: Path to piper executable (auto-detected if None)
            models_dir: Directory containing Piper models
            sample_rate: Output sample rate (Piper default is 22050)
            openai_api_key: OpenAI API key for fallback TTS
            openai_voice: OpenAI TTS voice (alloy, echo, fable, onyx, nova, shimmer)
        """
        self._piper_model_name = piper_model or "en_US-amy-medium"
        self._models_dir = models_dir
        self.piper_path = self._find_piper(piper_path)
        self.piper_model = self._find_piper_model()
        self.sample_rate = sample_rate
        self._openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        self._openai_voice = openai_voice

        self._running = False
        self._current_playback: asyncio.Task | None = None
        self._on_interrupt: Callable[[], None] | None = None
        self._engine: str | None = None

    def _find_piper(self, piper_path: str | Path | None) -> str | None:
        """Find piper executable."""
        if piper_path:
            path = Path(piper_path)
            if path.exists():
                return str(path)

        import shutil

        # Check PATH
        piper = shutil.which("piper")
        if piper:
            return piper

        # Check project-local installation
        project_paths = [
            Path(__file__).parent.parent.parent.parent / "bin" / "piper" / "piper",
            Path.cwd() / "bin" / "piper" / "piper",
        ]

        for path in project_paths:
            if path.exists():
                return str(path)

        # Check common system locations
        system_paths = [
            Path.home() / ".local" / "bin" / "piper",
            Path("/usr/local/bin/piper"),
            Path("/usr/bin/piper"),
        ]

        for path in system_paths:
            if path.exists():
                return str(path)

        return None

    def _find_piper_model(self) -> str | None:
        """Find piper model file."""
        model_name = self._piper_model_name

        # If already a full path
        if isinstance(model_name, Path) or (isinstance(model_name, str) and "/" in model_name):
            path = Path(model_name)
            if path.exists():
                return str(path)

        # Check models directory
        search_dirs = []

        if self._models_dir:
            search_dirs.append(Path(self._models_dir))

        # Project-local models
        search_dirs.extend([
            Path(__file__).parent.parent.parent.parent / "models" / "piper",
            Path.cwd() / "models" / "piper",
        ])

        # System locations
        search_dirs.extend([
            Path.home() / ".local" / "share" / "piper-voices",
            Path("/usr/share/piper-voices"),
        ])

        for dir_path in search_dirs:
            if not dir_path.exists():
                continue

            # Try exact name with .onnx
            model_file = dir_path / f"{model_name}.onnx"
            if model_file.exists():
                return str(model_file)

            # Try just the name
            model_file = dir_path / model_name
            if model_file.exists():
                return str(model_file)

        return None

    async def start(self) -> None:
        """Initialize TTS system."""
        if self._running:
            return

        self._running = True

        if self.piper_path and self.piper_model:
            self._engine = "piper"
            logger.info(
                "tts_started",
                engine="piper",
                model=Path(self.piper_model).stem,
                piper_path=self.piper_path,
            )
        elif self._openai_api_key:
            self._engine = "openai"
            logger.info("tts_started", engine="openai", voice=self._openai_voice)
        else:
            self._engine = None
            logger.warning(
                "tts_disabled",
                reason="No Piper installation/model found and no OpenAI API key provided",
                piper_path=self.piper_path,
                piper_model=self.piper_model,
            )

    async def stop(self) -> None:
        """Stop TTS and cancel any ongoing playback."""
        if not self._running:
            return

        self._running = False
        await self.cancel_playback()
        logger.info("tts_stopped")

    def _preprocess_text(self, text: str) -> str:
        """
        Preprocess text for better TTS pronunciation.
        
        Handles custom pronunciations and text normalization.
        """
        import re
        
        # Pronunciation substitutions (case-insensitive)
        substitutions = {
            r'\bKiro\b': 'Keero',      # Key-row, not Cairo
            r'\bKiro\'s\b': "Keero's",
        }
        
        result = text
        for pattern, replacement in substitutions.items():
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        
        return result

    async def speak(
        self,
        text: str,
        on_start: Callable[[], None] | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> bool:
        """
        Speak text asynchronously.

        Args:
            text: Text to speak
            on_start: Callback when speech starts
            on_complete: Callback when speech completes

        Returns:
            True if speech started successfully
        """
        if not self._running:
            logger.warning("tts_not_running")
            return False

        if not text.strip():
            return False

        # Preprocess for pronunciation
        text = self._preprocess_text(text)

        # Cancel any ongoing playback
        await self.cancel_playback()

        # Generate and play audio
        try:
            audio_data = await self._synthesize(text)
            if audio_data is None:
                return False

            if on_start:
                on_start()

            self._current_playback = asyncio.create_task(
                self._play_audio(audio_data, on_complete)
            )
            return True

        except Exception as e:
            logger.error("tts_error", error=str(e), exc_info=True)
            return False

    async def speak_blocking(self, text: str) -> bool:
        """
        Speak text and wait for completion.

        Args:
            text: Text to speak

        Returns:
            True if speech completed successfully
        """
        completed = asyncio.Event()
        success = [False]

        def on_complete():
            success[0] = True
            completed.set()

        started = await self.speak(text, on_complete=on_complete)
        if not started:
            return False

        try:
            await completed.wait()
            return success[0]
        except asyncio.CancelledError:
            await self.cancel_playback()
            return False

    async def cancel_playback(self) -> None:
        """Cancel current playback."""
        if self._current_playback and not self._current_playback.done():
            self._current_playback.cancel()
            try:
                await self._current_playback
            except asyncio.CancelledError:
                pass
            self._current_playback = None
            logger.debug("tts_playback_cancelled")

    async def _synthesize(self, text: str) -> bytes | None:
        """Synthesize text to audio bytes."""
        # Try Piper first
        if self.piper_path and self.piper_model:
            audio = await self._synthesize_piper(text)
            if audio:
                return audio

        # Fall back to OpenAI
        if self._openai_api_key:
            return await self._synthesize_openai(text)

        logger.error("tts_no_engine", message="No TTS engine available")
        return None

    async def _synthesize_piper(self, text: str) -> bytes | None:
        """Synthesize using Piper TTS."""
        try:
            start_time = time.perf_counter()
            logger.debug("tts_synthesizing", engine="piper", text_length=len(text))

            # Run piper in subprocess
            proc = await asyncio.create_subprocess_exec(
                self.piper_path,
                "--model", self.piper_model,
                "--output-raw",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate(input=text.encode())

            if proc.returncode != 0:
                logger.warning(
                    "piper_error",
                    returncode=proc.returncode,
                    stderr=stderr.decode()[:200]
                )
                return None

            elapsed = time.perf_counter() - start_time
            audio_duration = len(stdout) / 2 / self.sample_rate  # 16-bit = 2 bytes
            logger.info(
                "tts_synthesized",
                engine="piper",
                text_length=len(text),
                audio_duration=round(audio_duration, 2),
                latency=round(elapsed, 3),
            )
            return stdout

        except FileNotFoundError:
            logger.warning("piper_not_found", path=self.piper_path)
            self.piper_path = None  # Disable for future calls
            return None
        except Exception as e:
            logger.error("piper_error", error=str(e))
            return None

    async def _synthesize_openai(self, text: str) -> bytes | None:
        """Synthesize using OpenAI TTS."""
        try:
            import openai

            logger.debug("tts_synthesizing", engine="openai", text_length=len(text))

            client = openai.AsyncOpenAI(api_key=self._openai_api_key)

            response = await client.audio.speech.create(
                model="tts-1",
                voice=self._openai_voice,
                input=text,
                response_format="pcm",
            )

            audio_bytes = response.content
            logger.debug("tts_synthesized", engine="openai", audio_bytes=len(audio_bytes))
            return audio_bytes

        except Exception as e:
            logger.error("openai_tts_error", error=str(e))
            return None

    async def _play_audio(
        self,
        audio_data: bytes,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        """Play audio data through speakers."""
        try:
            import sounddevice as sd

            # Convert raw PCM to numpy array
            # Piper outputs 16-bit signed PCM at 22050 Hz
            audio = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

            logger.debug("tts_playing", samples=len(audio), duration=len(audio) / self.sample_rate)

            # Play audio (blocking in thread)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: sd.play(audio, samplerate=self.sample_rate, blocking=True)
            )

            logger.debug("tts_playback_complete")

            if on_complete:
                on_complete()

        except asyncio.CancelledError:
            # Stop playback
            import sounddevice as sd
            sd.stop()
            raise
        except Exception as e:
            logger.error("tts_playback_error", error=str(e), exc_info=True)

    @property
    def is_playing(self) -> bool:
        """Check if currently playing audio."""
        return self._current_playback is not None and not self._current_playback.done()

    @property
    def is_running(self) -> bool:
        """Check if TTS is running."""
        return self._running

    @property
    def engine(self) -> str | None:
        """Get the active engine name."""
        return self._engine
