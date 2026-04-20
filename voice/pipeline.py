"""
voice/pipeline.py — Shared voice pipeline components.

Extracted from kiro_server.py so the unified app can use them directly
without running a separate server process.

Classes:
    ServerSTT      — faster_whisper speech-to-text
    ServerLLM      — OpenAI-compatible LLM (voice-optimised: short, spoken responses)
    ServerTTS      — Orpheus-first, Kokoro fallback TTS
    PersonaRouter  — Keyword-based sticky persona routing
    SessionManager — In-memory per-session conversation history

Usage:
    from voice.pipeline import VoicePipeline
    vp = VoicePipeline(cfg)
    vp.init()
    wav_out, meta = vp.process(wav_bytes, session_id="abc", persona="kiro")
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import uuid
import wave
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("kiro.voice")

# ---------------------------------------------------------------------------
# Feature detection (lazy imports)
# ---------------------------------------------------------------------------
_whisper_available = False
_kokoro_available = False

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

_ORPHEUS_ENABLED = os.getenv("ORPHEUS_ENABLED", "false").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------
def wav_bytes_to_pcm(wav_bytes: bytes) -> tuple:
    """Parse WAV bytes → (int16 numpy array, sample_rate)."""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    if n_ch > 1:
        pcm = pcm[::n_ch]
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


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------
class ServerSTT:
    """faster_whisper speech-to-text engine."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.language = cfg.get("language", "en")
        self.beam_size = int(cfg.get("beam_size", 1))
        model_size = cfg.get("model_size", "tiny.en")
        device = cfg.get("device", "cuda")
        compute_type = cfg.get("compute_type", "float16")

        logger.info("Loading Whisper model: %s on %s (%s)...", model_size, device, compute_type)
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=int(cfg.get("cpu_threads", 4)),
            num_workers=int(cfg.get("num_workers", 1)),
        )
        logger.info("Whisper model loaded.")

    def transcribe(self, pcm_int16: np.ndarray, sample_rate: int) -> str:
        audio = pcm_int16.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


