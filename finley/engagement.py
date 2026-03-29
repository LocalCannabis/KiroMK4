"""
finley/engagement.py — Proactive Engagement Engine.

Determines when and how Finley should reach out to Tim without being
asked. Enforces strict anti-nagging rules so it never feels pushy.

Trigger types:
    TIME-BASED:   payday_ritual, weekly_pulse, month_end_review, cfpb_checkin
    EVENT-BASED:  large_expense, payday_spike, subscription_creep,
                  adhd_tax, positive_pattern, buffer_milestone
    INSIGHT:      category_drift, income_instability, progress_stall

Anti-nagging rules (spec §3.2):
    - Max 2 proactive messages per day
    - 4-hour minimum gap between messages
    - 72-hour cooldown on same trigger_type
    - 7-day cooldown after a declined suggestion
    - Tone NEVER escalates

Spec reference: FINLEY FINANCIAL PROFILING §3 — Proactive Engagement Engine.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .db import FinleyDB, milliunits_to_dollars

logger = logging.getLogger("kiro.finley.engagement")


# ═══════════════════════════════════════════════════════════════════════════
# Anti-Nagging Gate
# ═══════════════════════════════════════════════════════════════════════════

class AntiNagging:
    """Enforce all cooldown and rate-limit rules before any engagement fires."""

    def __init__(self, db: FinleyDB):
        self.db = db

    def can_engage(self, trigger_type: str) -> Tuple[bool, str]:
        """
        Check whether a proactive engagement is allowed right now.

        Returns (allowed: bool, reason: str).
        """
        # Load configurable limits from DB
        max_per_day = self.db.get_config("anti_nag_max_daily", 2)
        min_gap_hours = self.db.get_config("anti_nag_min_gap_hours", 4)
        same_trigger_cooldown_hours = self.db.get_config(
            "anti_nag_same_trigger_cooldown_hours", 72
        )
        declined_cooldown_days = self.db.get_config(
            "anti_nag_declined_cooldown_days", 7
        )

        recent_24h = self.db.get_recent_engagements(hours=24)

        # Rule 1: Max per day
        if len(recent_24h) >= max_per_day:
            return False, f"Already sent {len(recent_24h)} engagements today (max {max_per_day})"

        # Rule 2: Minimum gap
        if recent_24h:
            latest = recent_24h[0]  # ordered DESC
            latest_time = latest.get("created_at")
            if latest_time:
                if isinstance(latest_time, str):
                    latest_time = datetime.fromisoformat(latest_time)
                gap = datetime.utcnow() - latest_time.replace(tzinfo=None)
                if gap < timedelta(hours=min_gap_hours):
                    mins_left = int((timedelta(hours=min_gap_hours) - gap).total_seconds() / 60)
                    return False, f"Need {mins_left} more minutes before next engagement"

        # Rule 3: Same trigger cooldown
        recent_long = self.db.get_recent_engagements(hours=same_trigger_cooldown_hours)
        same_trigger_recent = [
            e for e in recent_long if e.get("trigger_type") == trigger_type
        ]
        if same_trigger_recent:
            return False, f"Same trigger '{trigger_type}' fired within {same_trigger_cooldown_hours}h"

        # Rule 4: Declined suggestion cooldown
        recent_week = self.db.get_recent_engagements(hours=declined_cooldown_days * 24)
        declined = [
            e for e in recent_week
            if e.get("acknowledged") is False
            or (e.get("response_summary") or "").lower() in ("no", "not now", "skip", "declined")
        ]
        if declined:
            return False, "Recent declined engagement — backing off"

        return True, "clear"


# ═══════════════════════════════════════════════════════════════════════════
# Trigger Evaluators
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_time_triggers(db: FinleyDB, profile: dict) -> List[Dict[str, Any]]:
    """
    Check time-based triggers. Called on a schedule (e.g., hourly cron).

    Returns list of candidate engagements (not yet filtered by anti-nagging).
    """
    candidates: List[Dict[str, Any]] = []
    today = date.today()
    now = datetime.now()

    # --- Payday Ritual (bi-weekly, day after detected income) ---
    # Check if income arrived in the last 24h
    yesterday = (today - timedelta(days=1)).isoformat()
    recent_txns = db.get_transactions(since_date=yesterday, until_date=today.isoformat())
    income_txns = [
        t for t in recent_txns
        if t["amount"] > 0 and not t.get("transfer_account_id")
    ]
    if income_txns:
        total_income = sum(t["amount"] for t in income_txns)
        candidates.append({
            "trigger_type": "payday_ritual",
            "trigger_detail": {
                "income_amount": milliunits_to_dollars(total_income),
                "sources": [t.get("payee_name", "Unknown") for t in income_txns],
            },
            "message_template": "payday_ritual",
        })

    # --- Weekly Pulse (Monday mornings) ---
    if today.weekday() == 0 and 8 <= now.hour <= 11:
        candidates.append({
            "trigger_type": "weekly_pulse",
            "trigger_detail": {"day": today.isoformat()},
            "message_template": "weekly_pulse",
        })

    # --- Month-End Review (last 3 days of month) ---
    next_month = today.replace(day=28) + timedelta(days=4)
    last_day = next_month - timedelta(days=next_month.day)
    if today >= last_day - timedelta(days=2):
        candidates.append({
            "trigger_type": "month_end_review",
            "trigger_detail": {"month": today.strftime("%Y-%m")},
            "message_template": "month_end_review",
        })

    # --- CFPB Check-in (quarterly — check if >90 days since last) ---
    wb = db.get_latest_wellbeing()
    if wb:
        assessed = wb.get("assessed_at")
        if assessed:
            if isinstance(assessed, str):
                assessed = datetime.fromisoformat(assessed)
            days_since = (datetime.utcnow() - assessed.replace(tzinfo=None)).days
            if days_since >= 90:
                candidates.append({
                    "trigger_type": "cfpb_checkin",
                    "trigger_detail": {"days_since_last": days_since},
                    "message_template": "cfpb_checkin",
                })
    else:
        # Never assessed — suggest first one
        candidates.append({
            "trigger_type": "cfpb_checkin",
            "trigger_detail": {"first_time": True},
            "message_template": "cfpb_checkin",
        })

    return candidates


def evaluate_event_triggers(db: FinleyDB, profile: dict) -> List[Dict[str, Any]]:
    """
    Check event-based triggers. Called after each sync.
    """
    candidates: List[Dict[str, Any]] = []
    vs = profile.get("vital_signs", {})
    bh = profile.get("behavioral", {})

    # --- Large Unplanned Expense ---
    threshold = db.get_config("large_txn_threshold", 100000)  # milliunits
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    recent_txns = db.get_transactions(since_date=yesterday, until_date=today.isoformat())
    large_outflows = [
        t for t in recent_txns
        if t["amount"] < 0 and abs(t["amount"]) >= threshold
    ]
    for t in large_outflows:
        candidates.append({
            "trigger_type": "large_expense",
            "trigger_detail": {
                "payee": t.get("payee_name", "Unknown"),
                "amount": milliunits_to_dollars(abs(t["amount"])),
                "txn_id": t["id"],
            },
            "message_template": "large_expense",
        })

    # --- Payday Spike ---
    if bh.get("payday_spike", 1.0) >= 2.0:
        candidates.append({
            "trigger_type": "payday_spike",
            "trigger_detail": {"ratio": bh["payday_spike"]},
            "message_template": "payday_spike",
        })

    # --- Subscription Creep ---
    sub_load = vs.get("subscription_load", 0)
    income = vs.get("monthly_income", 1)
    if income > 0 and sub_load / income > 0.15:
        candidates.append({
            "trigger_type": "subscription_creep",
            "trigger_detail": {
                "subscription_load": sub_load,
                "pct_of_income": round(sub_load / income * 100, 1),
            },
            "message_template": "subscription_creep",
        })

    # --- ADHD Tax ---
    adhd_tax = bh.get("adhd_tax", 0)
    if adhd_tax > 20:
        candidates.append({
            "trigger_type": "adhd_tax",
            "trigger_detail": {"amount": adhd_tax},
            "message_template": "adhd_tax",
        })

    # --- Positive Pattern (celebrate wins) ---
    cfd = vs.get("cash_flow_delta", 0)
    if cfd > 100:
        # Check if this is an improvement
        history = db.get_profile_history(limit=7)
        if len(history) >= 2:
            prev_cfd = (history[1].get("vital_signs") or {}).get("cash_flow_delta", 0)
            if cfd > prev_cfd + 50:
                candidates.append({
                    "trigger_type": "positive_pattern",
                    "trigger_detail": {
                        "current_delta": cfd,
                        "improvement": round(cfd - prev_cfd, 2),
                    },
                    "message_template": "positive_pattern",
                })

    # --- Buffer Milestone ---
    duz = vs.get("days_until_zero", 0)
    milestones = [7, 14, 21, 30, 60]
    for m in milestones:
        if duz >= m:
            # Check if we just crossed this milestone
            history = db.get_profile_history(limit=2)
            if len(history) >= 2:
                prev_duz = (history[1].get("vital_signs") or {}).get("days_until_zero", 0)
                if prev_duz < m <= duz:
                    candidates.append({
                        "trigger_type": "buffer_milestone",
                        "trigger_detail": {"days": m, "current_runway": duz},
                        "message_template": "buffer_milestone",
                    })
                    break  # Only fire the highest newly-crossed milestone

    return candidates


def evaluate_insight_triggers(db: FinleyDB, profile: dict) -> List[Dict[str, Any]]:
    """
    Check insight-based triggers (trend analysis over multiple profiles).
    """
    candidates: List[Dict[str, Any]] = []
    history = db.get_profile_history(limit=14)  # 2 weeks of profiles

    if len(history) < 3:
        return candidates  # Need history for trends

    # --- Category Drift ---
    # Check if variable_expenses jumped >20% from 7-day moving average
    recent_variable = [
        (h.get("vital_signs") or {}).get("variable_expenses", 0)
        for h in history[:7]
    ]
    older_variable = [
        (h.get("vital_signs") or {}).get("variable_expenses", 0)
        for h in history[7:14]
    ]
    if recent_variable and older_variable:
        recent_avg = sum(recent_variable) / len(recent_variable)
        older_avg = sum(older_variable) / len(older_variable)
        if older_avg > 0 and (recent_avg - older_avg) / older_avg > 0.20:
            candidates.append({
                "trigger_type": "category_drift",
                "trigger_detail": {
                    "recent_avg": round(recent_avg, 2),
                    "older_avg": round(older_avg, 2),
                    "pct_increase": round((recent_avg - older_avg) / older_avg * 100, 1),
                },
                "message_template": "category_drift",
            })

    # --- Progress Stall ---
    stages = [h.get("stage", "unknown") for h in history[:7]]
    if len(set(stages)) == 1 and stages[0] in ("distressed", "fragile"):
        candidates.append({
            "trigger_type": "progress_stall",
            "trigger_detail": {
                "stuck_stage": stages[0],
                "days_stuck": len(stages),
            },
            "message_template": "progress_stall",
        })

    return candidates


# ═══════════════════════════════════════════════════════════════════════════
# Message Templates
# ═══════════════════════════════════════════════════════════════════════════

def render_message(template_name: str, detail: dict, profile: dict) -> str:
    """
    Render a proactive engagement message in Finley's voice.

    These are starting points — the LLM will refine them with context.
    """
    stage = profile.get("stage", "unknown")

    templates = {
        "payday_ritual": (
            f"Hey, payday just hit — ${detail.get('income_amount', 0):,.2f} came in"
            f"{' from ' + detail.get('sources', [''])[0] if detail.get('sources') else ''}. "
            "Before that money starts disappearing, want to do a quick 2-minute "
            "check on where things stand?"
        ),
        "weekly_pulse": (
            "Monday check-in. Quick snapshot of your week — "
            "nothing heavy, just keeping eyes on the road."
        ),
        "month_end_review": (
            f"Month's wrapping up. Want a quick look at how {detail.get('month', 'this month')} "
            "played out? I'll keep it tight."
        ),
        "cfpb_checkin": (
            "Hey, it's been a while since we did a financial wellbeing check-in. "
            "Five quick questions, no math, just gut reactions. Want to knock it out?"
            if not detail.get("first_time") else
            "I'd like to do a quick check on how you're *feeling* about your money "
            "situation — not just the numbers. Five questions, takes about 2 minutes. Game?"
        ),
        "large_expense": (
            f"Heads up — ${detail.get('amount', 0):,.2f} just went to "
            f"{detail.get('payee', 'somewhere')}. "
            "Was that planned, or did something come up?"
        ),
        "payday_spike": (
            "Noticed spending tends to spike right after payday — "
            f"about {detail.get('ratio', 2):.1f}x your normal daily rate. "
            "Not judging, just flagging. Want to talk about a payday game plan?"
        ),
        "subscription_creep": (
            f"Your recurring subscriptions are running about ${detail.get('subscription_load', 0):,.2f}/month — "
            f"that's {detail.get('pct_of_income', 0):.0f}% of your income. "
            "Want to review the list? Sometimes there's stuff you forgot about."
        ),
        "adhd_tax": (
            f"Found about ${detail.get('amount', 0):,.2f} in late fees, "
            "overdrafts, and that kind of thing this month. "
            "Not a lecture — just wondering if we can set up some guardrails."
        ),
        "positive_pattern": (
            f"Hey, good news — your cash flow improved by ${detail.get('improvement', 0):,.2f} "
            "this period. Whatever you're doing, it's working."
        ),
        "buffer_milestone": (
            f"You just crossed {detail.get('days', 0)} days of runway. "
            "That's a real milestone. Seriously."
        ),
        "category_drift": (
            f"Variable spending crept up about {detail.get('pct_increase', 0):.0f}% "
            "from your recent average. Not alarming, but worth a glance."
        ),
        "progress_stall": (
            "We've been in the same spot for about a week. "
            "No pressure, but want to talk about what might help move the needle?"
        ),
    }

    msg = templates.get(template_name, f"Financial update: {template_name}")

    # Soften tone for distressed stage
    if stage == "distressed" and template_name not in ("positive_pattern", "buffer_milestone"):
        msg += " No rush — whenever you're ready."

    return msg


# ═══════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════

def check_engagements(db: FinleyDB, profile: dict | None = None) -> List[Dict[str, Any]]:
    """
    Evaluate all triggers and return engagements that pass anti-nagging.

    Called after sync and on schedule. Returns list of engagement dicts
    ready to be delivered (already logged to finley_engagements).
    """
    if profile is None:
        profile = db.get_latest_profile() or {}

    gate = AntiNagging(db)
    fired: List[Dict[str, Any]] = []

    # Collect all candidates
    candidates = []
    candidates.extend(evaluate_time_triggers(db, profile))
    candidates.extend(evaluate_event_triggers(db, profile))
    candidates.extend(evaluate_insight_triggers(db, profile))

    # Filter through anti-nagging and fire
    for candidate in candidates:
        trigger_type = candidate["trigger_type"]
        allowed, reason = gate.can_engage(trigger_type)

        if not allowed:
            logger.debug("Engagement blocked [%s]: %s", trigger_type, reason)
            continue

        # Render message
        message = render_message(
            candidate["message_template"],
            candidate["trigger_detail"],
            profile,
        )

        # Log to database
        eng_id = db.log_engagement(
            trigger_type=trigger_type,
            trigger_detail=candidate["trigger_detail"],
            message_text=message,
        )

        fired.append({
            "id": eng_id,
            "trigger_type": trigger_type,
            "message": message,
            "detail": candidate["trigger_detail"],
        })

        logger.info("Engagement fired [%s]: %s", trigger_type, message[:80])

        # Re-check gate after each fire (may have hit daily limit)
        # Actually, we already committed the log, so the next iteration
        # will see it in get_recent_engagements. But we short-circuit:
        if len(fired) >= gate.db.get_config("anti_nag_max_daily", 2):
            break

    return fired
