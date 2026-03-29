"""
audio/emotion_tagger.py — Orpheus emotion tag post-processor.

Sits between LLM output and the Orpheus API call.
Reads persona voice config and emotion keyword rules from PostgreSQL.
Formats output as:  {voice}: {<emotion_tag>} {text}

Per ORPHEUS_TTS_INTEGRATION_SPEC.md §4.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)

VALID_TAGS = {"laughter", "sigh", "excited", "whisper", "yawn", "gasp", "warm",
              "calm", "thoughtful", "confident", "encouraging", "neutral"}
TAG_RE = re.compile(r"<(" + "|".join(VALID_TAGS) + r")>")

# ── DB helpers ──────────────────────────────────────────────────────────────

def _get_pool():
    """Lazy import — only needed when Orpheus is active."""
    try:
        import psycopg2
        import psycopg2.pool
        return psycopg2.pool.SimpleConnectionPool(
            minconn=1, maxconn=3,
            host=os.getenv("PGHOST", "localhost"),
            port=int(os.getenv("PGPORT", "5432")),
            dbname=os.getenv("PGDATABASE", "kiro"),
            user=os.getenv("PGUSER", "kiro"),
            password=os.getenv("JACK_DB_PASSWORD") or os.getenv("PGPASSWORD", "kiro"),
        )
    except Exception as exc:
        log.warning("emotion_tagger: cannot connect to DB (%s)", exc)
        return None

_pool = None

def _pool_conn():
    global _pool
    if _pool is None:
        _pool = _get_pool()
    if _pool is None:
        return None
    return _pool.getconn()

def _release(conn):
    global _pool
    if _pool and conn:
        try:
            _pool.putconn(conn)
        except Exception:
            pass


@lru_cache(maxsize=16)
def _get_voice_config(persona: str) -> dict:
    """
    Fetch voice config for persona from kiro_voices.
    Cached — refreshes on process restart or after TTL (see below).
    Falls back to sensible defaults if DB is unavailable.
    """
    defaults = {
        "orpheus_voice": "leah",
        "clone_ref_path": None,
        "clone_ref_text": None,
        "default_emotion": "neutral",
        "speed": 1.0,
        "temperature": 0.6,
        "enabled": True,
    }
    conn = _pool_conn()
    if conn is None:
        return defaults
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT orpheus_voice, clone_ref_path, clone_ref_text,
                       default_emotion, speed, temperature, enabled
                FROM kiro_voices WHERE persona = %s
                """,
                (persona,),
            )
            row = cur.fetchone()
        if not row:
            return defaults
        return {
            "orpheus_voice":  row[0],
            "clone_ref_path": row[1],
            "clone_ref_text": row[2],
            "default_emotion": row[3] or "neutral",
            "speed":          float(row[4] or 1.0),
            "temperature":    float(row[5] or 0.6),
            "enabled":        bool(row[6]),
        }
    except Exception as exc:
        log.warning("emotion_tagger: DB query failed (%s)", exc)
        return defaults
    finally:
        _release(conn)


def _get_emotion_rules() -> list[dict]:
    """Fetch all enabled emotion keyword rules, ordered by descending priority."""
    conn = _pool_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT keyword, emotion_tag, priority
                FROM kiro_emotion_rules
                WHERE enabled = TRUE
                ORDER BY priority DESC
                """
            )
            return [{"keyword": r[0], "emotion_tag": r[1], "priority": r[2]}
                    for r in cur.fetchall()]
    except Exception as exc:
        log.warning("emotion_tagger: emotion rules query failed (%s)", exc)
        return []
    finally:
        _release(conn)


def invalidate_cache():
    """Call after updating kiro_voices in the DB to force a re-read."""
    _get_voice_config.cache_clear()


# ── Core tagger ─────────────────────────────────────────────────────────────

def tag_response(persona: str, text: str) -> str:
    """
    Inject an Orpheus emotion tag into the LLM response text.

    Steps:
      1. If text already contains a valid Orpheus tag, pass through.
      2. Check emotion keyword rules; inject matching tag before the
         sentence that contains the keyword.
      3. Fall back to persona's default_emotion.

    Returns the emotion-tagged text (e.g. "<confident> Hello Tim").
    The Orpheus server's own format_prompt() handles prepending the
    voice identifier — we do NOT add it here to avoid double-prefix.
    """
    config = _get_voice_config(persona)

    # Step 1: pass-through if already tagged
    if TAG_RE.search(text):
        return text

    # Step 2: keyword rules (first match wins)
    rules = _get_emotion_rules()
    tagged_text = text
    for rule in rules:
        if rule["keyword"].lower() in text.lower():
            tagged_text = f"<{rule['emotion_tag']}> {text}"
            break

    # Step 3: default emotion fallback
    if not TAG_RE.search(tagged_text):
        emotion = config["default_emotion"]
        if emotion and emotion != "neutral":
            tagged_text = f"<{emotion}> {tagged_text}"

    return tagged_text


def get_voice_config(persona: str) -> dict:
    """Public accessor for voice config (used by orpheus_client)."""
    return _get_voice_config(persona)
