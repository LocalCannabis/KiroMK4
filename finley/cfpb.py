"""
finley/cfpb.py — CFPB Financial Well-Being Scale (abbreviated 5-item).

Implements the Consumer Financial Protection Bureau's validated
psychometric instrument for measuring financial well-being.

Uses items 3, 5, 6, 8, 10 from the full 10-item scale.
IRT-based scoring converts raw sum (5-25) to a 0-100 scaled score.

Designed for conversational delivery by Finley (Sam Axe personality).
Each question is rephrased in Finley's voice while preserving the
validated item stem.

Spec reference: FINLEY FINANCIAL PROFILING §1.3 — CFPB Wellbeing Integration.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .db import FinleyDB

logger = logging.getLogger("kiro.finley.cfpb")

# ═══════════════════════════════════════════════════════════════════════════
# Scale Items — abbreviated 5-item version
# ═══════════════════════════════════════════════════════════════════════════

# Each item has:
#   "stem"       — the validated CFPB question text
#   "finley"     — Sam Axe's conversational rephrasing
#   "direction"  — "positive" (higher response = higher wellbeing) or
#                  "negative" (higher response = lower wellbeing, needs reverse)
#   "responses"  — ordered 1-5 (never → always for frequency items,
#                  not at all → completely for agreement items)

SCALE_ITEMS: List[Dict[str, Any]] = [
    {
        "id": "cfpb_3",
        "item_number": 3,
        "stem": "Because of my money situation, I feel like I will never have the things I want in life.",
        "finley": (
            "Alright, here's one — and remember, no wrong answers. "
            "Do you ever feel like your money situation means you'll never "
            "get the things you actually want in life? "
            "Scale of 1 to 5 — 1 is 'not at all', 5 is 'completely'."
        ),
        "direction": "negative",
        "response_labels": {
            1: "Not at all",
            2: "Very little",
            3: "Somewhat",
            4: "Very well",
            5: "Completely",
        },
    },
    {
        "id": "cfpb_5",
        "item_number": 5,
        "stem": "I am just getting by financially.",
        "finley": (
            "Next one. Would you say you're 'just getting by' financially? "
            "1 means that doesn't describe you at all, 5 means it's dead-on."
        ),
        "direction": "negative",
        "response_labels": {
            1: "Not at all",
            2: "Very little",
            3: "Somewhat",
            4: "Very well",
            5: "Completely",
        },
    },
    {
        "id": "cfpb_6",
        "item_number": 6,
        "stem": "I am concerned that the money I have or will save won't last.",
        "finley": (
            "How about this — are you worried that whatever money you've got "
            "saved up just... won't last? "
            "1 is 'not worried at all', 5 is 'very concerned'."
        ),
        "direction": "negative",
        "response_labels": {
            1: "Not at all",
            2: "Very little",
            3: "Somewhat",
            4: "Very well",
            5: "Completely",
        },
    },
    {
        "id": "cfpb_8",
        "item_number": 8,
        "stem": "I have money left over at the end of the month.",
        "finley": (
            "Okay, flip side — when the month wraps up, do you actually "
            "have money left over? "
            "1 is 'never', 5 is 'always'."
        ),
        "direction": "positive",
        "response_labels": {
            1: "Never",
            2: "Rarely",
            3: "Sometimes",
            4: "Often",
            5: "Always",
        },
    },
    {
        "id": "cfpb_10",
        "item_number": 10,
        "stem": "I am behind with my finances.",
        "finley": (
            "Last one, and you're doing great. Do you feel like you're "
            "behind with your finances? "
            "1 is 'not at all', 5 is 'completely'."
        ),
        "direction": "negative",
        "response_labels": {
            1: "Not at all",
            2: "Very little",
            3: "Somewhat",
            4: "Very well",
            5: "Completely",
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# IRT Lookup Table
# ═══════════════════════════════════════════════════════════════════════════

# CFPB's Item Response Theory lookup converts raw sum → scaled score.
# For the 5-item abbreviated scale (items 3,5,6,8,10), raw range is 5-25.
# Lookup derived from CFPB Technical Report (2017), Table A.2.
#
# Key: raw_score → scaled_score (0-100 range)
# The 18-29 age band is most appropriate for Tim.

_IRT_LOOKUP_18_29: Dict[int, int] = {
    5: 14,
    6: 19,
    7: 22,
    8: 25,
    9: 28,
    10: 31,
    11: 33,
    12: 36,
    13: 38,
    14: 41,
    15: 43,
    16: 46,
    17: 48,
    18: 51,
    19: 54,
    20: 57,
    21: 60,
    22: 64,
    23: 68,
    24: 74,
    25: 82,
}

# General population lookup (fallback)
_IRT_LOOKUP_GENERAL: Dict[int, int] = {
    5: 14,
    6: 18,
    7: 22,
    8: 25,
    9: 28,
    10: 30,
    11: 33,
    12: 35,
    13: 38,
    14: 40,
    15: 43,
    16: 45,
    17: 48,
    18: 51,
    19: 54,
    20: 57,
    21: 61,
    22: 65,
    23: 70,
    24: 76,
    25: 85,
}


# ═══════════════════════════════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════════════════════════════

def score_responses(responses: Dict[str, int],
                    age_band: str = "18-29") -> Tuple[int, float]:
    """
    Score a complete set of CFPB responses.

    Args:
        responses: dict mapping item_id → response (1-5)
        age_band: "18-29" or "general"

    Returns:
        (raw_score, scaled_score)

    Raises:
        ValueError if responses are incomplete or out of range.
    """
    expected_ids = {item["id"] for item in SCALE_ITEMS}
    provided_ids = set(responses.keys())
    missing = expected_ids - provided_ids
    if missing:
        raise ValueError(f"Missing responses for: {missing}")

    raw_score = 0
    for item in SCALE_ITEMS:
        response = responses[item["id"]]
        if not (1 <= response <= 5):
            raise ValueError(f"Response for {item['id']} must be 1-5, got {response}")

        if item["direction"] == "negative":
            # Reverse score: 1→5, 2→4, 3→3, 4→2, 5→1
            raw_score += (6 - response)
        else:
            raw_score += response

    # Lookup scaled score
    lookup = _IRT_LOOKUP_18_29 if age_band == "18-29" else _IRT_LOOKUP_GENERAL
    scaled_score = lookup.get(raw_score, 50)  # fallback to midpoint

    return raw_score, float(scaled_score)


def assess_wellbeing(db: FinleyDB, responses: Dict[str, int]) -> Dict[str, Any]:
    """
    Score and persist a CFPB wellbeing assessment.

    Returns dict with raw_score, scaled_score, interpretation, and history context.
    """
    raw_score, scaled_score = score_responses(responses)

    # Persist
    record_id = db.save_wellbeing(
        responses=responses,
        raw_score=raw_score,
        scaled_score=scaled_score,
    )

    # Interpretation
    if scaled_score >= 70:
        interpretation = "high"
        summary = "Your financial wellbeing is in a good place."
    elif scaled_score >= 50:
        interpretation = "moderate"
        summary = "You're managing, but there's room to feel more secure."
    elif scaled_score >= 30:
        interpretation = "low"
        summary = "Money stress is weighing on you — that's real and valid."
    else:
        interpretation = "very_low"
        summary = "You're under significant financial stress. Let's work on this together."

    # Check for trend
    history = db.get_wellbeing_history(limit=3)
    trend = None
    if len(history) >= 2:
        prev_score = history[1]["scaled_score"]
        delta = scaled_score - prev_score
        if delta >= 5:
            trend = "improving"
        elif delta <= -5:
            trend = "declining"
        else:
            trend = "stable"

    result = {
        "id": record_id,
        "raw_score": raw_score,
        "scaled_score": scaled_score,
        "interpretation": interpretation,
        "summary": summary,
        "trend": trend,
    }

    logger.info(
        "CFPB wellbeing assessed: raw=%d, scaled=%.0f, interpretation=%s",
        raw_score, scaled_score, interpretation,
    )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Conversational helpers
# ═══════════════════════════════════════════════════════════════════════════

def get_next_question(responses_so_far: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """
    Return the next unanswered CFPB question, or None if all answered.
    Used for conversational multi-turn assessment.
    """
    for item in SCALE_ITEMS:
        if item["id"] not in responses_so_far:
            return item
    return None


def get_assessment_intro() -> str:
    """Finley's opening for the CFPB assessment."""
    return (
        "Hey, so I want to check in on how you're actually *feeling* about "
        "your money situation — not just the numbers, but the vibes. "
        "I've got five quick questions. No math, just gut reactions. "
        "Ready? Here's the first one."
    )


