#!/usr/bin/env python3
"""
kiro_cli.py — Text-based interactive CLI for Kiro.

Full persona support, tool calling, memory, streaming output — everything
the voice pipeline does, minus the audio I/O.

Usage:
    python kiro_cli.py [--config config.yaml]

Slash commands:
    /persona <name>   — switch persona (e.g. /persona jack)
    /reset            — reset to default persona (kiro)
    /personas         — list available personas
    /history          — show conversation history (last N turns)
    /clear            — clear conversation history
    /help             — show this help
    /quit             — exit
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import queue
import re
import readline
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import yaml
from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from memory import MemoryManager
from tools import ToolRegistry

# Finley YNAB financial layer (optional)
_finley_available = False
try:
    from finley.sync import SyncDaemon
    from finley.analyzer import generate_insights
    from finley.prompts import get_finley_system_prompt
    from finley.intent_router import get_pending_insights_text
    from finley.config import load_config as load_finley_config
    _finley_available = True
except ImportError:
    pass

# Jack master grower persona (optional)
_jack_available = False
try:
    from jack.prompts import get_jack_system_prompt
    from jack.intent_router import get_jack_context_for_prompt
    _jack_available = True
except ImportError:
    pass

# ── ANSI colours ──────────────────────────────────────────────────────────
_C_RESET  = "\033[0m"
_C_BOLD   = "\033[1m"
_C_DIM    = "\033[2m"
_C_GREEN  = "\033[32m"
_C_CYAN   = "\033[36m"
_C_YELLOW = "\033[33m"
_C_BLUE   = "\033[34m"
_C_MAGENTA = "\033[35m"
_C_RED    = "\033[31m"

_PERSONA_COLOURS: Dict[str, str] = {
    "kiro":   _C_CYAN,
    "finley": _C_GREEN,
    "jack":   _C_GREEN,
    "chef":   _C_YELLOW,
    "coach":  _C_BLUE,
    "doc":    _C_MAGENTA,
    "sage":   _C_CYAN,
    "ops":    _C_DIM,
    "ruth":   _C_MAGENTA,
    "lisa":   _C_YELLOW,
}


def _pc(persona: str) -> str:
    """Persona colour escape."""
    return _PERSONA_COLOURS.get(persona, _C_CYAN)


# ── Config / Logging ─────────────────────────────────────────────────────

def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: Dict[str, Any]) -> logging.Logger:
    log_cfg = cfg.get("logging", {})
    log_level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)

    logger = logging.getLogger("kiro.cli")
    logger.setLevel(log_level)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    # File handler only — no console spam; the CLI owns stdout.
    log_file = Path(log_cfg.get("file", "./logs/kiro_cli.log"))
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


# ── LLM Client (text-oriented — no voice instructions) ───────────────────

class CLILLMClient:
    """
    Streaming OpenAI client tuned for terminal output.

    Differences from the voice-pipeline LLMClient:
      • System prompt does NOT include "You are speaking aloud" / "no markdown"
        instructions — free to use markdown, bullets, code blocks.
      • stream_tokens() yields individual token strings for real-time printing
        (no sentence buffering needed for TTS).
      • Higher max_tokens default (600 vs 200) for richer text answers.
    """

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        ai_cfg = cfg["ai"]
        self.model = ai_cfg["openai"].get("model", "gpt-4o")
        self.temperature = float(ai_cfg["openai"].get("temperature", 0.4))
        # Give the text CLI a generous token ceiling
        self.max_tokens = max(int(ai_cfg["openai"].get("max_tokens", 350)), 600)

        key_env = ai_cfg["openai"].get("api_key_env", "OPENAI_API_KEY")
        api_key = os.getenv(key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key env var: {key_env}")
        self.client = OpenAI(api_key=api_key)

    # ── Persona prompts (identical to kiro.py) ────────────────────────────
    _PERSONA_PROMPTS: Dict[str, str] = {
        "kiro": (
            "You are Kiro (pronounced Key-Row), Tim's always-on personal AI hub. "
            "You are the home base — calm, direct, slightly witty, and always present. "
            "You coordinate access to a team of specialists: Finley (finance), Chef (cooking), "
            "Coach (fitness), Doc (wellbeing), Sage (debate), Ops (tech), Ruth (companion), and Lisa (hangout buddy). "
            "When Tim returns to you from another persona, welcome him back briefly and ask what he needs. "
            "When Tim asks to switch personas, acknowledge it naturally — you hand off, not hand away. "
            "You have FULL access to Tim's Google Workspace: Calendar (create, modify, delete, search events, "
            "check availability), Gmail (read, send, reply, draft, search emails), Google Drive (search files, "
            "browse folders), Google Docs (create, read, append), and Google Sheets (read, write, create). "
            "Use these tools proactively when they're relevant to what Tim asks."
        ),
        "finley": (
            "You are Finley, Tim's personal finance advisor. "
            "Measured, precise, and advisory. Help Tim budget, track spending, and think clearly about money. "
            "You have access to Tim's real financial data from YNAB via local tools — use them to give concrete, "
            "data-driven answers with real dollar amounts. Never be vague when you can be specific. "
            "You can also check Tim's calendar for upcoming bills and read his emails for financial notifications."
        ),
        "coach": (
            "You are Coach, Tim's fitness and health advisor. "
            "Energetic, encouraging, and direct. Push Tim toward his health goals without being preachy. "
            "You can manage Tim's calendar for workout scheduling and check availability. "
            "You can create and read spreadsheets for workout logs and progress tracking, "
            "and create docs for workout plans."
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
            persona_text = get_finley_system_prompt(insights)
        # Jack gets a grow-state-aware prompt with knowledge retrieval (both grows)
        elif persona == "jack" and _jack_available:
            ctx = get_jack_context_for_prompt()
            persona_text = get_jack_system_prompt(
                indoor_snapshot=ctx.get("indoor_snapshot", ""),
                outdoor_snapshot=ctx.get("outdoor_snapshot", ""),
                knowledge_context=ctx.get("knowledge_context", ""),
                active_flags=ctx.get("active_flags", ""),
            )
        else:
            persona_text = self._PERSONA_PROMPTS.get(persona, self._PERSONA_PROMPTS["kiro"])

        # NOTE: no voice-specific instructions here — CLI is free to use
        # markdown, bullet points, code blocks, etc.
        base = (
            f"{persona_text}\n\n"
            f"Current date and time: {now} (Vancouver, BC, Canada). "
            "You are chatting via text — feel free to use markdown, bullet points, "
            "code blocks, and structured formatting when it helps. Be thorough but not verbose."
        )
        if memory_context:
            return f"{base}\n\n{memory_context}"
        return base

    def stream_tokens(
        self,
        user_text: str,
        persona: str = "kiro",
        history: List[Dict[str, str]] | None = None,
        memory_context: str = "",
        tools: List[Dict] | None = None,
        tool_fn=None,
    ) -> Iterator[str]:
        """
        Stream LLM response, yielding individual content tokens for real-time
        terminal output. Handles tool calling with the same two-pass pattern
        as the voice pipeline.
        """
        t0 = time.perf_counter()

        messages: List[Dict[str, Any]] = [
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

        stream = self.client.chat.completions.create(**kwargs)

        # Accumulate tool call deltas (index → {id, name, arguments})
        tool_calls_acc: Dict[int, Dict[str, str]] = {}
        full_response = ""
        first_token = True

        for chunk in stream:
            choice = chunk.choices[0]
            delta = choice.delta

            # Tool call accumulation
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

            # Content streaming — yield each token immediately
            content = delta.content or ""
            if not content:
                continue
            if first_token:
                self.logger.info("LLM first token in %.0fms", (time.perf_counter() - t0) * 1000)
                first_token = False
            full_response += content
            yield content

        # ── Tool calls: execute + second streaming pass ──
        if tool_calls_acc and tool_fn:
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

            # Execute each tool — show a dim status line
            for tc in tool_calls_acc.values():
                tool_name = tc["name"]
                yield f"\n{_C_DIM}  ⚙ {tool_name}…{_C_RESET}"
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    result = tool_fn(tool_name, args)
                    self.logger.info("Tool %s → %s", tool_name, str(result)[:200])
                except Exception as exc:
                    result = f"Error: {exc}"
                    self.logger.warning("Tool %s error: %s", tool_name, exc)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })

            yield "\n"

            # Second streaming call with tool results
            stream2 = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
                messages=messages,
            )
            for chunk in stream2:
                content = chunk.choices[0].delta.content or ""
                if content:
                    full_response += content
                    yield content

        self.logger.info(
            "LLM total latency_ms=%.0f | response length=%d",
            (time.perf_counter() - t0) * 1000,
            len(full_response),
        )


# ── CLI Orchestrator ──────────────────────────────────────────────────────

class KiroCLI:
    def __init__(self, config_path: str) -> None:
        self.cfg = load_config(config_path)
        self.logger = setup_logging(self.cfg)

        self.llm = CLILLMClient(self.cfg, self.logger)
        self.memory = MemoryManager(self.cfg, self.logger)

        self._notification_queue: queue.Queue = queue.Queue()
        self.tools = ToolRegistry(self.cfg, self._notification_queue, self.logger)

        self._session_id = str(uuid.uuid4())

        # Sticky persona routing
        router_cfg = self.cfg.get("router", {})
        self._current_persona: str = router_cfg.get("default_persona", "kiro")
        self._default_persona: str = self._current_persona
        self._keyword_map: Dict[str, list] = router_cfg.get("keyword_map", {})

        self._kiro_reset_words = {
            "kiro", "cairo", "kyro", "key-ro",
            "default", "nevermind", "reset", "home",
        }

        # Finley sync daemon
        self._finley_sync: Optional[Any] = None
        if _finley_available:
            try:
                finley_cfg = load_finley_config()
                if finley_cfg.get("ynab_token"):
                    interval = int(finley_cfg.get("sync_interval_minutes", 30))
                    self._finley_sync = SyncDaemon(
                        interval_minutes=interval,
                        post_sync_callback=generate_insights,
                        run_on_start=True,
                    )
                    self._finley_sync.start()
                    self.logger.info("Finley YNAB sync daemon started (interval=%dmin)", interval)
            except Exception as exc:
                self.logger.warning("Could not start Finley sync: %s", exc)

    # ── Persona routing ───────────────────────────────────────────────────

    def _route_persona(self, text: str) -> str:
        """Sticky keyword router — same logic as voice pipeline."""
        text_lower = text.lower()

        # Explicit reset back to Kiro
        if any(re.search(r'\b' + re.escape(w) + r'\b', text_lower) for w in self._kiro_reset_words):
            if self._current_persona != self._default_persona:
                self.logger.info("Router: reset → persona=%s", self._default_persona)
                self._current_persona = self._default_persona
            return self._current_persona

        # Check for a new persona keyword
        for persona, keywords in self._keyword_map.items():
            if any(re.search(r'\b' + re.escape(kw) + r'\b', text_lower) for kw in keywords):
                if persona != self._current_persona:
                    self.logger.info("Router: '%s' → persona=%s (was %s)",
                                     text[:40], persona, self._current_persona)
                    self._current_persona = persona
                return self._current_persona

        return self._current_persona

    # ── Notifications ─────────────────────────────────────────────────────

    def _drain_notifications(self) -> None:
        while not self._notification_queue.empty():
            try:
                msg = self._notification_queue.get_nowait()
                print(f"\n{_C_YELLOW}🔔 {msg}{_C_RESET}\n")
            except queue.Empty:
                break

    # ── Slash commands ────────────────────────────────────────────────────

    def _handle_slash(self, text: str) -> bool:
        """Handle slash commands. Returns True if consumed."""
        parts = text.strip().split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit", "/q"):
            print(f"\n{_C_DIM}Goodbye.{_C_RESET}")
            sys.exit(0)

        elif cmd == "/help":
            self._print_help()

        elif cmd == "/personas":
            names = sorted(self.llm._PERSONA_PROMPTS.keys())
            active = self._current_persona
            print(f"\n{_C_BOLD}Available personas:{_C_RESET}")
            for n in names:
                marker = " ← active" if n == active else ""
                c = _pc(n)
                print(f"  {c}{n}{_C_RESET}{_C_DIM}{marker}{_C_RESET}")
            print()

        elif cmd == "/persona":
            if not arg:
                print(f"{_C_DIM}Current persona: {_pc(self._current_persona)}{self._current_persona}{_C_RESET}")
            elif arg in self.llm._PERSONA_PROMPTS:
                old = self._current_persona
                self._current_persona = arg
                print(f"{_C_DIM}Switched: {old} → {_pc(arg)}{arg}{_C_RESET}")
            else:
                print(f"{_C_RED}Unknown persona '{arg}'. Use /personas to list.{_C_RESET}")

        elif cmd == "/reset":
            old = self._current_persona
            self._current_persona = self._default_persona
            print(f"{_C_DIM}Reset: {old} → {_pc(self._default_persona)}{self._default_persona}{_C_RESET}")

        elif cmd == "/history":
            turns = self.memory.recent_turns(self._session_id)
            if not turns:
                print(f"{_C_DIM}No conversation history this session.{_C_RESET}")
            else:
                print(f"\n{_C_BOLD}Recent history ({len(turns)} messages):{_C_RESET}")
                for t in turns:
                    role = t["role"]
                    content = t["content"][:120]
                    if role == "user":
                        print(f"  {_C_GREEN}You:{_C_RESET} {content}")
                    else:
                        print(f"  {_C_CYAN}AI:{_C_RESET} {content}")
                print()

        elif cmd == "/clear":
            # Start a fresh session to clear history
            self._session_id = str(uuid.uuid4())
            print(f"{_C_DIM}Conversation history cleared (new session).{_C_RESET}")

        else:
            print(f"{_C_DIM}Unknown command '{cmd}'. Type /help for options.{_C_RESET}")

        return True

    def _print_help(self) -> None:
        print(f"""
{_C_BOLD}Kiro CLI — Commands{_C_RESET}
  {_C_CYAN}/persona <name>{_C_RESET}  — switch persona (e.g. /persona jack)
  {_C_CYAN}/reset{_C_RESET}           — reset to default persona (kiro)
  {_C_CYAN}/personas{_C_RESET}        — list available personas
  {_C_CYAN}/history{_C_RESET}         — show recent conversation history
  {_C_CYAN}/clear{_C_RESET}           — clear conversation history
  {_C_CYAN}/help{_C_RESET}            — show this help
  {_C_CYAN}/quit{_C_RESET}            — exit

