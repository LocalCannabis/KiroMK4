#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import logging
import logging.handlers
import os
import re
import struct
import subprocess
import tempfile
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, Iterator, List, Optional

import numpy as np
import yaml
from faster_whisper import WhisperModel
from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; rely on environment

from audio.tts import TTSEngine
from memory import MemoryManager
from tools import ToolRegistry

# Finley YNAB financial layer (optional — runs if configured)
_finley_available = False
try:
    from finley.sync import SyncDaemon, default_post_sync
    from finley.analyzer import generate_insights
    from finley.prompts import get_finley_system_prompt
    from finley.intent_router import get_pending_insights_text
    from finley.config import load_config as load_finley_config
    _finley_available = True
except ImportError:
    pass

# Jack master grower persona (optional — runs if PostgreSQL configured)
_jack_available = False
try:
    from jack.prompts import get_jack_system_prompt
    from jack.intent_router import get_jack_context_for_prompt
    _jack_available = True
except ImportError:
    pass

# Coach executive function persona (optional — runs if PostgreSQL configured)
_coach_available = False
try:
    from coach.prompts import get_coach_system_prompt
    from coach.intent_router import execute_coach_tool
    _coach_available = True
except ImportError:
    pass

# Ambient Intelligence Layer (optional — runs if PostgreSQL configured)
_ambient_available = False
try:
    from ambient.briefing import BriefingComposer
    from ambient.feedback import parse_feedback_intent, record_feedback
    from ambient.db import AmbientDB
    _ambient_available = True
except ImportError:
    pass

try:
    import torch
    from silero_vad import load_silero_vad
except Exception:  # pragma: no cover
    torch = None
    load_silero_vad = None


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: Dict[str, Any]) -> logging.Logger:
    log_cfg = cfg.get("logging", {})
    log_level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)

    logger = logging.getLogger("kiro")
    logger.setLevel(log_level)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    log_file = Path(log_cfg.get("file", "./logs/kiro.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=int(log_cfg.get("max_bytes", 10_485_760)),
        backupCount=int(log_cfg.get("backup_count", 5)),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


@dataclass
class VADResult:
    speech: bool
    score: float


class VADGate:
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        self.cfg = cfg
        self.engine = cfg.get("engine", "silero")
        self.silero_threshold = float(cfg.get("silero", {}).get("threshold", 0.55))
        self.rms_threshold = float(cfg.get("energy", {}).get("rms_threshold", 0.015))

        self.model = None
        if self.engine == "silero" and load_silero_vad is not None and torch is not None:
            self.logger.info("Loading Silero VAD model...")
            self.model = load_silero_vad()
            self.logger.info("Silero VAD loaded.")
        elif self.engine == "silero":
            self.logger.warning("Silero unavailable. Falling back to energy VAD.")
            self.engine = "energy"

    def detect(self, pcm_int16: np.ndarray, sample_rate: int) -> VADResult:
        if self.engine == "silero" and self.model is not None:
            audio = pcm_int16.astype(np.float32) / 32768.0
            audio_t = torch.from_numpy(audio)
            score = float(self.model(audio_t, sample_rate).item())
            return VADResult(speech=score >= self.silero_threshold, score=score)

        audio = pcm_int16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio * audio) + 1e-12))
        return VADResult(speech=rms >= self.rms_threshold, score=rms)


