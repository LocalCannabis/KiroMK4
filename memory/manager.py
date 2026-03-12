"""
memory/manager.py — Kiro memory orchestration layer.

Memory layers implemented here:
  L0: In-context rolling window — last N turns injected into the messages list
      (managed in kiro.py; FactStore.get_recent_turns supplies the data)
  L1: ChromaDB vector store — every turn embedded + stored for similarity retrieval
  L2: SQLite facts table — explicit "Remember that..." facts, survives forever

Public API:
  parse_command(text)        → ('remember'|'forget', payload) or None
  store_turn(user, asst, id) → embed + persist turn
  remember(fact_text)        → store explicit user fact
  forget(query)              → delete matching facts, return count
  retrieve(query)            → formatted memory context string for prompt injection
  recent_turns(session_id)   → last N turns for L0 context window
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REMEMBER_RE = re.compile(
    r"^(?:hey kiro,?\s+)?(?:please\s+)?remember\s+(?:that\s+)?(.+)",
    re.IGNORECASE,
)
FORGET_RE = re.compile(
    r"^(?:hey kiro,?\s+)?(?:please\s+)?forget\s+(?:(?:about|that)\s+)?(.+)",
    re.IGNORECASE,
)


class MemoryManager:
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        mem_cfg = cfg.get("memory", {})
        self.enabled = mem_cfg.get("enabled", False)

        if not self.enabled:
            self.logger.info("Memory system disabled (memory.enabled=false in config).")
            self._vector = None
            self._facts = None
            return

        from .vectorstore import VectorStore
        from .facts import FactStore

        db_path = Path(mem_cfg.get("sqlite_path", "./data/kiro.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        vec_cfg = mem_cfg.get("vector", {})
        self._vector = VectorStore(vec_cfg, logger)
        self._facts = FactStore(str(db_path), logger)
        self._top_k = int(mem_cfg.get("retrieval", {}).get("top_k", 5))
        self._l0_turns = 6  # number of recent turns to include in context window

        self.logger.info("Memory system online.")

    # ------------------------------------------------------------------
    # Command parsing
    # ------------------------------------------------------------------

    def parse_command(self, text: str) -> Optional[tuple[str, str]]:
        """
        Detect explicit memory commands in a user utterance.
        Returns ('remember', fact) or ('forget', query) or None.
        """
        m = REMEMBER_RE.match(text.strip())
        if m:
            return ("remember", m.group(1).strip().rstrip("."))
        m = FORGET_RE.match(text.strip())
        if m:
            return ("forget", m.group(1).strip().rstrip("."))
        return None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def store_turn(self, user_text: str, assistant_text: str, session_id: str) -> None:
        if not self.enabled:
            return
        ts = datetime.now().isoformat()
        combined = f"User: {user_text}\nKiro: {assistant_text}"
        self._vector.add(
            combined,
            metadata={"type": "turn", "timestamp": ts, "session_id": session_id},
        )
        self._facts.store_turn(session_id, user_text, assistant_text, ts)

    def remember(self, fact_text: str) -> None:
        if not self.enabled:
            return
        ts = datetime.now().isoformat()
        self._facts.add_fact(fact_text, source="user", timestamp=ts)
        self._vector.add(
            f"[FACT] {fact_text}",
            metadata={"type": "fact", "timestamp": ts},
        )
        self.logger.info("Memory stored: %s", fact_text)

    def forget(self, query: str) -> int:
        if not self.enabled:
            return 0
        count = self._facts.delete_matching(query)
        self.logger.info("Memory forgot %d fact(s) matching '%s'", count, query)
        return count

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def retrieve(self, query: str) -> str:
        """
        Embed the query and return a formatted string of the top-K most
        relevant memories, ready to inject into the system prompt.
        Returns empty string if memory is disabled or no results.
        """
        if not self.enabled:
            return ""
        results = self._vector.query(query, n_results=self._top_k)
        if not results:
            return ""
        lines = ["[Memory context — use naturally, don't announce you're using memory]"]
        for doc in results:
            lines.append(f"- {doc}")
        return "\n".join(lines)

    def recent_turns(self, session_id: str) -> List[Dict[str, str]]:
        """Return recent turn history for L0 context window (OpenAI messages format)."""
        if not self.enabled or self._facts is None:
            return []
        return self._facts.get_recent_turns(session_id, n=self._l0_turns)
