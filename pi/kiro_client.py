#!/usr/bin/env python3
"""
kiro_client.py — Pi-side thin client for Kiro voice assistant.

Captures audio from the microphone, detects speech via Silero VAD,
sends speech audio to the Beast for processing, and plays back the
synthesized response.

Architecture:
    Pi mic → VAD → audio chunk → POST /process → Beast → WAV response → Pi speaker

State machine:
    IDLE → (VAD speech) → LISTENING → (silence) → PROCESSING → (response) → PLAYING → IDLE

Usage:
    python kiro_client.py [--config kiro_client_config.yaml]
"""

from __future__ import annotations

import argparse
import io
import logging
import logging.handlers
import os
import queue
import signal
import sys
import time
import uuid
import wave
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import yaml

try:
    import sounddevice as sd
except ImportError:
    print("FATAL: sounddevice not installed. Run: pip install sounddevice")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("FATAL: requests not installed. Run: pip install requests")
    sys.exit(1)

# Silero VAD (requires torch)
_vad_available = False
try:
    import torch
    from silero_vad import load_silero_vad
    _vad_available = True
except ImportError:
    torch = None
    load_silero_vad = None


# ============================================================================
# State Machine
# ============================================================================
class State(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    PLAYING = auto()
    ERROR = auto()
    SHUTDOWN = auto()


# ============================================================================
# Configuration & Logging
# ============================================================================
def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: Dict[str, Any]) -> logging.Logger:
    log_cfg = cfg.get("logging", {})
    log_level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)

    logger = logging.getLogger("kiro-client")
    logger.setLevel(log_level)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_file = log_cfg.get("file")
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5_242_880, backupCount=3, encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


# ============================================================================
# Audio Utilities
# ============================================================================
def resolve_device(device_spec, kind: str = "input") -> Optional[int]:
    """Resolve device specifier (None, int, str) → device index or None."""
    if device_spec is None:
        return None
    if isinstance(device_spec, int):
        return device_spec
    if isinstance(device_spec, str):
        if device_spec.isdigit():
            return int(device_spec)
        # Search by name substring
        devices = sd.query_devices()
        ch_key = "max_input_channels" if kind == "input" else "max_output_channels"
        for i, dev in enumerate(devices):
            if device_spec.lower() in dev["name"].lower() and dev[ch_key] > 0:
                return i
    return None


def pcm_to_wav(pcm_int16: np.ndarray, sample_rate: int, channels: int = 1) -> bytes:
    """Encode int16 numpy array → WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16.tobytes())
    return buf.getvalue()


def wav_to_playable(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV bytes → (float32 numpy array, sample_rate) for playback."""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        pcm = np.frombuffer(raw, dtype=np.int16)
        audio = pcm.astype(np.float32) / 32768.0
    elif sampwidth == 4:
        pcm = np.frombuffer(raw, dtype=np.int32)
        audio = pcm.astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    if n_ch > 1:
        audio = audio.reshape(-1, n_ch)
    else:
        audio = audio.reshape(-1, 1)

    return audio, sr