# ---------------------------------------------------------------------------
# LLM (voice-optimised: short spoken responses, tool support)
# ---------------------------------------------------------------------------
class ServerLLM:
    """OpenAI-compatible LLM client for voice responses."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
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
            logger.info("Voice LLM endpoint: %s (model=%s)", base_url, self.model)
        else:
            logger.info("Voice LLM endpoint: OpenAI direct (model=%s)", self.model)

        self.client = OpenAI(**kwargs)

    def generate(
        self,
        user_text: str,
        persona: str = "kiro",
        history: Optional[List[Dict[str, str]]] = None,
        tools: Optional[List[Dict]] = None,
        tool_fn: Optional[Any] = None,
        persona_prompts: Optional[Dict[str, str]] = None,
    ) -> str:
        """Generate a voice response. Supports one tool-call round-trip."""
        now = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")

        # Build persona prompt — try dynamic builders first, fall back to static
        persona_text = self._build_persona_prompt(persona, persona_prompts)

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
            messages.append(msg.to_dict() if hasattr(msg, "to_dict") else {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    result = tool_fn(tc.function.name, args)
                except Exception as exc:
                    result = f"Tool error: {exc}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
            second = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=messages,
            )
            return second.choices[0].message.content.strip()

        return (msg.content or "").strip()

    def _build_persona_prompt(
        self, persona: str, fallback_prompts: Optional[Dict[str, str]] = None
    ) -> str:
        """Try dynamic prompt builders, fall back to static prompts."""
        try:
            if persona == "finley":
                from finley.prompts import get_finley_system_prompt
                from finley.intent_router import get_pending_insights_text
                insights = get_pending_insights_text()
                return get_finley_system_prompt(insights_text=insights)
        except Exception:
            pass

        try:
            if persona == "coach":
                from coach.prompts import get_coach_system_prompt
                from coach.db import CoachDB
                return get_coach_system_prompt(db=CoachDB())
        except Exception:
            pass

        try:
            if persona == "jack":
                from jack.prompts import get_jack_system_prompt
                from jack.intent_router import get_jack_context_for_prompt
                ctx = get_jack_context_for_prompt()
                return get_jack_system_prompt(
                    indoor_snapshot=ctx.get("indoor_snapshot", ""),
                    outdoor_snapshot=ctx.get("outdoor_snapshot", ""),
                    knowledge_context=ctx.get("knowledge_context", ""),
                    active_flags=ctx.get("active_flags", ""),
                )
        except Exception:
            pass

        if fallback_prompts:
            return fallback_prompts.get(persona, fallback_prompts.get("kiro", "You are Kiro."))
        return "You are Kiro, Tim's personal AI assistant."


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------
class ServerTTS:
    """
    TTS engine — returns WAV bytes.

    Priority: Orpheus (if ORPHEUS_ENABLED=true) → Kokoro-82M fallback.
    """

    SAMPLE_RATE = 24000

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._orpheus_ok: bool = False
        self._kokoro_pipeline = None
        self._kokoro_voice_map: Dict[str, str] = {}
        self._kokoro_speed: float = 1.0

        if _ORPHEUS_ENABLED:
            try:
                from audio.orpheus_client import health_check
                if health_check():
                    self._orpheus_ok = True
                    logger.info("TTS engine: Orpheus (ORPHEUS_ENABLED=true)")
                else:
                    logger.warning("Orpheus unreachable — falling back to Kokoro")
            except ImportError as exc:
                logger.warning("audio.orpheus_client unavailable (%s); using Kokoro", exc)

        if not self._orpheus_ok:
            if not _kokoro_available:
                raise RuntimeError("Neither Orpheus nor Kokoro is available for TTS")
            kokoro_cfg = cfg.get("kokoro", {})
            self._kokoro_voice_map = kokoro_cfg.get("voice_map", {"kiro": "af_heart"})
            self._kokoro_speed = float(kokoro_cfg.get("speed", 1.0))
            device = kokoro_cfg.get("device", "cuda")
            logger.info("Loading Kokoro-82M on device=%s...", device)
            self._kokoro_pipeline = KokoroPipeline(lang_code="a", device=device)
            logger.info("TTS engine: Kokoro-82M")

    @property
    def engine_name(self) -> str:
        return "orpheus" if self._orpheus_ok else "kokoro"

    def synthesize_wav(self, text: str, persona: str = "kiro") -> bytes:
        if self._orpheus_ok:
            try:
                from audio.orpheus_client import generate_speech
                return generate_speech(persona, text)
            except Exception as exc:
                logger.warning("Orpheus failed (%s); trying Kokoro", exc)
                if self._kokoro_pipeline is None:
                    raise

        if self._kokoro_pipeline is not None:
            voice = self._kokoro_voice_map.get(
                persona, self._kokoro_voice_map.get("kiro", "af_heart")
            )
            chunks: List[np.ndarray] = []
            for result in self._kokoro_pipeline(text, voice=voice, speed=self._kokoro_speed):
                if result.audio is not None:
                    chunks.append(result.audio.numpy())
            if not chunks:
                return pcm_to_wav_bytes(np.zeros(0, dtype=np.float32), self.SAMPLE_RATE)
            audio = np.concatenate(chunks).astype(np.float32)
            return pcm_to_wav_bytes(audio, self.SAMPLE_RATE)

        raise RuntimeError("No TTS engine available")


# ---------------------------------------------------------------------------
# Persona routing
# ---------------------------------------------------------------------------
class PersonaRouter:
    """Keyword-based sticky persona router."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.default_persona = cfg.get("default_persona", "kiro")
        self.reset_words = set(cfg.get("reset_words", ["kiro", "reset", "home"]))
        self.keyword_map: Dict[str, List[str]] = cfg.get("keyword_map", {})
        self._session_personas: Dict[str, str] = {}

    def route(self, text: str, session_id: str) -> str:
        current = self._session_personas.get(session_id, self.default_persona)
        text_lower = text.lower()

        if any(re.search(r"\b" + re.escape(w) + r"\b", text_lower) for w in self.reset_words):
            self._session_personas[session_id] = self.default_persona
            return self.default_persona

        for persona, keywords in self.keyword_map.items():
            if any(re.search(r"\b" + re.escape(kw) + r"\b", text_lower) for kw in keywords):
                self._session_personas[session_id] = persona
                return persona

        self._session_personas[session_id] = current
        return current


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------
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

        max_msgs = self.max_turns * 2
        if len(session["history"]) > max_msgs:
            session["history"] = session["history"][-max_msgs:]

    def cleanup_expired(self) -> int:
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if (now - s["last_active"]) / 60 > self.expire_minutes
        ]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)


