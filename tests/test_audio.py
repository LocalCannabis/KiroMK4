"""
Tests for Kiro audio pipeline components.

Note: These tests mock hardware dependencies (microphone, API calls).
Integration testing with real hardware is done manually.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# Skip audio tests if sounddevice can't load (e.g., missing libportaudio)
try:
    from kiro.audio.capture import AudioCapture
    from kiro.audio.vad import VoiceActivityDetector
    from kiro.audio.pipeline import AudioPipeline, PipelineState
    AUDIO_AVAILABLE = True
except OSError as e:
    AUDIO_AVAILABLE = False
    AudioCapture = None
    VoiceActivityDetector = None
    AudioPipeline = None
    PipelineState = None

from kiro.events import EventBus

pytestmark = pytest.mark.skipif(
    not AUDIO_AVAILABLE,
    reason="Audio libraries not available (libportaudio missing)"
)


class TestAudioCapture:
    """Tests for AudioCapture module."""

    def test_init_defaults(self):
        capture = AudioCapture()
        assert capture.sample_rate == 16000
        assert capture.channels == 1
        assert capture.chunk_duration == 0.1
        assert capture.chunk_size == 1600

    def test_init_custom(self):
        capture = AudioCapture(
            sample_rate=8000,
            channels=2,
            chunk_duration=0.05,
        )
        assert capture.sample_rate == 8000
        assert capture.channels == 2
        assert capture.chunk_size == 400

    @pytest.mark.asyncio
    async def test_not_running_initially(self):
        capture = AudioCapture()
        assert not capture.is_running

    @pytest.mark.asyncio
    async def test_get_chunk_when_not_running(self):
        capture = AudioCapture()
        result = await capture.get_chunk(timeout=0.1)
        assert result is None


class TestVoiceActivityDetector:
    """Tests for VAD module."""

    def test_init_defaults(self):
        vad = VoiceActivityDetector()
        assert vad.sample_rate == 16000
        assert vad.frame_duration_ms == 30
        assert vad.min_speech_duration == 0.25
        assert vad.max_silence_duration == 0.8

    def test_invalid_sample_rate(self):
        with pytest.raises(ValueError, match="Sample rate must be"):
            VoiceActivityDetector(sample_rate=22050)

    def test_invalid_frame_duration(self):
        with pytest.raises(ValueError, match="Frame duration must be"):
            VoiceActivityDetector(frame_duration_ms=25)

    @pytest.mark.asyncio
    async def test_start_stop(self):
        vad = VoiceActivityDetector()
        await vad.start()
        assert vad.is_running
        await vad.stop()
        assert not vad.is_running

    @pytest.mark.asyncio
    async def test_reset(self):
        vad = VoiceActivityDetector()
        await vad.start()
        vad._is_speaking = True
        vad.reset()
        assert not vad._is_speaking
        assert vad._speech_start_time is None
        await vad.stop()

    @pytest.mark.asyncio
    async def test_process_silence(self):
        vad = VoiceActivityDetector()
        await vad.start()

        # Generate silence (very low amplitude noise)
        silence = np.random.randn(1600).astype(np.float32) * 0.001

        result = vad.process(silence)
        assert result["is_speech"] is False
        assert result["is_speaking"] is False
        assert result["end_of_speech"] is False

        await vad.stop()


class TestAudioPipeline:
    """Tests for AudioPipeline orchestrator."""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    def test_init(self, event_bus):
        pipeline = AudioPipeline(event_bus=event_bus)
        assert pipeline.state == PipelineState.IDLE
        assert not pipeline.is_running

    @pytest.mark.asyncio
    async def test_start_stop_mocked(self, event_bus):
        """Test pipeline start/stop with mocked components."""
        pipeline = AudioPipeline(event_bus=event_bus)

        # Mock all component start/stop methods
        pipeline._capture.start = AsyncMock()
        pipeline._capture.stop = AsyncMock()
        pipeline._wake_word.start = AsyncMock()
        pipeline._wake_word.stop = AsyncMock()
        pipeline._vad.start = AsyncMock()
        pipeline._vad.stop = AsyncMock()
        pipeline._stt.start = AsyncMock()
        pipeline._stt.stop = AsyncMock()

        # Mock the capture to not actually run
        pipeline._capture.get_chunk = AsyncMock(return_value=None)

        await pipeline.start()
        assert pipeline.is_running

        # Give it a moment to start the task
        await asyncio.sleep(0.1)

        await pipeline.stop()
        assert not pipeline.is_running

        # Verify all components were started
        pipeline._capture.start.assert_called_once()
        pipeline._wake_word.start.assert_called_once()
        pipeline._vad.start.assert_called_once()
        pipeline._stt.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_state_transitions(self, event_bus):
        """Test state machine transitions."""
        pipeline = AudioPipeline(event_bus=event_bus)
        
        # Initial state
        assert pipeline.state == PipelineState.IDLE

        # Simulate internal state changes
        pipeline._state = PipelineState.LISTENING
        assert pipeline.state == PipelineState.LISTENING

        pipeline._state = PipelineState.PROCESSING
        assert pipeline.state == PipelineState.PROCESSING


class TestAudioConfig:
    """Tests for audio configuration."""

    def test_audio_config_defaults(self):
        from kiro.config import AudioConfig

        config = AudioConfig()
        assert config.enabled is True
        assert config.sample_rate == 16000
        assert config.channels == 1
        assert config.wake_word.threshold == 0.5
        assert config.vad.aggressiveness == 2
        assert config.stt.model == "whisper-1"

    def test_audio_config_in_main_config(self):
        from kiro.config import KiroConfig

        config = KiroConfig()
        assert hasattr(config, "audio")
        assert config.audio.enabled is True
