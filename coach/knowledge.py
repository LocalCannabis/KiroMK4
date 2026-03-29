"""
coach/knowledge.py — Executive Function Knowledge Base (RAG-ready).

CRUD operations for the coach_knowledge table, topic-based retrieval,
and Tier 1 seed data from three authoritative sources:

    Source 1: GTD (David Allen) — The external capture-and-organize system
    Source 2: Atomic Habits (James Clear) — The habit-building framework
    Source 3: ADHD, EF & Self-Regulation (Russell Barkley, PhD) — The neuroscience

Knowledge Tiers:
    Tier 1 — Foundational frameworks (GTD, Atomic Habits, Barkley EF/ADHD)
    Tier 2 — Technique-specific (task estimation, project breakdown) [future]
    Tier 3 — Context-specific (developer productivity, ADHD coding) [future]
    Tier 4 — Personalised (Tim's own patterns, what works for him) [future]

Spec reference: GOSPEL_KIRO_PERSONA_SYSTEM_SPEC.md — Coach persona.
Seed reference: COACH_SEED_KNOWLEDGE.md
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .db import CoachDB

logger = logging.getLogger("kiro.coach.knowledge")


# ═══════════════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════════════

def add_knowledge(
    db: CoachDB,
    source_name: str,
    source_type: str,
    topic: str,
    content: str,
    tier: int = 1,
    adhd_relevant: bool = True,
    actionability: str = "reference",
) -> int:
    """Insert a knowledge entry. Returns the new row ID."""
    conn = db._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO coach_knowledge
                    (source_name, source_type, topic, content, tier,
                     adhd_relevant, actionability)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (source_name, source_type, topic, content, tier,
                  adhd_relevant, actionability))
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        db._put(conn)


def get_knowledge_by_topic(db: CoachDB, topic: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Retrieve knowledge entries matching a topic (substring match)."""
    import psycopg2.extras
    conn = db._conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM coach_knowledge
                WHERE LOWER(topic) LIKE LOWER(%s)
                ORDER BY tier ASC, created_at DESC
                LIMIT %s
            """, (f"%{topic}%", limit))
            return [dict(r) for r in cur.fetchall()]
    finally:
        db._put(conn)


def get_adhd_knowledge(db: CoachDB, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve ADHD-specific executive function knowledge."""
    import psycopg2.extras
    conn = db._conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM coach_knowledge
                WHERE adhd_relevant = TRUE
                ORDER BY tier ASC, actionability DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        db._put(conn)