# ---------------------------------------------------------------------------
# Unified pipeline wrapper
# ---------------------------------------------------------------------------
class VoicePipeline:
    """
    High-level wrapper around all voice components.
    Call init() after construction to load models.
    """

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.stt: Optional[ServerSTT] = None
        self.llm: Optional[ServerLLM] = None
        self.tts: Optional[ServerTTS] = None
        self.router: Optional[PersonaRouter] = None
        self.sessions: Optional[SessionManager] = None
        self._start_time = time.time()

    @property
    def ready(self) -> bool:
        return all([self.stt, self.llm, self.tts])

    def init(self) -> None:
        """Load all models. Call once at startup."""
        # STT
        if _whisper_available:
            try:
                self.stt = ServerSTT(self.cfg.get("stt", {}))
            except Exception as e:
                logger.error("STT init failed: %s", e)
        else:
            logger.warning("faster_whisper not installed — STT unavailable")

        # LLM
        if OpenAI is not None:
            try:
                self.llm = ServerLLM(self.cfg.get("llm", {}))
            except Exception as e:
                logger.error("LLM init failed: %s", e)
        else:
            logger.warning("openai package not installed — LLM unavailable")

        # TTS
        try:
            self.tts = ServerTTS(self.cfg.get("tts", {}))
        except Exception as e:
            logger.error("TTS init failed: %s", e)

        # Router
        self.router = PersonaRouter(self.cfg.get("router", {}))

        # Sessions
        session_cfg = self.cfg.get("session", {})
        self.sessions = SessionManager(
            max_turns=int(session_cfg.get("max_history_turns", 20)),
            expire_minutes=int(session_cfg.get("expire_minutes", 60)),
        )

        logger.info("=" * 60)
        logger.info("Voice pipeline initialized:")
        logger.info("  STT:    %s", "✓ ready" if self.stt else "✗ unavailable")
        logger.info("  LLM:    %s", "✓ ready" if self.llm else "✗ unavailable")
        logger.info("  TTS:    %s", f"✓ {self.tts.engine_name}" if self.tts else "✗ unavailable")
        logger.info("  Router: %s (default=%s)", "✓ ready", self.router.default_persona)
        logger.info("=" * 60)

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok" if self.ready else "degraded",
            "whisper": self.stt is not None,
            "llm": self.llm is not None,
            "tts": self.tts is not None,
            "tts_engine": self.tts.engine_name if self.tts else None,
            "uptime_seconds": round(time.time() - self._start_time),
        }

    def process(
        self,
        wav_bytes: bytes,
        session_id: Optional[str] = None,
        persona_override: Optional[str] = None,
    ) -> tuple:
        """
        Full pipeline: WAV in → (wav_out_bytes, metadata_dict).

        metadata_dict has: transcript, response_text, persona, session_id, timing
        """
        t_start = time.perf_counter()

        if not self.ready:
            raise RuntimeError("Voice pipeline not fully initialized")

        # Parse audio
        pcm, sr = wav_bytes_to_pcm(wav_bytes)
        t_parsed = time.perf_counter()

        # STT
        transcript = self.stt.transcribe(pcm, sr)
        if not transcript:
            raise ValueError("No speech detected in audio")
        t_stt = time.perf_counter()
        logger.info("STT: %s", transcript)

        # Route persona
        if not session_id:
            session_id = str(uuid.uuid4())
        if persona_override:
            persona = persona_override
        else:
            persona = self.router.route(transcript, session_id)

        # History
        history = self.sessions.get_history(session_id)

        # Resolve tools
        active_tools, active_tool_fn = self._get_tools(persona)

        # LLM
        response_text = self.llm.generate(
            transcript,
            persona=persona,
            history=history,
            tools=active_tools,
            tool_fn=active_tool_fn,
        )
        if not response_text:
            raise RuntimeError("LLM returned empty response")
        t_llm = time.perf_counter()
        logger.info("LLM [%s]: %s", persona, response_text)

        # Save turn
        self.sessions.add_turn(session_id, transcript, response_text)

        # TTS
        wav_out = self.tts.synthesize_wav(response_text, persona=persona)
        t_tts = time.perf_counter()

        timing = {
            "audio_parse_ms": round((t_parsed - t_start) * 1000),
            "stt_ms": round((t_stt - t_parsed) * 1000),
            "llm_ms": round((t_llm - t_stt) * 1000),
            "tts_ms": round((t_tts - t_llm) * 1000),
            "total_ms": round((t_tts - t_start) * 1000),
        }
        logger.info(
            "Voice: stt=%dms llm=%dms tts=%dms total=%dms",
            timing["stt_ms"], timing["llm_ms"], timing["tts_ms"], timing["total_ms"],
        )

        meta = {
            "transcript": transcript,
            "response_text": response_text,
            "persona": persona,
            "session_id": session_id,
            "timing": timing,
        }
        return wav_out, meta

    @staticmethod
    def _get_tools(persona: str):
        """Resolve tool schemas + executor for a persona."""
        try:
            if persona == "finley":
                from finley.intent_router import FINLEY_TOOL_SCHEMAS, execute_finley_tool
                return FINLEY_TOOL_SCHEMAS, execute_finley_tool
            if persona == "coach":
                from coach.intent_router import COACH_TOOL_SCHEMAS, execute_coach_tool
                return COACH_TOOL_SCHEMAS, execute_coach_tool
            if persona == "jack":
                from jack.intent_router import JACK_TOOL_SCHEMAS, execute_jack_tool
                return JACK_TOOL_SCHEMAS, execute_jack_tool
        except ImportError:
            pass
        return None, None