# ============================================================================
# VAD (Voice Activity Detection)
# ============================================================================
class VADDetector:
    """Silero VAD wrapper with energy-based fallback."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        self.threshold = float(cfg.get("threshold", 0.5))
        self.model = None

        if _vad_available and load_silero_vad is not None:
            self.logger.info("Loading Silero VAD model...")
            self.model = load_silero_vad()
            self.logger.info("Silero VAD loaded (threshold=%.2f).", self.threshold)
        else:
            self.logger.warning(
                "Silero VAD unavailable (torch not installed). "
                "Falling back to energy-based detection."
            )

    def detect(self, pcm_int16: np.ndarray, sample_rate: int) -> tuple[bool, float]:
        """Returns (is_speech, confidence_score)."""
        if self.model is not None:
            audio = pcm_int16.astype(np.float32) / 32768.0
            audio_t = torch.from_numpy(audio)
            score = float(self.model(audio_t, sample_rate).item())
            return score >= self.threshold, score

        # Energy-based fallback
        audio = pcm_int16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio * audio) + 1e-12))
        return rms >= 0.015, rms

    def reset(self) -> None:
        """Reset VAD internal state between utterances."""
        if self.model is not None and hasattr(self.model, "reset_states"):
            self.model.reset_states()


# ============================================================================
# Beast Connection
# ============================================================================
class BeastConnection:
    """HTTP client for communicating with the Beast API server."""

    def __init__(self, cfg: Dict[str, Any], session_cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        beast_cfg = cfg.get("beast", {})
        conn_cfg = cfg.get("connection", {})

        self.host = beast_cfg.get("host", "100.0.0.1")
        self.port = int(beast_cfg.get("port", 5400))
        self.base_url = f"http://{self.host}:{self.port}"
        self.health_interval = int(beast_cfg.get("health_check_interval_s", 60))

        self.timeout = int(conn_cfg.get("timeout_s", 30))
        self.retry_attempts = int(conn_cfg.get("retry_attempts", 3))
        self.retry_delay = float(conn_cfg.get("retry_delay_s", 1))
        self.reconnect_max_delay = int(conn_cfg.get("reconnect_max_delay_s", 60))

        # Session
        self.session_id = session_cfg.get("id") or str(uuid.uuid4())
        self.persona = session_cfg.get("persona")  # None = auto-route on Beast

        self._http = requests.Session()
        self._connected = False
        self._last_health_check = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    def check_health(self) -> bool:
        """Ping the Beast health endpoint. Updates connected state."""
        try:
            resp = self._http.get(
                f"{self.base_url}/health", timeout=5,
            )
            data = resp.json()
            self._connected = data.get("status") == "ok"
            if self._connected:
                self.logger.debug("Beast health OK: %s", data)
            return self._connected
        except Exception as e:
            self._connected = False
            self.logger.warning("Beast health check failed: %s", e)
            return False

    def maybe_health_check(self) -> None:
        """Run a health check if enough time has passed."""
        now = time.time()
        if now - self._last_health_check >= self.health_interval:
            self.check_health()
            self._last_health_check = now

    def send_audio(self, wav_bytes: bytes) -> Optional[Dict[str, Any]]:
        """
        Send audio to Beast /process endpoint.

        Returns dict with keys: audio_bytes, transcript, response_text, persona, timing
        Returns None on failure.
        """
        headers = {
            "Content-Type": "audio/wav",
            "X-Session-Id": self.session_id,
        }
        if self.persona:
            headers["X-Persona"] = self.persona

        last_error = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                t0 = time.perf_counter()
                resp = self._http.post(
                    f"{self.base_url}/process",
                    data=wav_bytes,
                    headers=headers,
                    timeout=self.timeout,
                )

                if resp.status_code == 200 and "audio" in resp.headers.get("Content-Type", ""):
                    network_ms = (time.perf_counter() - t0) * 1000
                    self._connected = True

                    result = {
                        "audio_bytes": resp.content,
                        "transcript": resp.headers.get("X-Transcript", ""),
                        "response_text": resp.headers.get("X-Response-Text", ""),
                        "persona": resp.headers.get("X-Persona", "kiro"),
                        "timing": resp.headers.get("X-Timing", "{}"),
                        "network_ms": network_ms,
                    }
                    return result

                # Non-audio response (error)
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", f"HTTP {resp.status_code}")
                except Exception:
                    error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"

                self.logger.error("Beast returned error (attempt %d/%d): %s",
                                  attempt, self.retry_attempts, error_msg)
                last_error = error_msg

                # Don't retry on client errors (4xx)
                if 400 <= resp.status_code < 500:
                    break

            except requests.exceptions.Timeout:
                self.logger.warning("Beast request timed out (attempt %d/%d)",
                                    attempt, self.retry_attempts)
                last_error = "timeout"
            except requests.exceptions.ConnectionError as e:
                self._connected = False
                self.logger.warning("Beast connection error (attempt %d/%d): %s",
                                    attempt, self.retry_attempts, e)
                last_error = str(e)
            except Exception as e:
                self.logger.error("Unexpected error sending to Beast: %s", e)
                last_error = str(e)
                break

            if attempt < self.retry_attempts:
                time.sleep(self.retry_delay * attempt)

        self.logger.error("All %d attempts to Beast failed. Last error: %s",
                          self.retry_attempts, last_error)
        return None

    def wait_for_connection(self) -> None:
        """Block until Beast is reachable (exponential backoff)."""
        delay = 1
        self.logger.info("Waiting for Beast at %s...", self.base_url)
        while True:
            if self.check_health():
                self.logger.info("Beast connected! ✓")
                return
            self.logger.info("Beast unreachable. Retrying in %ds...", delay)
            time.sleep(delay)
            delay = min(delay * 2, self.reconnect_max_delay)


# ============================================================================
# Kiro Client (main state machine)
# ============================================================================
class KiroClient:
    """
    Main voice client. Runs the state machine:
    IDLE → LISTENING → PROCESSING → PLAYING → IDLE
    """

    def __init__(self, config_path: str) -> None:
        self.cfg = load_config(config_path)
        self.logger = setup_logging(self.cfg)

        # Audio config
        audio_cfg = self.cfg.get("audio", {})
        self.sample_rate = int(audio_cfg.get("sample_rate", 16000))
        self.channels = int(audio_cfg.get("channels", 1))
        self.block_size = int(audio_cfg.get("block_size", 512))
        self.input_device = resolve_device(audio_cfg.get("input_device"), "input")
        self.output_device = resolve_device(audio_cfg.get("output_device"), "output")

        # VAD config
        vad_cfg = self.cfg.get("vad", {})
        self.vad = VADDetector(vad_cfg, self.logger)
        self.start_trigger_frames = int(vad_cfg.get("start_trigger_frames", 6))
        self.silence_duration_ms = int(vad_cfg.get("silence_duration_ms", 800))
        self.pre_buffer_ms = int(vad_cfg.get("pre_buffer_ms", 300))
        self.min_speech_ms = int(vad_cfg.get("min_speech_ms", 500))
        self.max_utterance_s = float(vad_cfg.get("max_utterance_s", 20))

        # Derived VAD parameters
        self.block_ms = self.block_size / self.sample_rate * 1000  # ~32ms
        self.silence_frames = max(1, int(self.silence_duration_ms / self.block_ms))
        self.pre_buffer_frames = max(1, int(self.pre_buffer_ms / self.block_ms))
        self.min_speech_frames = max(1, int(self.min_speech_ms / self.block_ms))
        self.max_frames = int(self.max_utterance_s * 1000 / self.block_ms)

        # Beast connection
        session_cfg = self.cfg.get("session", {})
        self.beast = BeastConnection(self.cfg, session_cfg, self.logger)

        # Feedback config
        self.verbose = self.cfg.get("feedback", {}).get("verbose", True)

        # State
        self.state = State.IDLE
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self._running = True
        self._stream: Optional[sd.InputStream] = None

    # --- State transitions ---
    def _set_state(self, new_state: State) -> None:
        if new_state != self.state:
            old = self.state
            self.state = new_state
            if self.verbose:
                indicator = {
                    State.IDLE: "💤 IDLE",
                    State.LISTENING: "🎤 LISTENING",
                    State.PROCESSING: "⚡ PROCESSING",
                    State.PLAYING: "🔊 PLAYING",
                    State.ERROR: "❌ ERROR",
                    State.SHUTDOWN: "🛑 SHUTDOWN",
                }
                self.logger.info("State: %s → %s", old.name, indicator.get(new_state, new_state.name))

    # --- Audio capture callback ---
    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for each audio block."""
        if status:
            self.logger.debug("Audio status: %s", status)
        self._audio_queue.put(indata[:, 0].copy())  # Mono channel

    # --- Main loop ---
    def run(self) -> None:
        """Run the voice client forever."""
        self.logger.info("=" * 60)
        self.logger.info("Kiro Pi Voice Client starting")
        self.logger.info("  Beast: %s:%d", self.beast.host, self.beast.port)
        self.logger.info("  Session: %s", self.beast.session_id)
        self.logger.info("  Sample rate: %dHz, Block: %d samples (%.0fms)",
                         self.sample_rate, self.block_size, self.block_ms)
        self.logger.info("  Input device: %s", self.input_device or "default")
        self.logger.info("  Output device: %s", self.output_device or "default")
        self.logger.info("=" * 60)

        # Wait for Beast
        self.beast.wait_for_connection()

        # Open audio input stream
        self.logger.info("Opening audio input stream...")
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=self.block_size,
                device=self.input_device,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            self.logger.error("Failed to open audio input: %s", e)
            self.logger.error("Run `python audio_test.py` to check your audio devices.")
            return

        self.logger.info("Audio stream open. Listening for speech...")
        self._set_state(State.IDLE)

        try:
            while self._running and self.state != State.SHUTDOWN:
                if self.state == State.IDLE:
                    self._handle_idle()
                elif self.state == State.LISTENING:
                    self._handle_listening()
                elif self.state == State.PROCESSING:
                    self._handle_processing()
                elif self.state == State.PLAYING:
                    self._handle_playing()
                elif self.state == State.ERROR:
                    self._handle_error()

        except KeyboardInterrupt:
            self.logger.info("Interrupted by user.")
        finally:
            self._cleanup()

    def _handle_idle(self) -> None:
        """Wait for VAD to detect speech onset."""
        speech_count = 0
        pre_buffer: List[np.ndarray] = []

        # Periodic health checks
        self.beast.maybe_health_check()

        while self.state == State.IDLE and self._running:
            try:
                chunk = self._audio_queue.get(timeout=1.0)
            except queue.Empty:
                self.beast.maybe_health_check()
                continue

            is_speech, score = self.vad.detect(chunk, self.sample_rate)

            # Maintain rolling pre-buffer
            pre_buffer.append(chunk)
            if len(pre_buffer) > self.pre_buffer_frames:
                pre_buffer.pop(0)

            if is_speech:
                speech_count += 1
            else:
                speech_count = max(0, speech_count - 1)

            if speech_count >= self.start_trigger_frames:
                self.logger.info("Speech detected (VAD=%.3f)", score)
                # Transfer pre-buffer to the listening phase
                self._speech_buffer = list(pre_buffer)
                self._set_state(State.LISTENING)
                return

    def _handle_listening(self) -> None:
        """Buffer audio until silence is detected."""
        silence_count = 0
        frame_count = len(self._speech_buffer)
        t_start = time.perf_counter()

        while self.state == State.LISTENING and self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._speech_buffer.append(chunk)
            frame_count += 1

            # Max utterance guard
            if frame_count >= self.max_frames:
                self.logger.warning("Max utterance length reached (%ds).", self.max_utterance_s)
                break

            is_speech, score = self.vad.detect(chunk, self.sample_rate)

            if is_speech:
                silence_count = 0
            else:
                silence_count += 1

            if silence_count >= self.silence_frames:
                duration_ms = (time.perf_counter() - t_start) * 1000
                self.logger.info("End of speech (%.0fms, %d frames).", duration_ms, frame_count)
                break

        # Check minimum speech length
        speech_frames = len(self._speech_buffer)
        if speech_frames < self.min_speech_frames:
            self.logger.debug("Speech too short (%d frames < %d min). Ignoring.",
                              speech_frames, self.min_speech_frames)
            self.vad.reset()
            self._set_state(State.IDLE)
            return

        # Package audio and move to processing
        self._pending_audio = np.concatenate(self._speech_buffer).astype(np.int16)
        self.vad.reset()
        self._set_state(State.PROCESSING)

    def _handle_processing(self) -> None:
        """Send audio to Beast and wait for response."""
        # Drain audio queue to prevent stale audio buildup
        self._drain_audio_queue()

        # Encode to WAV
        wav_bytes = pcm_to_wav(self._pending_audio, self.sample_rate, self.channels)
        audio_size_kb = len(wav_bytes) / 1024
        self.logger.info("Sending %.1f KB audio to Beast...", audio_size_kb)

        t_send = time.perf_counter()
        result = self.beast.send_audio(wav_bytes)
        round_trip_ms = (time.perf_counter() - t_send) * 1000

        if result is None:
            self.logger.error("Beast processing failed.")
            self._set_state(State.ERROR)
            return

        # Log results
        transcript = result.get("transcript", "")
        response_text = result.get("response_text", "")
        persona = result.get("persona", "kiro")
        timing_str = result.get("timing", "{}")

        self.logger.info("📝 User: %s", transcript)
        self.logger.info("💬 [%s]: %s", persona, response_text)

        # Parse and log latency breakdown
        try:
            import json
            timing = json.loads(timing_str)
            self.logger.info(
                "[LATENCY] stt=%dms llm=%dms tts=%dms beast_total=%dms network_roundtrip=%.0fms",
                timing.get("stt_ms", 0),
                timing.get("llm_ms", 0),
                timing.get("tts_ms", 0),
                timing.get("total_ms", 0),
                round_trip_ms,
            )
        except Exception:
            self.logger.info("[LATENCY] round_trip=%.0fms", round_trip_ms)

        # Store response audio for playback
        self._response_audio = result["audio_bytes"]
        self._set_state(State.PLAYING)

    def _handle_playing(self) -> None:
        """Play back the response audio. Mic is effectively muted (queue drained)."""
        try:
            audio, sr = wav_to_playable(self._response_audio)
            self.logger.info("Playing response (%d samples, %dHz)...", len(audio), sr)

            sd.play(audio, samplerate=sr, device=self.output_device)
            sd.wait()  # Block until playback completes

            # Drain audio captured during playback (echo cancellation)
            self._drain_audio_queue()
            self.logger.info("Playback complete.")

        except Exception as e:
            self.logger.error("Playback failed: %s", e)

        self._set_state(State.IDLE)

    def _handle_error(self) -> None:
        """Handle errors gracefully. Brief pause then back to idle."""
        self._drain_audio_queue()

        if not self.beast.connected:
            self.logger.info("Lost connection to Beast. Attempting reconnect...")
            self.beast.wait_for_connection()

        time.sleep(1)  # Brief pause before resuming
        self._set_state(State.IDLE)

    # --- Helpers ---
    def _drain_audio_queue(self) -> None:
        """Discard all pending audio chunks (prevents echo / stale data)."""
        count = 0
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                count += 1
            except queue.Empty:
                break
        if count > 0:
            self.logger.debug("Drained %d audio chunks from queue.", count)

    def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self.logger.info("Kiro client shut down.")

    def shutdown(self) -> None:
        """Request graceful shutdown."""
        self._running = False
        self._set_state(State.SHUTDOWN)


# ============================================================================
# Entry Point
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Kiro Pi Voice Client")
    parser.add_argument(
        "--config",
        default="kiro_client_config.yaml",
        help="Path to client config file",
    )
    args = parser.parse_args()

    client = KiroClient(args.config)

    # Handle signals for clean shutdown
    def signal_handler(sig, frame):
        client.logger.info("Received signal %s, shutting down...", sig)
        client.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    client.run()


if __name__ == "__main__":
    main()
