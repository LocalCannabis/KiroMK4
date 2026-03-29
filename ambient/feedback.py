"""
ambient/feedback.py — Briefing feedback capture and calibration.

Handles Tim's feedback on briefings and adjusts ambient config over time.

Feedback types:
- "helpful" — good, keep doing this
- "too_long" — reduce max insights / raise priority threshold
- "missed_something" — lower priority threshold / broaden coverage
- "irrelevant" — raise priority threshold / narrow coverage

Integrates with the voice pipeline: "Kiro, that was too long" →
feedback captured on the most recent briefing.

Usage (from Kiro's main pipeline):
    from ambient.feedback import record_feedback, parse_feedback_intent
    
    intent = parse_feedback_intent("Kiro, that was too long")
    if intent:
        record_feedback(intent["feedback"], intent.get("notes"))
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from ambient.db import AmbientDB

logger = logging.getLogger("ambient.feedback")

# ── Feedback intent patterns ────────────────────────────────────────────────

FEEDBACK_PATTERNS = [
    # Positive
    (r"(?:that was |briefing was )?helpful", "helpful"),
    (r"good (?:briefing|update|morning)", "helpful"),
    (r"perfect|exactly what i needed", "helpful"),
    (r"thanks|thank you", "helpful"),

    # Too long
    (r"too (?:long|much|many|verbose)", "too_long"),
    (r"shorter|more concise|cut it down", "too_long"),
    (r"that was a lot", "too_long"),
    (r"too much (?:info|information|noise)", "too_long"),

    # Missed something
    (r"miss(?:ed)? (?:something|anything)", "missed_something"),
    (r"what about|you forgot|you didn'?t mention", "missed_something"),
    (r"there was also|also wanted to know", "missed_something"),

    # Irrelevant
    (r"irrelevant|didn'?t need (?:that|to know)", "irrelevant"),
    (r"don'?t care about|not important", "irrelevant"),
    (r"waste of time|useless", "irrelevant"),
]

# ── Config adjustment rules ─────────────────────────────────────────────────

ADJUSTMENT_RULES = {
    "too_long": {
        "max_insights_per_briefing": -1,   # Reduce max insights
        "priority_threshold": -1,           # Raise bar (lower threshold = fewer insights)
    },
    "missed_something": {
        "priority_threshold": +1,           # Lower bar (higher threshold = more insights)
    },
    "irrelevant": {
        "priority_threshold": -1,           # Raise bar
    },
    # "helpful" doesn't change config — it reinforces current settings
}


def parse_feedback_intent(text: str) -> Optional[Dict[str, str]]:
    """
    Parse natural language feedback into a structured intent.

    Returns dict with 'feedback' key, or None if not a feedback statement.
    """
    text_lower = text.lower().strip()

    for pattern, feedback_type in FEEDBACK_PATTERNS:
        if re.search(pattern, text_lower):
            return {
                "feedback": feedback_type,
                "notes": text,
            }

    return None


def record_feedback(
    feedback: str,
    notes: Optional[str] = None,
    db: Optional[AmbientDB] = None,
) -> bool:
    """
    Record feedback on the most recent briefing and optionally
    adjust ambient config based on feedback patterns.

    Returns True if feedback was recorded successfully.
    """
    if db is None:
        db = AmbientDB()

    # Find the most recent briefing
    last_briefing = db.get_last_briefing()
    if not last_briefing:
        logger.info("No recent briefing to attach feedback to")
        return False

    briefing_id = last_briefing["id"]

    # Record feedback
    db.record_briefing_feedback(briefing_id, feedback, notes)
    logger.info("Recorded feedback '%s' on briefing #%d", feedback, briefing_id)

    # Check if we should adjust config
    _maybe_adjust_config(db, feedback)

    return True


def _maybe_adjust_config(db: AmbientDB, feedback: str) -> None:
    """
    Check recent feedback patterns and adjust config if there's a trend.

    Only adjusts if the same feedback type has been given 3+ times
    in the last 10 briefings.
    """
    # Get recent briefings to check for feedback patterns
    conn = db._conn()
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT feedback, COUNT(*) as cnt
                FROM kiro_briefings
                WHERE feedback IS NOT NULL
                  AND delivered_at > NOW() - INTERVAL '7 days'
                GROUP BY feedback
                ORDER BY cnt DESC
            """)
            feedback_counts = {row["feedback"]: row["cnt"] for row in cur.fetchall()}
    finally:
        db._put(conn)

    # Check if any feedback type has hit 3+ occurrences
    adjustments = ADJUSTMENT_RULES.get(feedback, {})
    if not adjustments:
        return

    count = feedback_counts.get(feedback, 0)
    if count < 3:
        return

    logger.info("Feedback pattern detected: '%s' (%d times in 7 days) — adjusting config",
                feedback, count)

    for config_key, delta in adjustments.items():
        current = db.get_config(config_key)
        if current is not None and isinstance(current, (int, float)):
            new_value = current + delta
            # Clamp to reasonable ranges
            if config_key == "priority_threshold":
                new_value = max(2, min(9, new_value))
            elif config_key == "max_insights_per_briefing":
                new_value = max(3, min(15, new_value))

            if new_value != current:
                db.set_config(config_key, new_value)
                logger.info("Adjusted %s: %s → %s (based on '%s' feedback)",
                           config_key, current, new_value, feedback)

    db.log("feedback", "INFO", f"Config adjusted based on '{feedback}' feedback pattern",
           {"feedback": feedback, "count": count, "adjustments": adjustments})
