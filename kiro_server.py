#!/usr/bin/env python3
"""
kiro_server.py — Beast-side API server for Kiro Pi thin-client architecture.

Accepts raw audio from thin clients (Pi 5), processes it through the full
Kiro pipeline (STT → LLM → TTS), and returns synthesized speech audio.

Architecture:
    Pi mic → [network] → /process → Whisper STT → LLM → Kokoro TTS → [network] → Pi speaker

Endpoints:
    POST /process  — Full pipeline: audio in → audio out
    GET  /health   — Component health check
    GET  /ping     — Latency measurement

Usage:
    python kiro_server.py [--config kiro_server_config.yaml]

Test:
    curl -X POST http://localhost:5400/process \\
        -H "Content-Type: audio/wav" \\
        --data-binary @test_recording.wav \\
        --output response.wav

    curl http://localhost:5400/health
    curl http://localhost:5400/ping
"""

from __future__ import annotations

import argparse
import io
import logging
import logging.handlers
import os
import re
import time
import uuid
import wave
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import yaml
from flask import Flask, request, jsonify, Response

# ---------------------------------------------------------------------------
# Pipeline imports (loaded lazily to report health accurately)
# ---------------------------------------------------------------------------
_whisper_available = False
_kokoro_available = False
_piper_available = False

try:
    from faster_whisper import WhisperModel
    _whisper_available = True
except ImportError:
    WhisperModel = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from kokoro import KPipeline as KokoroPipeline
    _kokoro_available = True
except ImportError:
    KokoroPipeline = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_jack_api_available = False
_jack_tools_available = False
try:
    from jack.api import jack_bp
    _jack_api_available = True
except ImportError:
    jack_bp = None
try:
    from jack.intent_router import JACK_TOOL_SCHEMAS, execute_jack_tool
    _jack_tools_available = True
except ImportError as _e:
    logging.getLogger("kiro-server").warning("Jack tools unavailable: %s", _e)

# Finley YNAB financial layer (optional)
_finley_available = False
_finley_sync: Optional[Any] = None
_finley_db: Optional[Any] = None
try:
    from finley.sync import SyncDaemon, default_post_sync
    from finley.prompts import get_finley_system_prompt
    from finley.intent_router import FINLEY_TOOL_SCHEMAS, execute_finley_tool, get_pending_insights_text
    from finley.config import load_config as load_finley_config
    _finley_available = True
except ImportError as _e:
    logging.getLogger("kiro-server").warning("Finley unavailable: %s", _e)

# Coach executive function persona (optional)
_coach_available = False
try:
    from coach.prompts import get_coach_system_prompt
    from coach.intent_router import COACH_TOOL_SCHEMAS, execute_coach_tool
    from coach.db import CoachDB
    _coach_available = True
except ImportError as _e:
    logging.getLogger("kiro-server").warning("Coach unavailable: %s", _e)

# Orpheus TTS feature flag (reads from .env loaded above)
_ORPHEUS_ENABLED = os.getenv("ORPHEUS_ENABLED", "false").lower() in ("true", "1", "yes")


