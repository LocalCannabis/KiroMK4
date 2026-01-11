"""
Wake Word Detection Module

Detects wake words using OpenWakeWord library.
Triggers when user says "Hey Jarvis" (closest to "Hey Kiro" in built-in models).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# OpenWakeWord model loading is slow, so we do it lazily
_oww_model = None


def _get_oww_model():
    """Lazy-load OpenWakeWord model."""
    global _oww_model
    if _oww_model is None:
        import openwakeword
        from openwakeword.model import Model
        
        # Find built-in model path
        pkg_dir = os.path.dirname(openwakeword.__file__)
        models_dir = os.path.join(pkg_dir, 'resources', 'models')
        
        # Use "hey_jarvis" as closest to "hey kiro"
        # In production, train a custom model for "hey kiro"
        model_path = os.path.join(models_dir, 'hey_jarvis_v0.1.onnx')
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Wake word model not found: {model_path}")
        
        _oww_model = Model(wakeword_model_paths=[model_path])
        logger.info("openwakeword_model_loaded", model="hey_jarvis")
    return _oww_model


class WakeWordDetector:
    """
    Detects wake words in audio stream.

    Uses OpenWakeWord for efficient, low-CPU wake word detection.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        refractory_period: float = 2.0,
        sample_rate: int = 16000,
    ):
        """
        Initialize wake word detector.

        Args:
            threshold: Detection confidence threshold (0-1)
            refractory_period: Minimum seconds between detections
            sample_rate: Expected audio sample rate
        """
        self.threshold = threshold
        self.refractory_period = refractory_period
        self.sample_rate = sample_rate

        self._model = None
        self._last_detection_time: float = 0
        self._running = False

    async def start(self) -> None:
        """Initialize the wake word model."""
        if self._running:
            return

        logger.info("wake_word_detector_starting", threshold=self.threshold)

        # Load model in thread pool to avoid blocking
        loop = asyncio.get_running_loop()
        self._model = await loop.run_in_executor(None, _get_oww_model)

        self._running = True
        logger.info("wake_word_detector_started")

    async def stop(self) -> None:
        """Stop the detector."""
        self._running = False
        logger.info("wake_word_detector_stopped")

    def process(self, audio_chunk: np.ndarray) -> dict | None:
        """
        Process audio chunk for wake word detection.

        Args:
            audio_chunk: Float32 audio samples

        Returns:
            Detection dict with score if wake word detected, None otherwise
        """
        if not self._running or self._model is None:
            return None

        import time
        current_time = time.time()

        # Check refractory period
        if current_time - self._last_detection_time < self.refractory_period:
            return None

        # Convert to int16 for OpenWakeWord (expects -32768 to 32767)
        audio_int16 = (audio_chunk * 32767).astype(np.int16)

        # Run prediction
        prediction = self._model.predict(audio_int16)

        # Check for wake word detection
        # OpenWakeWord returns dict with model names as keys
        for model_name, scores in prediction.items():
            if isinstance(scores, np.ndarray):
                score = float(scores[-1]) if len(scores) > 0 else 0.0
            else:
                score = float(scores)

            if score >= self.threshold:
                self._last_detection_time = current_time
                logger.info(
                    "wake_word_detected",
                    model=model_name,
                    score=round(score, 3),
                )
                return {
                    "model": model_name,
                    "score": score,
                    "timestamp": current_time,
                }

        return None

    def reset(self) -> None:
        """Reset detector state (e.g., after handling an utterance)."""
        if self._model:
            self._model.reset()

    @property
    def is_running(self) -> bool:
        """Check if detector is running."""
        return self._running