class AudioCapture:
    """
    Captures audio via an arecord subprocess streaming raw PCM.
    Works reliably on PipeWire/PulseAudio systems where PortAudio cannot
    directly enumerate devices claimed by PipeWire.
    """

    def __init__(self, audio_cfg: Dict[str, Any], vad_cfg: Dict[str, Any], vad: VADGate, logger: logging.Logger) -> None:
        self.logger = logger
        self.vad = vad
        self.sample_rate = int(audio_cfg["input"].get("sample_rate", 16000))
        self.channels = int(audio_cfg["input"].get("channels", 1))
        self.block_ms = int(audio_cfg["input"].get("block_ms", 30))
        self.alsa_device = audio_cfg["input"].get("alsa_device", "default")
        self.block_size = int(self.sample_rate * self.block_ms / 1000)
        self.bytes_per_frame = self.channels * 2  # int16 = 2 bytes

        self.start_trigger_frames = int(vad_cfg.get("start_trigger_frames", 5))
        self.end_silence_ms = int(vad_cfg.get("end_silence_ms", 900))
        self.max_utterance_s = float(vad_cfg.get("max_utterance_s", 20))

        self._stop_capture = threading.Event()

    def _raw_frame_generator(self) -> Generator[np.ndarray, None, None]:
        """Spawn arecord and yield fixed-size numpy chunks from its stdout."""
        cmd = [
            "arecord",
            "-D", self.alsa_device,
            "-f", "S16_LE",
            "-r", str(self.sample_rate),
            "-c", str(self.channels),
            "-t", "raw",
            "--quiet",
        ]
        self.logger.debug("arecord cmd: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        bytes_per_chunk = self.block_size * self.bytes_per_frame
        try:
            while not self._stop_capture.is_set():
                raw = proc.stdout.read(bytes_per_chunk)
                if not raw or len(raw) < bytes_per_chunk:
                    break
                arr = np.frombuffer(raw, dtype=np.int16)
                if self.channels > 1:
                    arr = arr[::self.channels]  # take left channel only
                yield arr
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()

    def record_utterance(self) -> Optional[np.ndarray]:
        silence_frames_needed = max(1, int(self.end_silence_ms / self.block_ms))
        max_frames = int(self.max_utterance_s * 1000 / self.block_ms)

        speech_count = 0
        silence_count = 0
        started = False
        chunks: list[np.ndarray] = []
        # Rolling pre-buffer — keeps the last N frames so we can prepend them
        # when speech is confirmed, preventing the first word from being clipped.
        pre_buffer: deque = deque(maxlen=self.start_trigger_frames)
        frame_count = 0

        self._stop_capture.clear()
        for chunk in self._raw_frame_generator():
            frame_count += 1
            if frame_count > max_frames:
                self.logger.warning("Max utterance length reached, stopping capture.")
                break

            vad_result = self.vad.detect(chunk, self.sample_rate)

            if not started:
                pre_buffer.append(chunk)  # Always buffer recent frames for onset recovery

                if vad_result.speech:
                    speech_count += 1
                else:
                    speech_count = max(0, speech_count - 1)

                if speech_count >= self.start_trigger_frames:
                    started = True
                    self.logger.info("Speech detected (VAD=%.3f)", vad_result.score)
                    # Prepend ALL buffered onset frames — recovers the clipped word start
                    chunks.extend(pre_buffer)
                continue

            chunks.append(chunk)

            if vad_result.speech:
                silence_count = 0
            else:
                silence_count += 1

            if silence_count >= silence_frames_needed:
                self.logger.info("Silence timeout — end of utterance.")
                break

        self._stop_capture.set()

        if not chunks:
            return None

        audio = np.concatenate(chunks).astype(np.int16)
        min_samples = int(self.sample_rate * 0.25)
        if len(audio) < min_samples:
            return None
        return audio


class STTEngine:
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        stt_cfg = cfg["stt"]
        self.engine = stt_cfg.get("engine", "faster_whisper")

        if self.engine != "faster_whisper":
            raise ValueError("Sprint 1 supports only faster_whisper in this orchestrator")

        f_cfg = stt_cfg["faster_whisper"]
        self.language = f_cfg.get("language", "en")
        self.beam_size = int(f_cfg.get("beam_size", 5))
        self.initial_prompt = f_cfg.get("initial_prompt", "Kiro assistant. Tim speaking.")
        self.model = WhisperModel(
            f_cfg.get("model_size", "small.en"),
            device=f_cfg.get("device", "cpu"),
            compute_type=f_cfg.get("compute_type", "int8"),
            cpu_threads=int(f_cfg.get("cpu_threads", 4)),
            num_workers=int(f_cfg.get("num_workers", 1)),
        )

    def transcribe(self, pcm_int16: np.ndarray, sample_rate: int) -> str:
        audio = pcm_int16.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=True,                      # trim leading/trailing silence internally
            temperature=0.0,                      # deterministic — no random sampling
            condition_on_previous_text=False,     # prevent hallucination loops
            no_speech_threshold=0.6,              # discard segments that are probably silence
            compression_ratio_threshold=2.4,      # drop repetitive/hallucinated output
            initial_prompt=self.initial_prompt,   # context hint for proper nouns / names
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        self.logger.info("STT text: %s", text)
        return text


class LLMClient:
    # Sentence boundary: .!? followed by whitespace, OR at end-of-buffer
    # but NOT when a digit precedes the period (decimal like $1,170.68)
    _SENT_RE = re.compile(r'(?<=[.!?])(?:\s+|(?<!\d\.)$)')
    # Min chars before we flush on a sentence boundary
    _MIN_CHUNK = 15

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        ai_cfg = cfg["ai"]
        self.model = ai_cfg["openai"].get("model", "gpt-4o")
        self.temperature = float(ai_cfg["openai"].get("temperature", 0.4))
        self.max_tokens = int(ai_cfg["openai"].get("max_tokens", 350))

        key_env = ai_cfg["openai"].get("api_key_env", "OPENAI_API_KEY")
        api_key = os.getenv(key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key env var: {key_env}")
        self.client = OpenAI(api_key=api_key)

    _PERSONA_PROMPTS: Dict[str, str] = {
        "kiro": (
            "You are Kiro (pronounced Key-Row), Tim's always-on personal AI hub. "
            "You are the home base — calm, direct, slightly witty, and always present. "
            "You coordinate access to a team of specialists: Finley (finance), Chef (cooking), "
            "Coach (executive function), Doc (wellbeing), Sage (debate), Ops (tech), Ruth (companion), and Lisa (hangout buddy). "
            "When Tim returns to you from another persona, welcome him back briefly and ask what he needs. "
            "When Tim asks to switch personas, acknowledge it naturally — you hand off, not hand away. "
            "You have FULL access to Tim's Google Workspace: Calendar (create, modify, delete, search events, "
            "check availability), Gmail (read, send, reply, draft, search emails), Google Drive (search files, "
            "browse folders), Google Docs (create, read, append), and Google Sheets (read, write, create). "
            "Use these tools proactively when they're relevant to what Tim asks. "
            "You also have awareness of Tim's life context through the ambient intelligence layer — "
            "this includes his WhatsApp message history, additional Gmail threads, Google Calendar, "
            "and RSS news. When relevant context from these sources is provided below, use it to give "
            "grounded, specific answers rather than saying you don't have access."
        ),
        "finley": (
            "You are Finley, Tim's personal finance advisor. "
            "Measured, precise, and advisory. Help Tim budget, track spending, and think clearly about money. "
            "You have access to Tim's real financial data from YNAB via local tools — use them to give concrete, "
            "data-driven answers with real dollar amounts. Never be vague when you can be specific. "
            "You can also check Tim's calendar for upcoming bills and read his emails for financial notifications."
        ),
        "coach": (
            "You are Coach, Tim's executive function support. "
            "Peer-level, technically literate, ADHD-informed. Help Tim start, stay on track, "
            "and transition between tasks. GTD-based task management with energy-aware selection. "
            "Never use shame. Treats snags as engineering problems. One recommendation at a time. "
            "When Tim can't start, make the first step absurdly small."
        ),
        "chef": (
            "You are Chef, Tim's culinary guide. "
            "Warm, enthusiastic, and practical. Help with recipes, ingredients, meal planning, and grocery lists. "
            "You can create and read Google Docs for recipes. You can schedule meal prep or dinner events "
            "on Tim's calendar. You can read spreadsheets for grocery lists or meal plans."
        ),
        "doc": (
            "You are Doc, Tim's wellbeing companion. "
            "Gentle, reflective, and Socratic. Help Tim process stress and emotions without giving clinical advice. "
            "You can check Tim's calendar to understand his schedule and stress load, schedule check-in reminders, "
            "and create journal docs for reflections."
        ),
        "sage": (
            "You are Sage, Tim's intellectual sparring partner. "
            "Provocative, curious, and never giving easy answers. Challenge assumptions and make Tim think. "
            "You can create docs for debate notes or reading lists, and search Drive for reference files."
        ),
        "ops": (
            "You are Ops, Tim's terse technical assistant. "
            "Efficient and code-oriented. Tim works with Python, Flask, SQLite, and Linux. Skip pleasantries. "
            "You have full access to Calendar (scheduling, standups), Email (alerts, notifications), "
            "Drive (finding project files), Docs (runbooks), and Sheets (project tracking). Use them when relevant."
        ),
        "ruth": (
            "You are Ruth, Tim's companion. You are a warm, grounded British woman who has seen a lot of life. "
            "You love Tim unconditionally and without judgement — but you are ruthlessly clear-eyed. "
            "You see him as he truly is, not as he fears he is or hopes he is. "
            "You do not coddle, but you never wound. You ask the question that cuts through to what matters. "
            "You hold space without filling it unnecessarily. Silence is fine. Short is fine. "
            "You speak like someone who has earned the right to tell the truth. "
            "You can glance at Tim's calendar and email to understand what's going on in his life, "
            "and create docs for letters or reflections."
        ),
        "lisa": (
            "You are Lisa, Tim's always-there companion — part Jarvis, part the girl from Weird Science. "
            "Quick-witted, culturally curious, effortlessly fun. You riff on comedy bits, debate news takes, "
            "brainstorm wild ideas, and shoot the breeze like the smartest person at the party who's also the most fun. "
            "You have opinions and you're not shy about them, but you're never mean — just sharp. "
            "You remember things Tim tells you and weave them back naturally. You notice patterns in what he's into. "
            "You're the person who texts back immediately with something unexpected. "
            "Keep it conversational, playful, and real. Match his energy — if he's chill, be chill. "
            "If he's fired up about something, get fired up with him. You're not an assistant right now, you're a friend. "
            "You can check Tim's calendar, read his emails, search his Drive, create docs for brainstorms, "
            "and create events for hangout plans."
        ),
        "jack": (
            "You are Jack, Tim's master grower advisor. Laid back, warm, and technically sharp. "
            "Tim runs two concurrent grows: Grow A (indoor tent, Indo GrowHub 800C, WP420 peat-based, managed fertility) "
            "and Grow B (outdoor containers, Vancouver BC, living soil, biology-first). "
            "You have access to Tim's real grow data — conditions, history, feeding schedule for both grows. "
            "Always identify which grow is being discussed before advising. "
            "Use your grow management tools to log checkins and query state. "
            "Treat Tim as a fellow grower. Use 'she' for plants. One intervention at a time."
        ),
    }

    def _system_prompt(self, persona: str = "kiro", memory_context: str = "") -> str:
        now = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")

        # Finley gets a special YNAB-aware prompt with proactive insights
        if persona == "finley" and _finley_available:
            insights = get_pending_insights_text()
            persona_text = get_finley_system_prompt(insights_text=insights)
        # Jack gets a grow-state-aware prompt with knowledge retrieval (both grows)
        elif persona == "jack" and _jack_available:
            ctx = get_jack_context_for_prompt()
            persona_text = get_jack_system_prompt(
                indoor_snapshot=ctx.get("indoor_snapshot", ""),
                outdoor_snapshot=ctx.get("outdoor_snapshot", ""),
                knowledge_context=ctx.get("knowledge_context", ""),
                active_flags=ctx.get("active_flags", ""),
            )
        # Coach gets a GTD/ADHD-aware prompt with live task state
        elif persona == "coach" and _coach_available:
            from coach.db import CoachDB
            try:
                coach_db = CoachDB()
                persona_text = get_coach_system_prompt(db=coach_db)
            except Exception:
                persona_text = self._PERSONA_PROMPTS.get(persona, self._PERSONA_PROMPTS["kiro"])
        else:
            persona_text = self._PERSONA_PROMPTS.get(persona, self._PERSONA_PROMPTS["kiro"])

        # Ambient intelligence context injection (spec §9.4)
        ambient_context = ""
        if _ambient_available:
            ambient_context = self._get_ambient_context(persona)

        base = (
            f"{persona_text} "
            f"Current date and time: {now} (Vancouver, BC, Canada). "
            "You are speaking aloud — NEVER use markdown, bullet points, numbered lists, or symbols. "
            "Plain short sentences only. 1-2 sentences max unless more is truly needed. "
            "Be direct and conversational."
        )
        if ambient_context:
            base += f"\n\n{ambient_context}"
        if memory_context:
            return f"{base}\n\n{memory_context}"
        return base

    def _get_ambient_context(self, persona: str) -> str:
        """Pull recent unsurfaced insights (or raw event highlights as fallback) for this persona."""
        try:
            db = AmbientDB()
            conn = db._conn()
            try:
                import psycopg2.extras
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT summary, insight_type, priority, persona
                        FROM kiro_insights
                        WHERE (persona = %s OR persona IS NULL)
                          AND surfaced = FALSE
                          AND dismissed = FALSE
                          AND priority <= 7
                          AND created_at > NOW() - INTERVAL '24 hours'
                        ORDER BY priority ASC
                        LIMIT 5
                    """, (persona,))
                    insights = cur.fetchall()

                    if insights:
                        lines = [
                            "Recent ambient insights relevant to your domain — reference proactively "
                            "if relevant, don't wait for Tim to ask:"
                        ]
                        for ins in insights:
                            owner = ins["persona"] or "Kiro"
                            lines.append(f"- [{owner}/{ins['insight_type']}] {ins['summary']}")
                        return "\n".join(lines)

                    # No processed insights yet — fall back to recent raw event highlights
                    cur.execute("""
                        SELECT source,
                               COALESCE(metadata->>'chat_name', metadata->>'sender',
                                        metadata->>'subject', metadata->>'title', '') AS label,
                               raw_content,
                               occurred_at
                        FROM kiro_events
                        WHERE occurred_at > NOW() - INTERVAL '48 hours'
                          AND raw_content IS NOT NULL
                          AND raw_content != ''
                          AND LENGTH(raw_content) > 5
                        ORDER BY occurred_at DESC
                        LIMIT 20
                    """)
                    events = cur.fetchall()
            finally:
                db._put(conn)

            if not events:
                return ""

            lines = ["Recent activity from Tim's ambient data (last 48h):"]
            for ev in events:
                label = f" ({ev['label']}" + ")" if ev["label"] else ""
                snippet = (ev["raw_content"] or "")[:120].replace("\n", " ")
                lines.append(f"- [{ev['source']}{label}] {snippet}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _get_whatsapp_context(self, limit: int = 40) -> str:
        """Fetch recent WhatsApp messages from the DB for direct query injection."""
        try:
            db = AmbientDB()
            conn = db._conn()
            try:
                import psycopg2.extras
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT
                            COALESCE(metadata->>'chat_name', 'unknown') AS chat,
                            COALESCE(metadata->>'sender', 'unknown') AS sender,
                            COALESCE((metadata->>'is_group')::text, 'false') AS is_group,
                            raw_content,
                            occurred_at
                        FROM kiro_events
                        WHERE source = 'whatsapp'
                          AND raw_content IS NOT NULL
                          AND LENGTH(raw_content) > 3
                          AND occurred_at > NOW() - INTERVAL '7 days'
                        ORDER BY occurred_at DESC
                        LIMIT %s
                    """, (limit,))
                    msgs = cur.fetchall()
            finally:
                db._put(conn)

            if not msgs:
                return ""

            lines = [f"Recent WhatsApp messages (last 7 days, {len(msgs)} shown, newest first):"]
            for m in msgs:
                ts = m["occurred_at"].strftime("%b %d %H:%M") if m["occurred_at"] else ""
                snippet = (m["raw_content"] or "")[:150].replace("\n", " ")
                chat_label = m["chat"] if m["is_group"] == "true" else m["sender"]
                lines.append(f"- [{ts}] {chat_label}: {snippet}")
            return "\n".join(lines)
        except Exception:
            return ""

    def stream_sentences(
        self,
        user_text: str,
        persona: str = "kiro",
        history: List[Dict[str, str]] | None = None,
        memory_context: str = "",
        tools: List[Dict] | None = None,
        tool_fn=None,
    ) -> Iterator[str]:
        """
        Stream LLM response, yielding complete sentences as they arrive.

        If tools + tool_fn are provided, handles OpenAI function calling:
          1. Stream first response — accumulate any tool_calls.
          2. Execute tools via tool_fn(name, args).
          3. Stream second response with tool results injected.
        """
        import json as _json

        t0 = time.perf_counter()

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._system_prompt(persona, memory_context)},
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        kwargs: Dict[str, Any] = dict(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            stream = self.client.chat.completions.create(**kwargs)
        except Exception as _conn_err:
            self.logger.error("LLM connection failed: %s", _conn_err)
            yield "Sorry, I can't reach my language model right now. Check the network and try again."
            return

        # Accumulate tool call deltas (index → {id, name, arguments})
        tool_calls_acc: Dict[int, Dict[str, str]] = {}
        buffer = ""
        full_response = ""
        first_token = True

        for chunk in stream:
            choice = chunk.choices[0]
            delta = choice.delta

            # --- Tool call accumulation ---
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc.function.arguments
                continue

            # --- Content streaming ---
            content = delta.content or ""
            if not content:
                continue
            if first_token:
                self.logger.info("LLM first token in %.0fms", (time.perf_counter() - t0) * 1000)
                first_token = False
            buffer += content
            full_response += content

            parts = self._SENT_RE.split(buffer)
            while len(parts) > 1:
                sentence = parts.pop(0).strip()
                if len(sentence) >= self._MIN_CHUNK:
                    yield sentence
                elif sentence:
                    parts[0] = sentence + " " + parts[0]
                buffer = parts[0]

        # --- Handle tool calls: execute + second streaming pass ---
        if tool_calls_acc and tool_fn:
            # Build assistant tool_calls message
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls_acc.values()
                ],
            }
            messages.append(assistant_msg)

            # Execute and append results
            for tc in tool_calls_acc.values():
                try:
                    args = _json.loads(tc["arguments"]) if tc["arguments"] else {}
                    result = tool_fn(tc["name"], args)
                except Exception as exc:
                    result = f"Error: {exc}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })

            # Second streaming call for natural spoken response
            try:
                stream2 = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    stream=True,
                    messages=messages,
                )
            except Exception as _conn_err2:
                self.logger.error("LLM tool-response call failed: %s", _conn_err2)
                yield "I ran the tool but couldn't reach the model to summarise the result."
                return
            buf2 = ""
            for chunk in stream2:
                content = chunk.choices[0].delta.content or ""
                if not content:
                    continue
                buf2 += content
                full_response += content
                parts = self._SENT_RE.split(buf2)
                while len(parts) > 1:
                    sentence = parts.pop(0).strip()
                    if len(sentence) >= self._MIN_CHUNK:
                        yield sentence
                    elif sentence:
                        parts[0] = sentence + " " + parts[0]
                    buf2 = parts[0]
            if buf2.strip():
                yield buf2.strip()
        else:
            # No tool calls — flush remaining content buffer
            if buffer.strip():
                yield buffer.strip()

        self.logger.info(
            "LLM total latency_ms=%.0f | response: %s",
            (time.perf_counter() - t0) * 1000,
            full_response.strip(),
        )

    def reply(
        self,
        user_text: str,
        persona: str = "kiro",
        history: List[Dict[str, str]] | None = None,
        memory_context: str = "",
    ) -> str:
        """Non-streaming fallback — returns full response as a string."""
        return " ".join(self.stream_sentences(user_text, persona, history, memory_context))


