"""
Audio Capture Module

Continuous microphone monitoring using sounddevice.
Provides audio chunks to the processing pipeline.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Callable

import numpy as np
import sounddevice as sd
import structlog

logger = structlog.get_logger(__name__)


class AudioCapture:
    """
    Captures audio from microphone and provides chunks to callbacks.

    Uses sounddevice for low-latency audio capture with a background thread
    feeding an asyncio queue.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration: float = 0.1,  # 100ms chunks
        device: str | int | None = None,
    ):
        """
        Initialize audio capture.

        Args:
            sample_rate: Audio sample rate in Hz (16000 for speech)
            channels: Number of audio channels (1 for mono)
            chunk_duration: Duration of each audio chunk in seconds
            device: Audio input device (None for default)
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_duration = chunk_duration
        self.chunk_size = int(sample_rate * chunk_duration)
        self.device = device

        self._stream: sd.InputStream | None = None
        self._running = False
        self._audio_queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=100)
        self._callbacks: list[Callable[[np.ndarray], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: dict,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback from sounddevice - runs in audio thread."""
        if status:
            logger.warning("audio_callback_status", status=str(status))

        # Early exit if shutting down
        if not self._running:
            return

        # Copy data (sounddevice reuses buffer)
        audio_chunk = indata[:, 0].copy() if self.channels == 1 else indata.copy()

        # Put in queue for async processing
        loop = self._loop
        if loop and self._running:
            try:
                loop.call_soon_threadsafe(
                    self._audio_queue.put_nowait, audio_chunk
                )
            except asyncio.QueueFull:
                pass  # Drop frame silently on overload
            except RuntimeError:
                # Event loop is closed - shutdown in progress
                pass

    async def start(self) -> None:
        """Start audio capture."""
        if self._running:
            return

        self._loop = asyncio.get_running_loop()
        self._running = True

        # Query device info for logging
        device_info = sd.query_devices(self.device, "input")
        logger.info(
            "audio_capture_starting",
            device=device_info["name"],
            sample_rate=self.sample_rate,
            chunk_size=self.chunk_size,
        )

        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=np.float32,
            blocksize=self.chunk_size,
            callback=self._audio_callback,
        )
        self._stream.start()

        logger.info("audio_capture_started")

    async def stop(self) -> None:
        """Stop audio capture."""
        if not self._running:
            return

        logger.info("audio_capture_stopping")
        self._running = False
        
        # Clear loop reference to stop callback from queueing
        self._loop = None

        if self._stream:
            try:
                # Abort is faster than stop - doesn't wait for buffer to drain
                self._stream.abort()
                self._stream.close()
            except Exception as e:
                logger.warning("audio_stream_close_error", error=str(e))
            self._stream = None

        # Clear queue and unblock any waiters
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        logger.info("audio_capture_stopped")

    async def get_chunk(self, timeout: float = 1.0) -> np.ndarray | None:
        """
        Get next audio chunk from queue.

        Returns None on timeout or if not running.
        """
        if not self._running:
            return None

        try:
            return await asyncio.wait_for(self._audio_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @property
    def is_running(self) -> bool:
        """Check if capture is running."""
        return self._running

    @staticmethod
    def list_devices() -> list[dict]:
        """List available audio input devices."""
        devices = sd.query_devices()
        input_devices = []
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                input_devices.append({
                    "index": i,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "sample_rate": dev["default_samplerate"],
                })
        return input_devices

    @staticmethod
    def get_default_device() -> dict:
        """Get default input device info."""
        device_info = sd.query_devices(kind="input")
        return {
            "name": device_info["name"],
            "channels": device_info["max_input_channels"],
            "sample_rate": device_info["default_samplerate"],
        }