# ============================================================================
# Persona prompts (mirrors kiro.py LLMClient._PERSONA_PROMPTS)
# ============================================================================
PERSONA_PROMPTS: Dict[str, str] = {
    "kiro": (
        "You are Kiro (pronounced Key-Row), Tim's always-on personal AI hub. "
        "You are the home base — calm, direct, slightly witty, and always present. "
        "You coordinate access to a team of specialists: Finley (finance), Chef (cooking), "
        "Coach (executive function), Doc (wellbeing), Sage (debate), Ops (tech), Ruth (companion), and Lisa (hangout buddy). "
        "When Tim returns to you from another persona, welcome him back briefly and ask what he needs. "
        "When Tim asks to switch personas, acknowledge it naturally — you hand off, not hand away."
    ),
    "finley": (
        "You are Finley, Tim's personal finance advisor. "
        "Measured, precise, and advisory. Help Tim budget, track spending, and think clearly about money."
    ),
    "coach": (
        "You are Coach, Tim's executive function support. "
        "Peer-level, technically literate, ADHD-informed. Help Tim start, stay on track, "
        "and transition between tasks. Never use shame. One recommendation at a time."
    ),
    "chef": (
        "You are Chef, Tim's culinary guide. "
        "Warm, enthusiastic, and practical. Help with recipes, ingredients, meal planning, and grocery lists."
    ),
    "doc": (
        "You are Doc, Tim's wellbeing companion. "
        "Gentle, reflective, and Socratic. Help Tim process stress and emotions without giving clinical advice."
    ),
    "sage": (
        "You are Sage, Tim's intellectual sparring partner. "
        "Provocative, curious, and never giving easy answers. Challenge assumptions and make Tim think."
    ),
    "ops": (
        "You are Ops, Tim's terse technical assistant. "
        "Efficient and code-oriented. Tim works with Python, Flask, SQLite, and Linux. Skip pleasantries."
    ),
    "ruth": (
        "You are Ruth, Tim's companion. A warm, grounded British woman who sees Tim as he truly is. "
        "You do not coddle, but you never wound. You ask the question that cuts through to what matters."
    ),
    "lisa": (
        "You are Lisa, Tim's always-there companion — part Jarvis, part the girl from Weird Science. "
        "Quick-witted, culturally curious, effortlessly fun. Match his energy — if he's chill, be chill."
    ),
    "jack": (
        "You are Jack, Tim's master grower advisor. Laid back, warm, and technically sharp. "
        "Tim runs two concurrent grows: Grow A (indoor tent, Indo GrowHub 800C, WP420 peat-based, managed fertility) "
        "and Grow B (outdoor containers, Vancouver BC, living soil, biology-first). "
        "Always identify which grow is being discussed before advising. "
        "Treat Tim as a fellow grower. Use 'she' for plants. One intervention at a time. Cite sources naturally."
    ),
}