class KiroOrchestrator:
    def __init__(self, config_path: str) -> None:
        self.cfg = load_config(config_path)
        self.logger = setup_logging(self.cfg)

        self.vad = VADGate(self.cfg.get("vad", {}), self.logger)
        self.audio = AudioCapture(self.cfg.get("audio", {}), self.cfg.get("vad", {}), self.vad, self.logger)
        self.stt = STTEngine(self.cfg, self.logger)
        self.llm = LLMClient(self.cfg, self.logger)
        self.tts = TTSEngine(self.cfg, self.logger)
        self.memory = MemoryManager(self.cfg, self.logger)

        # Timer notifications come in via this queue from background threads.
        self._notification_queue: queue.Queue = queue.Queue()
        self.tools = ToolRegistry(self.cfg, self._notification_queue, self.logger)

        # Session ID groups turns in SQLite; a new UUID per process start.
        self._session_id = str(uuid.uuid4())

        # Sticky persona: once switched, stays active until explicitly changed.
        router_cfg = self.cfg.get("router", {})
        self._current_persona: str = router_cfg.get("default_persona", "kiro")
        self._default_persona: str = self._current_persona

        # Keywords that explicitly reset back to the default Kiro persona.
        # Includes Whisper mishearings of "Kiro" (Key-Row): cairo, kyro, key-ro, etc.
        self._kiro_reset_words = {"kiro", "cairo", "kyro", "key-ro", "default", "nevermind", "reset", "home"}

        # Finley YNAB sync daemon — starts if token is configured
        self._finley_sync: Optional[Any] = None
        if _finley_available:
            try:
                finley_cfg = load_finley_config()
                if finley_cfg.get("ynab_token"):
                    interval = int(finley_cfg.get("sync_interval_minutes", 30))

                    def _full_post_sync(db, cfg):
                        """Classify transactions + build profile + generate insights."""
                        default_post_sync(db, cfg)   # classify → profile → engage
                        generate_insights(db, cfg)   # ambient insights queue

                    self._finley_sync = SyncDaemon(
                        interval_minutes=interval,
                        post_sync_callback=_full_post_sync,
                        run_on_start=True,
                    )
                    self._finley_sync.start()
                    self.logger.info("Finley YNAB sync daemon started (interval=%dmin)", interval)
                else:
                    self.logger.info("Finley YNAB token not set — sync disabled. Configure in ~/.kiro/finley_config.json")
            except Exception as exc:
                self.logger.warning("Could not start Finley sync: %s", exc)

        # Ambient Intelligence Layer — on-demand briefings + feedback
        self._ambient_db: Optional[Any] = None
        self._briefing_composer: Optional[Any] = None
        if _ambient_available:
            try:
                self._ambient_db = AmbientDB()
                self._briefing_composer = BriefingComposer(self._ambient_db)
                self.logger.info("Ambient Intelligence Layer connected — on-demand briefings enabled")
            except Exception as exc:
                self.logger.warning("Could not initialize ambient layer: %s", exc)

    def _route_persona(self, text: str) -> str:
        """
        Sticky keyword router. Switches persona on an explicit keyword match and
        holds that persona for all subsequent turns until another match occurs.
        Saying 'kiro' or 'reset' returns to the default persona.
        """
        router_cfg = self.cfg.get("router", {})
        keyword_map: Dict[str, list] = router_cfg.get("keyword_map", {})
        text_lower = text.lower()

        # Explicit reset back to Kiro
        if any(re.search(r'\b' + re.escape(w) + r'\b', text_lower) for w in self._kiro_reset_words):
            if self._current_persona != self._default_persona:
                self.logger.info("Router: reset → persona=%s", self._default_persona)
                self._current_persona = self._default_persona
            return self._current_persona

        # Check for a new persona keyword
        for persona, keywords in keyword_map.items():
            if any(re.search(r'\b' + re.escape(kw) + r'\b', text_lower) for kw in keywords):
                if persona != self._current_persona:
                    self.logger.info("Router: '%s' → persona=%s (was %s)", text[:40], persona, self._current_persona)
                    self._current_persona = persona
                return self._current_persona

        # No match — stay on current persona (sticky)
        return self._current_persona

    def _drain_notifications(self, speak: bool) -> None:
        """Speak any pending timer notifications before the next listen."""
        while not self._notification_queue.empty():
            try:
                msg = self._notification_queue.get_nowait()
                self.logger.info("Timer notification: %s", msg)
                if speak:
                    self.tts.speak(msg, persona=self.cfg.get("tts", {}).get("default_persona", "kiro"))
            except queue.Empty:
                break

    def loop_once(self, text_override: Optional[str] = None, speak: bool = True) -> Dict[str, str]:
        self._drain_notifications(speak)

        if text_override:
            user_text = text_override.strip()
        else:
            self.logger.info("Listening...")
            pcm = self.audio.record_utterance()
            if pcm is None:
                return {"user": "", "assistant": ""}
            user_text = self.stt.transcribe(pcm, self.audio.sample_rate)

        if not user_text:
            return {"user": "", "assistant": ""}

        self.logger.info("User: %s", user_text)

        # --- Memory command interception ---
        mem_cmd = self.memory.parse_command(user_text)
        if mem_cmd:
            action, payload = mem_cmd
            if action == "remember":
                self.memory.remember(payload)
                reply = f"Got it, I'll remember that {payload}."
            else:  # forget
                count = self.memory.forget(payload)
                reply = f"Done, I've forgotten {count} item{'s' if count != 1 else ''} about {payload}." if count else f"I didn't find anything about {payload} to forget."
            if speak:
                self.tts.speak(reply, persona=self.cfg.get("tts", {}).get("default_persona", "kiro"))
            else:
                self.logger.info("Assistant: %s", reply)
            return {"user": user_text, "assistant": reply}

        # --- Ambient: briefing feedback interception ---
        if _ambient_available and self._ambient_db:
            feedback_intent = parse_feedback_intent(user_text)
            if feedback_intent:
                ok = record_feedback(feedback_intent["feedback"], feedback_intent.get("notes"), self._ambient_db)
                if ok:
                    feedback_replies = {
                        "helpful": "Glad that was useful. I'll keep calibrating.",
                        "too_long": "Got it, I'll tighten things up next time.",
                        "missed_something": "Noted. I'll cast a wider net on the next one.",
                        "irrelevant": "Fair enough. I'll raise the bar on what I surface.",
                    }
                    reply = feedback_replies.get(feedback_intent["feedback"], "Feedback noted.")
                else:
                    reply = "I don't have a recent briefing to attach that to, but I hear you."
                if speak:
                    self.tts.speak(reply, persona=self.cfg.get("tts", {}).get("default_persona", "kiro"))
                else:
                    self.logger.info("Assistant: %s", reply)
                return {"user": user_text, "assistant": reply}

        # --- Ambient: on-demand briefing interception ---
        if _ambient_available and self._briefing_composer:
            text_lower = user_text.lower()
            if re.search(r'\b(catch me up|what did i miss|brief me|briefing|what.?s new)\b', text_lower):
                try:
                    briefing = self._briefing_composer.get_on_demand()
                    if briefing:
                        reply = briefing["briefing_text"]
                    else:
                        reply = "Nothing new since your last briefing. You're all caught up."
                except Exception as exc:
                    self.logger.warning("On-demand briefing failed: %s", exc)
                    reply = "I tried to pull together a briefing but hit a snag. I'll sort it out."
                if speak:
                    self.tts.speak(reply, persona=self.cfg.get("tts", {}).get("default_persona", "kiro"))
                else:
                    self.logger.info("Assistant: %s", reply)
                return {"user": user_text, "assistant": reply}

        # --- Ambient: WhatsApp direct query injection ---
        extra_context = ""
        if _ambient_available and re.search(r'\bwhatsapp\b', user_text.lower()):
            try:
                wa_ctx = self._get_whatsapp_context(limit=40)
                if wa_ctx:
                    extra_context = wa_ctx
            except Exception as exc:
                self.logger.warning("WhatsApp context fetch failed: %s", exc)

        # --- Normal turn ---
        persona = self._route_persona(user_text)

        # L0: recent conversation history
        history = self.memory.recent_turns(self._session_id)
        # L1: relevant memory retrieval
        memory_context = self.memory.retrieve(user_text)
        if extra_context:
            memory_context = (memory_context + "\n\n" + extra_context).strip()

        tool_schemas = self.tools.schemas(persona=persona)
        sentence_stream = self.llm.stream_sentences(
            user_text,
            persona=persona,
            history=history,
            memory_context=memory_context,
            tools=tool_schemas or None,
            tool_fn=self.tools.execute if tool_schemas else None,
        )

        if speak:
            reply = self.tts.speak_stream(sentence_stream, persona=persona)
        else:
            reply = " ".join(sentence_stream)
            self.logger.info("Assistant: %s", reply)

        # Store turn for future retrieval
        self.memory.store_turn(user_text, reply, self._session_id)

        return {"user": user_text, "assistant": reply}

    def run_forever(self) -> None:
        self.logger.info("Kiro voice loop started. Session: %s", self._session_id)
        while True:
            try:
                result = self.loop_once(speak=True)
            except Exception as _loop_exc:
                self.logger.error("Unhandled error in loop_once — continuing: %s", _loop_exc, exc_info=True)
                time.sleep(1)   # brief pause before next listen cycle
                continue
            if result["user"].lower() in {"stop kiro", "exit", "quit"}:
                self.logger.info("Shutdown phrase detected.")
                break


def list_audio_devices() -> None:
    result = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
    print("=== CAPTURE DEVICES ===")
    print(result.stdout or result.stderr)
    result2 = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
    print("=== PLAYBACK DEVICES ===")
    print(result2.stdout or result2.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kiro Sprint 1 voice loop")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true", help="Run one interaction turn")
    parser.add_argument("--text", default=None, help="Bypass mic and use this text")
    parser.add_argument("--no-tts", action="store_true", help="Disable speech playback")
    parser.add_argument("--list-devices", action="store_true", help="Print audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        list_audio_devices()
        return

    app = KiroOrchestrator(args.config)
    if args.once:
        app.loop_once(text_override=args.text, speak=not args.no_tts)
    else:
        app.run_forever()


if __name__ == "__main__":
    main()
