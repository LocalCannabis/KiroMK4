"""
Voice Activity Detection (VAD) Module

Detects speech presence and end-of-utterance using WebRTC VAD.
"""

from __future__ import annotations

import collections
import time

import numpy as np
import structlog
import webrtcvad

logger = structlog.get_logger(__name__)


class VoiceActivityDetector:
    """
    Detects voice activity and end-of-speech.

    Uses WebRTC VAD for robust speech detection. Tracks speech state
    to determine when user has finished speaking.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        aggressiveness: int = 2,
        frame_duration_ms: int = 30,
        min_speech_duration: float = 0.25,
        max_silence_duration: float = 0.8,
        padding_duration: float = 0.3,
    ):
        """
        Initialize VAD.

        Args:
            sample_rate: Audio sample rate (must be 8000, 16000, 32000, or 48000)
            aggressiveness: VAD aggressiveness (0-3, higher = more aggressive filtering)
            frame_duration_ms: Frame size for VAD (10, 20, or 30 ms)
            min_speech_duration: Minimum speech to trigger "speaking" state
            max_silence_duration: Silence duration to trigger end-of-speech
            padding_duration: Extra audio to capture before/after speech
        """
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError(f"Sample rate must be 8000, 16000, 32000, or 48000, got {sample_rate}")
        if frame_duration_ms not in (10, 20, 30):
            raise ValueError(f"Frame duration must be 10, 20, or 30 ms, got {frame_duration_ms}")

        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)

        self.min_speech_duration = min_speech_duration
        self.max_silence_duration = max_silence_duration
        self.padding_duration = padding_duration

        # WebRTC VAD
        self._vad = webrtcvad.Vad(aggressiveness)

        # State tracking
        self._is_speaking = False
        self._speech_start_time: float | None = None
        self._last_speech_time: float | None = None
        self._triggered = False

        # Ring buffer for padding
        padding_frames = int(padding_duration / (frame_duration_ms / 1000))
        self._ring_buffer: collections.deque = collections.deque(maxlen=padding_frames)

        self._running = False

    async def start(self) -> None:
        """Start the VAD."""
        self._running = True
        self.reset()
        logger.info(
            "vad_started",
            min_speech=self.min_speech_duration,
            max_silence=self.max_silence_duration,
        )

    async def stop(self) -> None:
        """Stop the VAD."""
        self._running = False
        logger.info("vad_stopped")

    def reset(self) -> None:
        """Reset VAD state for new utterance."""
        self._is_speaking = False
        self._speech_start_time = None
        self._last_speech_time = None
        self._triggered = False
        self._ring_buffer.clear()

    def buffer_audio(self, audio_chunk: np.ndarray) -> None:
        """
        Buffer audio without full VAD processing.
        
        Use this during IDLE state to capture pre-wake-word audio
        so it's available when utterance recording starts.
        """
        # Store in ring buffer (speech flag doesn't matter for padding)
        self._ring_buffer.append((audio_chunk.copy(), False))

    def process(self, audio_chunk: np.ndarray) -> dict:
        """
        Process audio chunk for voice activity.

        Args:
            audio_chunk: Float32 audio samples

        Returns:
            dict with:
                - is_speech: True if current frame contains speech
                - is_speaking: True if in active speech state
                - end_of_speech: True if speech just ended
                - speech_duration: Duration of current speech segment
        """
        if not self._running:
            return {"is_speech": False, "is_speaking": False, "end_of_speech": False}

        current_time = time.time()

        # Convert to int16 bytes for WebRTC VAD
        audio_int16 = (audio_chunk * 32767).astype(np.int16)

        # Process in frame_duration_ms chunks
        is_speech_frames = []
        for i in range(0, len(audio_int16) - self.frame_size + 1, self.frame_size):
            frame = audio_int16[i : i + self.frame_size]
            frame_bytes = frame.tobytes()
            try:
                is_speech = self._vad.is_speech(frame_bytes, self.sample_rate)
                is_speech_frames.append(is_speech)
            except Exception as e:
                logger.warning("vad_error", error=str(e))
                is_speech_frames.append(False)

        # Aggregate: consider speech if any frame has speech
        is_speech = any(is_speech_frames) if is_speech_frames else False

        # Store in ring buffer for padding
        self._ring_buffer.append((audio_chunk.copy(), is_speech))

        # State machine
        end_of_speech = False
        speech_duration = 0.0

        if is_speech:
            self._last_speech_time = current_time

            if not self._is_speaking:
                # Possible start of speech
                if self._speech_start_time is None:
                    self._speech_start_time = current_time
                elif current_time - self._speech_start_time >= self.min_speech_duration:
                    # Enough speech to trigger
                    self._is_speaking = True
                    self._triggered = True
                    logger.debug("speech_started")
        else:
            # No speech in this chunk
            if self._is_speaking and self._last_speech_time:
                silence_duration = current_time - self._last_speech_time
                if silence_duration >= self.max_silence_duration:
                    # End of speech
                    end_of_speech = True
                    speech_duration = current_time - (self._speech_start_time or current_time)
                    logger.debug(
                        "speech_ended",
                        duration=round(speech_duration, 2),
                        silence=round(silence_duration, 2),
                    )
                    self.reset()
            elif not self._is_speaking:
                # Reset if never triggered
                self._speech_start_time = None

        return {
            "is_speech": is_speech,
            "is_speaking": self._is_speaking,
            "end_of_speech": end_of_speech,
            "speech_duration": speech_duration,
        }

    def get_padding_audio(self) -> np.ndarray | None:
        """Get buffered audio from before speech started."""
        if not self._ring_buffer:
            return None
        return np.concatenate([chunk for chunk, _ in self._ring_buffer])

    @property
    def is_running(self) -> bool:
        """Check if VAD is running."""
        return self._running

    @property
    def is_speaking(self) -> bool:
        """Check if currently in speech state."""
        return self._is_speaking
