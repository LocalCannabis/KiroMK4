"""
audio/tts.py — Multi-engine Text-to-Speech for Kiro

Engine priority:
  1. Kokoro-82M  (primary: 24kHz, 54 voices, Apache licensed, CPU/GPU)
  2. Piper       (fallback: 22050Hz, offline, Pi-safe)

Kokoro voices used per persona:
  kiro    → af_heart   (warm neutral American female)
  ops     → am_adam    (flat efficient American male)
  sage    → bm_george  (intellectual British male)
  finley  → bm_lewis   (measured authoritative British male)
  coach   → am_michael (energetic American male)
  chef    → bf_emma    (warm British female)
  doc     → af_nicole  (gentle soft American female)

Chatterbox is intentionally deferred: the PyPI package fails to build on
Python 3.12 (pkgutil.ImpImporter removed). Add it when a 3.12-compatible
release ships or if you switch the env to 3.10.
"""

from __future__ import annotations

import io
import logging
import subprocess
import wave
from typing import Any, Dict, Iterator, Optional

import numpy as np


def _play_wav_bytes(wav_bytes: bytes, alsa_device: str) -> None:
    """Write a complete in-memory WAV file to aplay via stdin."""
    proc = subprocess.Popen(
        ["aplay", "-D", alsa_device, "-q", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.stdin.write(wav_bytes)
    proc.stdin.close()
    proc.wait()


def _numpy_to_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    """Convert float32 numpy audio [-1, 1] to a WAV bytes buffer."""
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Kokoro engine
# ---------------------------------------------------------------------------

class _KokoroEngine:
    """
    Wraps hexgrad/Kokoro-82M via the `kokoro` Python package.

    Model weights (~327MB) and voices are downloaded on first use from
    HuggingFace into ~/.cache/huggingface/hub/ — subsequent starts are
    instant (cached).

    Sample rate: 24000 Hz
    GPU: uses torch.cuda if available (no ctranslate2 — no cuDNN crash)
    """

    SAMPLE_RATE = 24000

    def __init__(
        self,
        voice_map: Dict[str, str],
        default_persona: str,
        device: str,
        speed: float,
        logger: logging.Logger,
    ) -> None:
        self.logger = logger
        self.voice_map = voice_map       # persona → kokoro voice id
        self.default_persona = default_persona
        self.speed = speed

        self.logger.info("Loading Kokoro-82M on device=%s ...", device)
        from kokoro import KPipeline
        # KPipeline downloads model weights on first run; cached after
        self._pipeline = KPipeline(lang_code="a", device=device)
        self.logger.info("Kokoro loaded.")

    def synthesize(self, text: str, persona: str) -> np.ndarray:
        """Return float32 audio array at SAMPLE_RATE Hz."""
        voice = self.voice_map.get(persona, self.voice_map.get(self.default_persona, "af_heart"))
        audio_chunks = []
        for result in self._pipeline(text, voice=voice, speed=self.speed):
            if result.audio is not None:
                audio_chunks.append(result.audio.numpy())
        if not audio_chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(audio_chunks).astype(np.float32)


# ---------------------------------------------------------------------------
# Piper engine (fallback / Pi)
# ---------------------------------------------------------------------------

class _PiperEngine:
    """
    Wraps the Piper binary via subprocess.
    Uses --output-raw (raw S16_LE PCM) piped directly to aplay — no temp files.
    Sample rate: configured per voice model (typically 22050 Hz).
    """

    def __init__(
        self,
        voice_map: Dict[str, str],
        default_persona: str,
        piper_bin: str,
        sample_rate: int,
        alsa_device: str,
        logger: logging.Logger,
    ) -> None:
        self.logger = logger
        self.voice_map = voice_map
        self.default_persona = default_persona
        self.piper_bin = piper_bin
        self.sample_rate = sample_rate
        self.alsa_device = alsa_device

    def speak_sentence(self, text: str, persona: str) -> None:
        voice_path = self.voice_map.get(persona, self.voice_map.get(self.default_persona))
        piper_cmd = [self.piper_bin, "--model", voice_path, "--output-raw"]
        aplay_cmd = [
            "aplay", "-D", self.alsa_device,
            "-f", "S16_LE", "-r", str(self.sample_rate), "-c", "1", "-q",
        ]
        piper_proc = subprocess.Popen(
            piper_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        aplay_proc = subprocess.Popen(
            aplay_cmd, stdin=piper_proc.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        piper_proc.stdout.close()
        piper_proc.stdin.write(text.encode("utf-8"))
        piper_proc.stdin.close()
        piper_proc.wait()
        aplay_proc.wait()


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------

class TTSEngine:
    """
    Engine-agnostic TTS facade used by KiroOrchestrator.

    Tries Kokoro first. If Kokoro is unavailable (model not yet downloaded,
    import error, etc.), falls back to Piper transparently.
    """

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        tts_cfg = cfg.get("tts", {})
        self.alsa_device = cfg.get("audio", {}).get("output", {}).get("alsa_device", "default")

        engine_pref = tts_cfg.get("engine", "kokoro")

        # Piper config (always available as fallback)
        piper_voice_map: Dict[str, str] = tts_cfg.get("piper_voice_map", {})
        default_persona: str = tts_cfg.get("default_persona", "kiro")
        piper_bin: str = tts_cfg.get("piper", {}).get("executable", "piper")
        piper_sr: int = int(tts_cfg.get("piper", {}).get("output_sample_rate", 22050))

        self._piper = _PiperEngine(
            voice_map=piper_voice_map,
            default_persona=default_persona,
            piper_bin=piper_bin,
            sample_rate=piper_sr,
            alsa_device=self.alsa_device,
            logger=logger,
        )

        # Kokoro config
        self._kokoro: Optional[_KokoroEngine] = None
        if engine_pref == "kokoro":
            kokoro_cfg = tts_cfg.get("kokoro", {})
            kokoro_voice_map: Dict[str, str] = kokoro_cfg.get("voice_map", {})
            kokoro_device: str = kokoro_cfg.get("device", "cpu")
            kokoro_speed: float = float(kokoro_cfg.get("speed", 1.0))
            try:
                self._kokoro = _KokoroEngine(
                    voice_map=kokoro_voice_map,
                    default_persona=default_persona,
                    device=kokoro_device,
                    speed=kokoro_speed,
                    logger=logger,
                )
                self.logger.info("TTS engine: Kokoro-82M")
            except Exception as exc:
                self.logger.warning(
                    "Kokoro unavailable (%s). Falling back to Piper.", exc
                )

        if self._kokoro is None:
            self.logger.info("TTS engine: Piper (fallback)")

        self.default_persona = default_persona

    def speak_stream(self, sentences: Iterator[str], persona: Optional[str] = None) -> str:
        """
        Speak each sentence as soon as it arrives from the LLM stream.
        Returns the full spoken text.
        """
        persona = persona or self.default_persona
        self.logger.info("TTS streaming start (persona=%s)", persona)
        t_start = __import__("time").perf_counter()
        spoken: list[str] = []

        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue
            spoken.append(sentence)
            t_sent = __import__("time").perf_counter()
            self.logger.info(
                "TTS sentence %d queued at %.0fms → %s",
                i + 1,
                (t_sent - t_start) * 1000,
                sentence,
            )
            self._speak_sentence(sentence, persona)

        elapsed = __import__("time").perf_counter() - t_start
        self.logger.info("TTS streaming done in %.1fs", elapsed)
        return " ".join(spoken)

    def speak(self, text: str, persona: Optional[str] = None) -> None:
        """Speak a complete pre-assembled string (non-streaming)."""
        if not text:
            return
        persona = persona or self.default_persona
        self._speak_sentence(text, persona)

    def _speak_sentence(self, text: str, persona: str) -> None:
        if self._kokoro is not None:
            try:
                audio = self._kokoro.synthesize(text, persona)
                wav_bytes = _numpy_to_wav(audio, _KokoroEngine.SAMPLE_RATE)
                _play_wav_bytes(wav_bytes, self.alsa_device)
                return
            except Exception as exc:
                self.logger.warning("Kokoro synthesis failed (%s); using Piper.", exc)
        # Piper fallback
        self._piper.speak_sentence(text, persona)