# ============================================================================
# Configuration & Logging
# ============================================================================
def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: Dict[str, Any]) -> logging.Logger:
    log_cfg = cfg.get("logging", {})
    log_level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)

    logger = logging.getLogger("kiro-server")
    logger.setLevel(log_level)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_file = Path(log_cfg.get("file", "./logs/kiro_server.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=int(log_cfg.get("max_bytes", 10_485_760)),
        backupCount=int(log_cfg.get("backup_count", 5)),
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


# ============================================================================
# Audio Utilities
# ============================================================================
def wav_bytes_to_pcm(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Parse WAV bytes → (int16 numpy array, sample_rate)."""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    if n_ch > 1:
        pcm = pcm[::n_ch]  # mono: take first channel
    return pcm, sr


def pcm_to_wav_bytes(audio_float32: np.ndarray, sample_rate: int) -> bytes:
    """Convert float32 numpy audio [-1, 1] → WAV bytes (16-bit mono)."""
    audio_int16 = (np.clip(audio_float32, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


# ============================================================================
# Pipeline Components
# ============================================================================
class ServerSTT:
    """faster_whisper speech-to-text engine."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        self.language = cfg.get("language", "en")
        self.beam_size = int(cfg.get("beam_size", 1))
        model_size = cfg.get("model_size", "tiny.en")
        device = cfg.get("device", "cuda")
        compute_type = cfg.get("compute_type", "float16")

        self.logger.info("Loading Whisper model: %s on %s (%s)...", model_size, device, compute_type)
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=int(cfg.get("cpu_threads", 4)),
            num_workers=int(cfg.get("num_workers", 1)),
        )
        self.logger.info("Whisper model loaded.")

    def transcribe(self, pcm_int16: np.ndarray, sample_rate: int) -> str:
        audio = pcm_int16.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text


class ServerLLM:
    """OpenAI-compatible LLM client (works with OpenAI, OpenRouter, etc.)."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        self.model = cfg.get("model", "gpt-4o")
        self.temperature = float(cfg.get("temperature", 0.4))
        self.max_tokens = int(cfg.get("max_tokens", 200))

        key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.getenv(key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key from env var: {key_env}")

        base_url = cfg.get("base_url")
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
            self.logger.info("LLM endpoint: %s (model=%s)", base_url, self.model)
        else:
            self.logger.info("LLM endpoint: OpenAI direct (model=%s)", self.model)

        self.client = OpenAI(**kwargs)

    def generate(
        self,
        user_text: str,
        persona: str = "kiro",
        history: Optional[List[Dict[str, str]]] = None,
        tools: Optional[List[Dict]] = None,
        tool_fn: Optional[Any] = None,
    ) -> str:
        """Generate a complete response (non-streaming). Supports one tool-call round-trip."""
        now = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")

        # Finley gets a dynamic system prompt with live profile context + insights
        if persona == "finley" and _finley_available:
            try:
                insights = get_pending_insights_text()
                persona_text = get_finley_system_prompt(db=_finley_db, insights_text=insights)
            except Exception:
                persona_text = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["kiro"])
        # Coach gets a GTD/ADHD-aware prompt with live task state
        elif persona == "coach" and _coach_available:
            try:
                coach_db = CoachDB()
                persona_text = get_coach_system_prompt(db=coach_db)
            except Exception:
                persona_text = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["kiro"])
        else:
            persona_text = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["kiro"])

        system_msg = (
            f"{persona_text} "
            f"Current date and time: {now} (Vancouver, BC, Canada). "
            "You are speaking aloud — NEVER use markdown, bullet points, numbered lists, or symbols. "
            "Plain short sentences only. 1-2 sentences max unless more is truly needed. "
            "Be direct and conversational."
        )

        messages: List[Dict[str, str]] = [{"role": "system", "content": system_msg}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        # First LLM call (with optional tool schemas)
        kwargs: Dict[str, Any] = dict(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        # Handle tool calls (single round-trip)
        if tool_fn and msg.tool_calls:
            # Append assistant tool-call message
            messages.append(msg.to_dict() if hasattr(msg, "to_dict") else {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            # Execute each tool and append results
            import json as _json
            for tc in msg.tool_calls:
                try:
                    args = _json.loads(tc.function.arguments or "{}")
                    result = tool_fn(tc.function.name, args)
                except Exception as exc:
                    result = f"Tool error: {exc}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
            # Second LLM call with tool results (no tools this time — get final answer)
            second = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=messages,
            )
            return second.choices[0].message.content.strip()

        return (msg.content or "").strip()


class ServerTTS:
    """
    TTS engine for the Beast API server — returns WAV bytes (no local playback).

    Priority order (controlled by ORPHEUS_ENABLED env var):
      1. Orpheus  — when ORPHEUS_ENABLED=true and the FastAPI server is reachable
      2. Kokoro-82M — when Orpheus is disabled or unavailable
    """

    SAMPLE_RATE = 24000

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        self._orpheus_ok: bool = False
        self._kokoro_pipeline = None
        self._kokoro_voice_map: Dict[str, str] = {}
        self._kokoro_speed: float = 1.0

        # ── Try Orpheus first ─────────────────────────────────────────────
        if _ORPHEUS_ENABLED:
            try:
                from audio.orpheus_client import health_check
                if health_check():
                    self._orpheus_ok = True
                    self.logger.info("TTS engine: Orpheus (ORPHEUS_ENABLED=true)")
                else:
                    self.logger.warning(
                        "Orpheus server unreachable at %s — falling back to Kokoro",
                        os.getenv("ORPHEUS_API_URL", "http://localhost:5005"),
                    )
            except ImportError as exc:
                self.logger.warning("audio.orpheus_client unavailable (%s); using Kokoro", exc)

        # ── Kokoro fallback ────────────────────────────────────────────────
        if not self._orpheus_ok:
            if not _kokoro_available:
                raise RuntimeError("Neither Orpheus nor Kokoro is available for TTS")
            kokoro_cfg = cfg.get("kokoro", {})
            self._kokoro_voice_map = kokoro_cfg.get("voice_map", {"kiro": "af_heart"})
            self._kokoro_speed = float(kokoro_cfg.get("speed", 1.0))
            device = kokoro_cfg.get("device", "cuda")
            self.logger.info("Loading Kokoro-82M on device=%s...", device)
            self._kokoro_pipeline = KokoroPipeline(lang_code="a", device=device)
            self.logger.info("TTS engine: Kokoro-82M")

    @property
    def engine_name(self) -> str:
        return "orpheus" if self._orpheus_ok else "kokoro"

    def synthesize_wav(self, text: str, persona: str = "kiro") -> bytes:
        """Synthesize text → WAV bytes (24kHz mono 16-bit)."""
        # ── Orpheus path ──────────────────────────────────────────────────
        if self._orpheus_ok:
            try:
                from audio.orpheus_client import generate_speech
                return generate_speech(persona, text)
            except Exception as exc:
                self.logger.warning(
                    "Orpheus synthesis failed (%s); attempting Kokoro fallback", exc
                )
                # Try to recover via Kokoro if it's loaded
                if self._kokoro_pipeline is not None:
                    pass  # fall through to Kokoro below
                else:
                    raise  # No fallback available

        # ── Kokoro path ───────────────────────────────────────────────────
        if self._kokoro_pipeline is not None:
            voice = self._kokoro_voice_map.get(
                persona, self._kokoro_voice_map.get("kiro", "af_heart")
            )
            chunks: List[np.ndarray] = []
            for result in self._kokoro_pipeline(text, voice=voice, speed=self._kokoro_speed):
                if result.audio is not None:
                    chunks.append(result.audio.numpy())
            if not chunks:
                self.logger.warning("Kokoro produced no audio for: %s", text[:80])
                return pcm_to_wav_bytes(np.zeros(0, dtype=np.float32), self.SAMPLE_RATE)
            audio = np.concatenate(chunks).astype(np.float32)
            return pcm_to_wav_bytes(audio, self.SAMPLE_RATE)

        raise RuntimeError("No TTS engine available")


class PersonaRouter:
    """Keyword-based sticky persona router (mirrors kiro.py logic)."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.default_persona = cfg.get("default_persona", "kiro")
        self.reset_words = set(cfg.get("reset_words", ["kiro", "reset", "home"]))
        self.keyword_map: Dict[str, List[str]] = cfg.get("keyword_map", {})
        # Per-session sticky persona state
        self._session_personas: Dict[str, str] = {}

    def route(self, text: str, session_id: str) -> str:
        current = self._session_personas.get(session_id, self.default_persona)
        text_lower = text.lower()

        # Check for reset words
        if any(re.search(r"\b" + re.escape(w) + r"\b", text_lower) for w in self.reset_words):
            self._session_personas[session_id] = self.default_persona
            return self.default_persona

        # Check for persona keywords
        for persona, keywords in self.keyword_map.items():
            if any(re.search(r"\b" + re.escape(kw) + r"\b", text_lower) for kw in keywords):
                self._session_personas[session_id] = persona
                return persona

        # Sticky: stay on current persona
        self._session_personas[session_id] = current
        return current


class SessionManager:
    """In-memory conversation history per session."""

    def __init__(self, max_turns: int = 20, expire_minutes: int = 60) -> None:
        self.max_turns = max_turns
        self.expire_minutes = expire_minutes
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        session = self._sessions.get(session_id)
        if not session:
            return []
        # Check expiry
        elapsed = (time.time() - session["last_active"]) / 60
        if elapsed > self.expire_minutes:
            del self._sessions[session_id]
            return []
        return list(session["history"])

    def add_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = {"history": [], "last_active": time.time()}

        session = self._sessions[session_id]
        session["last_active"] = time.time()
        session["history"].append({"role": "user", "content": user_text})
        session["history"].append({"role": "assistant", "content": assistant_text})

        # Trim to max turns (each turn = 2 messages)
        max_msgs = self.max_turns * 2
        if len(session["history"]) > max_msgs:
            session["history"] = session["history"][-max_msgs:]

    def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count removed."""
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if (now - s["last_active"]) / 60 > self.expire_minutes
        ]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)


# ============================================================================
# Flask Application
# ============================================================================
app = Flask(__name__)

# Register optional persona blueprints
if _jack_api_available and jack_bp is not None:
    app.register_blueprint(jack_bp, url_prefix="/jack")

try:
    from jack.grow_api import grow_bp
    app.register_blueprint(grow_bp, url_prefix="/api/grow")
    logging.getLogger("kiro-server").info("Grow API registered at /api/grow")
except Exception as _e:
    logging.getLogger("kiro-server").warning("Grow API unavailable: %s", _e)

# Globals initialized at startup
config: Dict[str, Any] = {}
stt: Optional[ServerSTT] = None
llm: Optional[ServerLLM] = None
tts: Optional[ServerTTS] = None
router: Optional[PersonaRouter] = None
sessions: Optional[SessionManager] = None
logger: logging.Logger = logging.getLogger("kiro-server")


@app.route("/ping", methods=["GET"])
def ping():
    """Latency measurement endpoint."""
    return jsonify({"pong": True, "timestamp": time.time()})


@app.route("/health", methods=["GET"])
def health():
    """Component health check."""
    return jsonify({
        "status": "ok",
        "whisper": stt is not None,
        "llm": llm is not None,
        "tts": tts is not None,
        "tts_engine": tts.engine_name if tts is not None else None,
        "timestamp": time.time(),
        "uptime_seconds": time.time() - app.config.get("start_time", time.time()),
    })


@app.route("/process", methods=["POST"])
def process():
    """
    Full pipeline: audio in → audio out.

    Input:
        Body: Raw audio bytes (WAV or PCM)
        Headers:
            Content-Type: audio/wav | audio/pcm
            X-Sample-Rate: 16000     (required for PCM)
            X-Channels: 1            (required for PCM)
            X-Session-Id: <uuid>     (optional; enables conversation history)
            X-Persona: <name>        (optional; override persona routing)

    Output:
        Body: WAV audio bytes
        Content-Type: audio/wav
        X-Transcript: <user speech text>
        X-Response-Text: <assistant response text>
        X-Persona: <persona used>
        X-Timing: <json latency breakdown>
    """
    t_start = time.perf_counter()

    # --- Validate pipeline ---
    if not all([stt, llm, tts]):
        return jsonify({"error": "Pipeline not fully initialized"}), 503

    # --- Parse input audio ---
    content_type = request.content_type or "audio/wav"
    audio_data = request.get_data()

    if not audio_data:
        return jsonify({"error": "No audio data in request body"}), 400

    try:
        if "wav" in content_type:
            pcm, sr = wav_bytes_to_pcm(audio_data)
        elif "pcm" in content_type or "raw" in content_type:
            sr = int(request.headers.get("X-Sample-Rate", 16000))
            channels = int(request.headers.get("X-Channels", 1))
            pcm = np.frombuffer(audio_data, dtype=np.int16)
            if channels > 1:
                pcm = pcm[::channels]
        else:
            return jsonify({"error": f"Unsupported Content-Type: {content_type}"}), 415
    except Exception as e:
        logger.error("Failed to parse input audio: %s", e)
        return jsonify({"error": f"Invalid audio data: {e}"}), 400

    t_audio_parsed = time.perf_counter()

    # --- STT ---
    try:
        transcript = stt.transcribe(pcm, sr)
    except Exception as e:
        logger.error("STT failed: %s", e)
        return jsonify({"error": f"STT failed: {e}"}), 500

    if not transcript:
        return jsonify({"error": "No speech detected in audio"}), 422

    t_stt = time.perf_counter()
    logger.info("STT transcript: %s", transcript)

    # --- Persona routing ---
    session_id = request.headers.get("X-Session-Id", str(uuid.uuid4()))
    persona_override = request.headers.get("X-Persona")

    if persona_override and persona_override in PERSONA_PROMPTS:
        persona = persona_override
    else:
        persona = router.route(transcript, session_id)

    # --- Conversation history ---
    history = sessions.get_history(session_id)

    # --- LLM ---
    try:
        # Build persona-specific tool schemas and dispatch function
        active_tools = None
        active_tool_fn = None

        if persona == "finley" and _finley_available:
            active_tools = FINLEY_TOOL_SCHEMAS
            active_tool_fn = execute_finley_tool
        elif persona == "coach" and _coach_available:
            active_tools = COACH_TOOL_SCHEMAS
            active_tool_fn = execute_coach_tool
        elif persona == "jack" and _jack_tools_available:
            active_tools = JACK_TOOL_SCHEMAS
            active_tool_fn = execute_jack_tool

        response_text = llm.generate(
            transcript,
            persona=persona,
            history=history,
            tools=active_tools,
            tool_fn=active_tool_fn,
        )
    except Exception as e:
        logger.error("LLM failed: %s", e)
        return jsonify({"error": f"LLM failed: {e}"}), 502

    if not response_text:
        return jsonify({"error": "LLM returned empty response"}), 502

    t_llm = time.perf_counter()
    logger.info("LLM response [%s]: %s", persona, response_text)

    # --- Store turn in session history ---
    sessions.add_turn(session_id, transcript, response_text)

    # --- TTS ---
    try:
        wav_response = tts.synthesize_wav(response_text, persona=persona)
    except Exception as e:
        logger.error("TTS failed: %s", e)
        return jsonify({"error": f"TTS failed: {e}"}), 500

    t_tts = time.perf_counter()

    # --- Latency breakdown ---
    timing = {
        "audio_parse_ms": round((t_audio_parsed - t_start) * 1000),
        "stt_ms": round((t_stt - t_audio_parsed) * 1000),
        "llm_ms": round((t_llm - t_stt) * 1000),
        "tts_ms": round((t_tts - t_llm) * 1000),
        "total_ms": round((t_tts - t_start) * 1000),
    }

    logger.info(
        "/process: stt=%dms, llm=%dms, tts=%dms, total=%dms",
        timing["stt_ms"], timing["llm_ms"], timing["tts_ms"], timing["total_ms"],
    )

    # --- Return response ---
    import json
    resp = Response(wav_response, mimetype="audio/wav")
    resp.headers["X-Transcript"] = transcript
    resp.headers["X-Response-Text"] = response_text[:500]  # Header length safety
    resp.headers["X-Persona"] = persona
    resp.headers["X-Session-Id"] = session_id
    resp.headers["X-Timing"] = json.dumps(timing)
    return resp


# ============================================================================
# Startup
# ============================================================================
def init_pipeline(cfg: Dict[str, Any], log: logging.Logger) -> None:
    """Initialize all pipeline components. Called once at startup."""
    global config, stt, llm, tts, router, sessions, logger
    config = cfg
    logger = log

    # --- STT ---
    stt_cfg = cfg.get("stt", {})
    if _whisper_available:
        try:
            stt = ServerSTT(stt_cfg, logger)
        except Exception as e:
            logger.error("Failed to initialize STT: %s", e)
    else:
        logger.error("faster_whisper not installed — STT unavailable")

    # --- LLM ---
    llm_cfg = cfg.get("llm", {})
    if OpenAI is not None:
        try:
            llm = ServerLLM(llm_cfg, logger)
        except Exception as e:
            logger.error("Failed to initialize LLM: %s", e)
    else:
        logger.error("openai package not installed — LLM unavailable")

    # --- TTS ---
    # ServerTTS tries Orpheus first (if ORPHEUS_ENABLED=true), then falls back
    # to Kokoro. No engine= branch needed — priority is baked into ServerTTS.
    tts_cfg = cfg.get("tts", {})
    try:
        tts = ServerTTS(tts_cfg, logger)
    except Exception as e:
        logger.error("Failed to initialize TTS: %s", e)

    # --- Persona Router ---
    router_cfg = cfg.get("router", {})
    router = PersonaRouter(router_cfg)

    # --- Session Manager ---
    session_cfg = cfg.get("session", {})
    sessions = SessionManager(
        max_turns=int(session_cfg.get("max_history_turns", 20)),
        expire_minutes=int(session_cfg.get("expire_minutes", 60)),
    )

    # --- Finley YNAB sync daemon ---
    global _finley_sync, _finley_db
    if _finley_available:
        try:
            from finley.db import FinleyDB
            _finley_db = FinleyDB()
            log.info("Finley DB connected (PostgreSQL)")
            finley_cfg = load_finley_config()
            if finley_cfg.get("ynab_token"):
                interval = int(finley_cfg.get("sync_interval_minutes", 30))
                _finley_sync = SyncDaemon(
                    interval_minutes=interval,
                    post_sync_callback=default_post_sync,
                    run_on_start=True,
                )
                _finley_sync.start()
                log.info("Finley YNAB sync daemon started (interval=%dmin)", interval)
            else:
                log.info("Finley YNAB token not set — sync daemon disabled")
        except Exception as exc:
            log.warning("Could not start Finley sync daemon: %s", exc)

    # Report status
    logger.info("=" * 60)
    logger.info("Kiro Server pipeline initialized:")
    logger.info("  STT:     %s", "✓ ready" if stt else "✗ unavailable")
    logger.info("  LLM:     %s", "✓ ready" if llm else "✗ unavailable")
    logger.info("  TTS:     %s", f"✓ {tts.engine_name}" if tts else "✗ unavailable")
    logger.info("  Router:  %s (default=%s)", "✓ ready", router.default_persona)
    logger.info("  Finley:  %s", f"✓ sync running" if _finley_sync else ("✓ tools only (no token)" if _finley_available else "✗ unavailable"))
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Kiro Beast API Server")
    parser.add_argument(
        "--config",
        default="kiro_server_config.yaml",
        help="Path to server config file",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Override listen host",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override listen port",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg)

    log.info("Starting Kiro Beast API Server...")
    log.info("Config: %s", args.config)

    init_pipeline(cfg, log)

    host = args.host or cfg.get("server", {}).get("host", "0.0.0.0")
    port = args.port or int(cfg.get("server", {}).get("port", 5400))
    debug = cfg.get("server", {}).get("debug", False)

    app.config["start_time"] = time.time()

    log.info("Listening on %s:%d", host, port)
    log.info("Tailscale clients: POST http://<beast-tailscale-ip>:%d/process", port)

    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