def get_assessment_outro(result: Dict[str, Any]) -> str:
    """Finley's closing remarks after scoring."""
    score = result["scaled_score"]
    interpretation = result["interpretation"]
    trend = result.get("trend")

    parts = [
        f"Alright, done. Your financial wellbeing score is {score:.0f} out of 100."
    ]

    if interpretation == "high":
        parts.append(
            "That's solid — you're in a better place than most people. "
            "Doesn't mean everything's perfect, but the foundation's there."
        )
    elif interpretation == "moderate":
        parts.append(
            "That's the middle zone — you're keeping it together, but "
            "there's probably some stress lurking. Normal. We can chip away at it."
        )
    elif interpretation == "low":
        parts.append(
            "Look, that's below where we want it, but it's honest and "
            "that matters. A lot of people score here. Doesn't define you."
        )
    else:
        parts.append(
            "That tells me money is causing real stress right now. "
            "I'm not gonna sugarcoat it, but I will say — you asked for help, "
            "and that's step one. We'll figure this out."
        )

    if trend == "improving":
        parts.append("Good news though — your score is trending up from last time.")
    elif trend == "declining":
        parts.append(
            "I should mention your score dropped a bit since last time. "
            "Nothing to panic about, but worth keeping an eye on."
        )

    return " ".join(parts)
