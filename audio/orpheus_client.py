"""
audio/orpheus_client.py — HTTP client for the Orpheus TTS FastAPI server.

Calls the OpenAI-compatible /v1/audio/speech endpoint running locally
(default: http://localhost:5005).

Per ORPHEUS_TTS_INTEGRATION_SPEC.md §7.

Environment variables (from .env or ~/.kiro/ambient.env):
  ORPHEUS_API_URL          — base URL of Orpheus FastAPI server (default: http://localhost:5005)
  ORPHEUS_MODEL_NAME       — GGUF model filename passed to the API
  ORPHEUS_DEFAULT_VOICE    — fallback built-in voice if DB unavailable
  ORPHEUS_LEAD_PAD_MS      — silence prepended to each chunk (default: 50ms)
  ORPHEUS_TRAIL_PAD_MS     — silence appended to each chunk (default: 150ms)
"""

from __future__ import annotations

import io
import logging
import os
import wave
from typing import Iterator, Optional

import numpy as np
import requests

from audio.emotion_tagger import tag_response, get_voice_config

log = logging.getLogger(__name__)

ORPHEUS_API_URL = os.getenv("ORPHEUS_API_URL", "http://localhost:5005")
ORPHEUS_MODEL   = os.getenv("ORPHEUS_MODEL_NAME", "orpheus-tts-0.1-finetune-prod-Q8_0.gguf")
ORPHEUS_DEFAULT_VOICE = os.getenv("ORPHEUS_DEFAULT_VOICE", "leah")
LEAD_PAD_MS  = int(os.getenv("ORPHEUS_LEAD_PAD_MS",  "50"))
TRAIL_PAD_MS = int(os.getenv("ORPHEUS_TRAIL_PAD_MS", "150"))

SAMPLE_RATE = 24000  # Orpheus outputs 24kHz mono WAV


# ── Helpers ─────────────────────────────────────────────────────────────────

def _silence_wav(ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Return a WAV bytes buffer of silence for the given duration."""
    n_samples = int(sample_rate * ms / 1000)
    silence = np.zeros(n_samples, dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(silence.tobytes())
    return buf.getvalue()


def _wav_to_numpy(wav_bytes: bytes) -> Optional[np.ndarray]:
    """Parse a WAV bytes buffer and return float32 audio array."""
    try:
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
        return audio
    except Exception as exc:
        log.warning("orpheus_client: WAV decode failed (%s)", exc)
        return None


# ── Public API ───────────────────────────────────────────────────────────────

def generate_speech(persona: str, text: str) -> bytes:
    """
    Generate speech audio for a given persona and text.

    1. Runs text through emotion_tagger to inject tags + voice prefix.
    2. POSTs to Orpheus /v1/audio/speech.
    3. Returns raw WAV bytes with lead/trail silence padding.

    Raises requests.HTTPError on API failure.
    """
    if not text.strip():
        return _silence_wav(50)

    config = get_voice_config(persona)
    tagged_input = tag_response(persona, text)

    voice = config.get("orpheus_voice", ORPHEUS_DEFAULT_VOICE)

    payload: dict = {
        "model": ORPHEUS_MODEL,
        "input": tagged_input,
        "voice": voice,
        "response_format": "wav",
        "speed": config.get("speed", 1.0),
    }

    log.debug("orpheus_client: POST %s/v1/audio/speech voice=%s len=%d",
              ORPHEUS_API_URL, voice, len(text))

    resp = requests.post(
        f"{ORPHEUS_API_URL}/v1/audio/speech",
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()

    # Pad with silence to smooth playback transitions
    raw_wav = resp.content
    lead  = _silence_wav(LEAD_PAD_MS)
    trail = _silence_wav(TRAIL_PAD_MS)

    # Merge: strip WAV headers from middle chunks and rebuild a single WAV
    return _merge_wav_segments([lead, raw_wav, trail])


def generate_speech_stream(persona: str, text: str) -> Iterator[bytes]:
    """
    Streaming variant — yields WAV chunks as they arrive from Orpheus.
    Uses requests streaming; each yielded item is a raw bytes chunk
    (not a complete WAV — caller is responsible for playback).
    Falls back to generate_speech() if streaming is not available.
    """
    if not text.strip():
        yield _silence_wav(50)
        return

    config = get_voice_config(persona)
    tagged_input = tag_response(persona, text)

    payload: dict = {
        "model": ORPHEUS_MODEL,
        "input": tagged_input,
        "voice": config.get("orpheus_voice", ORPHEUS_DEFAULT_VOICE),
        "response_format": "wav",
        "speed": config.get("speed", 1.0),
    }

    try:
        with requests.post(
            f"{ORPHEUS_API_URL}/v1/audio/speech",
            json=payload,
            stream=True,
            timeout=60,
        ) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
    except Exception as exc:
        log.warning("orpheus_client: streaming failed (%s), falling back", exc)
        yield generate_speech(persona, text)


def health_check() -> bool:
    """Return True if the Orpheus API server is reachable."""
    try:
        resp = requests.get(f"{ORPHEUS_API_URL}/v1/audio/voices", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


# ── WAV stitching ────────────────────────────────────────────────────────────

def _merge_wav_segments(segments: list[bytes]) -> bytes:
    """Merge multiple complete WAV buffers into one WAV buffer."""
    all_frames = b""
    params = None
    for seg in segments:
        if not seg:
            continue
        try:
            buf = io.BytesIO(seg)
            with wave.open(buf, "rb") as wf:
                if params is None:
                    params = wf.getparams()
                all_frames += wf.readframes(wf.getnframes())
        except Exception:
            # Segment might be raw bytes without a WAV header — append directly
            all_frames += seg

    if not all_frames:
        return _silence_wav(50)

    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        if params:
            wf.setparams(params)
        else:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
        wf.writeframes(all_frames)
    return out.getvalue()