{_C_DIM}Persona switching also works via keywords in your message
(e.g. mentioning "jack" or "grow" switches to Jack).{_C_RESET}
""")

    # ── Main turn ─────────────────────────────────────────────────────────

    def turn(self, user_text: str) -> str:
        """Process one user turn. Returns full assistant response."""
        self._drain_notifications()

        # Memory command interception
        mem_cmd = self.memory.parse_command(user_text)
        if mem_cmd:
            action, payload = mem_cmd
            if action == "remember":
                self.memory.remember(payload)
                reply = f"Got it, I'll remember that {payload}."
            else:
                count = self.memory.forget(payload)
                reply = (
                    f"Done, I've forgotten {count} item{'s' if count != 1 else ''} about {payload}."
                    if count else
                    f"I didn't find anything about {payload} to forget."
                )
            print(f"{_pc(self._current_persona)}{reply}{_C_RESET}")
            return reply

        # Route persona
        persona = self._route_persona(user_text)

        # Memory layers
        history = self.memory.recent_turns(self._session_id)
        memory_context = self.memory.retrieve(user_text)

        # Tool schemas for this persona
        tool_schemas = self.tools.schemas(persona=persona)

        # Stream response token-by-token to terminal
        colour = _pc(persona)
        print(f"{colour}{_C_BOLD}{persona}:{_C_RESET} {colour}", end="", flush=True)

        full_reply = []
        for token in self.llm.stream_tokens(
            user_text,
            persona=persona,
            history=history,
            memory_context=memory_context,
            tools=tool_schemas or None,
            tool_fn=self.tools.execute if tool_schemas else None,
        ):
            print(token, end="", flush=True)
            full_reply.append(token)

        print(_C_RESET)  # end colour + newline

        reply = "".join(full_reply).strip()
        self.memory.store_turn(user_text, reply, self._session_id)
        return reply

    # ── REPL ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Main read-eval-print loop."""
        print(f"""
{_C_BOLD}╔═══════════════════════════════════════╗
║          Kiro CLI  v1.0               ║
║   Type /help for commands, /quit exit ║
╚═══════════════════════════════════════╝{_C_RESET}
""")
        self.logger.info("CLI session started. Session: %s", self._session_id)

        while True:
            try:
                self._drain_notifications()
                persona = self._current_persona
                prompt = f"{_C_BOLD}{_pc(persona)}[{persona}]{_C_RESET} {_C_GREEN}▸{_C_RESET} "
                user_text = input(prompt).strip()

                if not user_text:
                    continue

                if user_text.startswith("/"):
                    self._handle_slash(user_text)
                    continue

                self.turn(user_text)
                print()  # blank line between turns

            except (KeyboardInterrupt, EOFError):
                print(f"\n{_C_DIM}Goodbye.{_C_RESET}")
                break


# ── Entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Kiro text-based CLI")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    try:
        cli = KiroCLI(args.config)
    except Exception as exc:
        print(f"{_C_RED}Error initializing: {exc}{_C_RESET}", file=sys.stderr)
        sys.exit(1)

    cli.run()


if __name__ == "__main__":
    main()