def count_knowledge(db: CoachDB) -> Dict[str, int]:
    """Count knowledge entries by tier."""
    conn = db._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT tier, COUNT(*) FROM coach_knowledge
                GROUP BY tier ORDER BY tier
            """)
            return {f"tier_{r[0]}": r[1] for r in cur.fetchall()}
    finally:
        db._put(conn)


# ═══════════════════════════════════════════════════════════════════════════
# Tier 1 Seed Data — From COACH_SEED_KNOWLEDGE.md
# ═══════════════════════════════════════════════════════════════════════════

TIER_1_SEEDS: List[Dict[str, Any]] = [
    # ── GTD: Getting Things Done (David Allen) ────────────────────────
    {
        "source_name": "GTD — Five Stages of Mastering Workflow",
        "source_type": "framework",
        "topic": "gtd,capture,clarify,organize,reflect,engage,workflow",
        "content": (
            "GTD's five stages: (1) CAPTURE — gather everything that catches your attention "
            "into collection tools. Zero open loops in your head. "
            "(2) CLARIFY — for each item: Is it actionable? If not, trash/file/someday. "
            "If yes, define the very next physical action. Under 2 minutes? Do it now. "
            "(3) ORGANIZE — Next Actions by context, Projects list, Waiting For, Calendar "
            "(only time-specific items), Someday/Maybe, Reference. "
            "(4) REFLECT — daily check of calendar + next actions. WEEKLY REVIEW is critical: "
            "process all inboxes to zero, review all projects, update everything. "
            "(5) ENGAGE — choose what to do by: context, time available, energy available, priority. "
            "Key ADHD insight: this system IS the external brain that working memory can't be."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "framework",
    },
    {
        "source_name": "GTD — Natural Planning Model",
        "source_type": "framework",
        "topic": "gtd,planning,projects,breakdown",
        "content": (
            "For any project, the brain naturally plans in five steps: "
            "(1) Define purpose and principles — WHY are we doing this? "
            "(2) Outcome visioning — WHAT does done look like? "
            "(3) Brainstorming — HOW, generate ideas without judgment. "
            "(4) Organizing — identify components, sequences, priorities. "
            "(5) Identify next actions — what's the very next physical thing to do? "
            "Most people skip to step 5 and wonder why they're stuck. "
            "For ADHD: steps 1-2 provide the 'why' that generates initial motivation. "
            "Step 5 provides the concrete anchor that overcomes task initiation difficulty."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "strategy",
    },
    {
        "source_name": "GTD — Core Principles",
        "source_type": "framework",
        "topic": "gtd,principles,working_memory,next_action",
        "content": (
            "Key GTD principles for ADHD executive function support: "
            "'Your mind is for having ideas, not holding them.' "
            "'Things rarely get stuck because of lack of time. They get stuck because "
            "the doing of them has not been defined.' "
            "The Two-Minute Rule: if an action takes less than two minutes, do it now. "
            "The brain can only hold about four things at once. "
            "You should always know your next physical action for every project. "
            "Heylighen & Vidal (2008) provided cognitive science validation: "
            "GTD implements distributed cognition principles — organizing tasks into "
            "actionable external memories reduces cognitive load and anxiety."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "principle",
    },

    # ── Atomic Habits (James Clear) ──────────────────────────────────
    {
        "source_name": "Atomic Habits — Core Philosophy",
        "source_type": "framework",
        "topic": "habits,systems,identity,atomic_habits",
        "content": (
            "Three key lessons from Atomic Habits: "
            "(1) Small habits make a big difference — 1% daily improvement = 37x over a year. "
            "Results are often delayed (Plateau of Latent Potential) but work is never wasted. "
            "(2) Focus on SYSTEMS, not goals — 'The purpose of setting goals is to win the game. "
            "The purpose of building systems is to continue playing the game.' "
            "(3) Build identity-based habits — change what you believe about yourself. "
            "'Every action you take is a vote for the type of person you wish to become.' "
            "ADHD insight: Identity-based habits bypass the motivation/willpower problem. "
            "Instead of relying on self-regulation, build systems that make the habit automatic."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "framework",
    },
    {
        "source_name": "Atomic Habits — Four Laws of Behavior Change",
        "source_type": "framework",
        "topic": "habits,cue,craving,response,reward,behavior_change",
        "content": (
            "Every habit follows: Cue → Craving → Response → Reward. "
            "To CREATE a good habit: "
            "1st Law (Cue): Make it obvious — habit scorecard, implementation intentions "
            "('I will [BEHAVIOR] at [TIME] in [LOCATION]'), habit stacking, environment design. "
            "2nd Law (Craving): Make it attractive — temptation bundling, join supportive cultures. "
            "3rd Law (Response): Make it easy — reduce friction, prime the environment, "
            "Two-Minute Rule (downscale until ≤2 min), automate with technology. "
            "4th Law (Reward): Make it satisfying — immediate reinforcement, habit tracking. "
            "To BREAK a bad habit: invert each law — make it invisible, unattractive, difficult, unsatisfying. "
            "ADHD application: environment design (Laws 1+3) is the highest-leverage intervention."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "strategy",
    },
    {
        "source_name": "Atomic Habits — Key Tactics",
        "source_type": "strategy",
        "topic": "habits,two_minute_rule,habit_stacking,environment,never_miss_twice",
        "content": (
            "Habit Stacking: 'After I [CURRENT HABIT], I will [NEW HABIT].' "
            "Piggybacks new habits on existing routines — doesn't require working memory to trigger. "
            "The Two-Minute Rule: when starting a new habit, downscale until it takes ≤2 minutes. "
            "'Read before bed' becomes 'read one page.' Master the art of showing up. "
            "Environment Design: make cues for good habits obvious and visible. "
            "Make cues for bad habits invisible. Better environment > more motivation. "
            "Never Miss Twice: missing once is an accident, missing twice starts a new habit. "
            "Get back on track immediately — no shame, just reset. "
            "Motion vs. Action: planning and strategizing is motion. Behavior that produces results "
            "is action. Don't confuse preparation with progress."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "tactic",
    },

    # ── Barkley: ADHD, Executive Function & Self-Regulation ──────────
    {
        "source_name": "Barkley — ADHD as Executive Function Deficit",
        "source_type": "research",
        "topic": "adhd,executive_function,self_regulation,barkley,efdd",
        "content": (
            "ADHD = SRDD (Self-Regulation Deficit Disorder) = EFDD (Executive Function Deficit Disorder). "
            "The six executive functions as self-directed actions: "
            "(1) Inhibition (self-restraint) — stopping impulsive actions. "
            "(2) Self-awareness (self-directed attention) — monitoring your own behavior. "
            "(3) Verbal working memory (self-speech) — internal voice, holding rules in mind. "
            "(4) Nonverbal working memory (self-directed imagery) — visualizing outcomes. "
            "(5) Emotional self-control (self-directed emotion) — managing reactions. "
            "(6) Self-motivation — generating drive without external rewards. "
            "(7) Problem-solving (self-directed play) — flexible thinking and planning. "
            "Source: Russell A. Barkley, PhD, Clinical Professor of Psychiatry, MUSC."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "framework",
    },
    {
        "source_name": "Barkley — Performance Disorder, Not Knowledge Disorder",
        "source_type": "research",
        "topic": "adhd,performance,knowledge,environment,point_of_performance",
        "content": (
            "THE single most important insight for Coach's design: "
            "'Disorders of EF create disorders mainly of PERFORMANCE rather than of KNOWLEDGE or skills. "
            "They are problems with doing what one knows and not of knowing what to do.' "
            "This means: conveying more knowledge (telling someone what to do) does NOT help as much "
            "as changing the environment where performance happens. "
            "The solution is engineering the environment at the POINT OF PERFORMANCE — "
            "the place and time where the person needs to act. "
            "'Once per week counseling without efforts to insert accommodations at key points "
            "of performance in natural settings is unlikely to succeed.' "
            "Coach must help Tim WHERE and WHEN he's struggling, not in abstract planning sessions."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "principle",
    },
    {
        "source_name": "Barkley — Time Blindness",
        "source_type": "research",
        "topic": "adhd,time_blindness,temporal_myopia,deadlines",
        "content": (
            "ADHD creates time blindness: 'Those with ADHD cannot organize their behavior both "
            "within and across time, leaving them with serious problems with time, timing, and "
            "timeliness of behavior, such that they are to time what nearsightedness is to spatial vision.' "
            "Creates temporal myopia — behavior governed by the immediate 'now' rather than future consequences. "
            "Short-sighted decisions are NOT moral failures — they reflect neurological difficulty "
            "representing the future. "
            "Coach implication: make the future concrete and proximate. Break projects into today-sized pieces. "
            "Use visible countdown timers. Reduce temporal gaps between action and consequence."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "principle",
    },
    {
        "source_name": "Barkley — Five Principles for Managing EF Deficits",
        "source_type": "research",
        "topic": "adhd,externalize,motivation,fuel_tank,environment,barkley_principles",
        "content": (
            "Barkley's five principles: "
            "(1) Externalize information — if working memory isn't controlling behavior, "
            "make it physical: sticky notes, posted rules, checklists at the point of performance. "
            "(2) Externalize time — make time visible: countdown timers, break long projects into "
            "daily 'baby steps' with immediate feedback, make deadlines proximate. "
            "(3) Externalize motivation — provide artificial external rewards throughout tasks. "
            "These are 'motivational prostheses' — as essential as mechanical limbs for amputees. "
            "'Complaining about lack of motivation will not suffice to correct the problem.' "
            "(4) Manage the EF fuel tank — self-regulation depletes a limited resource. "
            "Replenish with: exercise, 10-min breaks, relaxation, visualizing rewards, positive emotions, glucose. "
            "Depletes faster with: stress, alcohol, illness, protracted demands. "
            "(5) Engineer the environment — minimize distractors, replace with task-salient cues, "
            "post rules visibly, verbalize rules aloud before and during work."
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "strategy",
    },

    # ── Synthesis: How the Three Sources Work Together ────────────────
    {
        "source_name": "Coach Synthesis — Operating Principles",
        "source_type": "framework",
        "topic": "coach,principles,synthesis,gtd,atomic_habits,barkley",
        "content": (
            "Coach's 10 operating principles derived from all three sources: "
            "(1) Externalize everything — if it's in Tim's head, it needs to be in the system. (GTD + Barkley) "
            "(2) Define the next physical action — ambiguity kills momentum. (GTD) "
            "(3) Make it obvious, easy, and satisfying — design the environment. (Atomic Habits) "
            "(4) Work at the point of performance — help Tim where and when he's struggling. (Barkley) "
            "(5) Protect the fuel tank — monitor energy, suggest breaks, prevent depletion spirals. (Barkley) "
            "(6) Never shame — missed tasks are system failures, not character failures. (Barkley + Kiro global rule) "
            "(7) Start with two minutes — when Tim can't start, make the first step absurdly small. (Atomic Habits) "
            "(8) Use novelty and deadlines — these are Tim's natural motivation levers. (Tim's cognitive profile) "
            "(9) Bridge time — make the future concrete and proximate. Today-sized pieces. (Barkley) "
            "(10) Review weekly — the system only works if it's maintained. Non-negotiable. (GTD)"
        ),
        "tier": 1,
        "adhd_relevant": True,
        "actionability": "principle",
    },
]


def seed_knowledge(db: CoachDB) -> int:
    """
    Insert Tier 1 seed knowledge. Checks by source_name to avoid duplicates.
    Returns the number of new entries inserted.
    """
    inserted = 0
    conn = db._conn()
    try:
        with conn.cursor() as cur:
            for seed in TIER_1_SEEDS:
                # Check if already seeded
                cur.execute(
                    "SELECT 1 FROM coach_knowledge WHERE source_name = %s",
                    (seed["source_name"],),
                )
                if cur.fetchone():
                    continue

                cur.execute("""
                    INSERT INTO coach_knowledge
                        (source_name, source_type, topic, content, tier,
                         adhd_relevant, actionability)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    seed["source_name"], seed["source_type"],
                    seed["topic"], seed["content"], seed["tier"],
                    seed["adhd_relevant"], seed["actionability"],
                ))
                inserted += 1
        conn.commit()
        logger.info("Seeded %d new Coach knowledge entries (Tier 1).", inserted)
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        db._put(conn)
