"""
finley/knowledge.py — Financial Knowledge Base (RAG-ready).

CRUD operations for the finley_knowledge table, topic-based retrieval,
and Tier 1 seed data (foundational frameworks).

Knowledge Tiers:
    Tier 1 — Foundational frameworks (CFPB, YNAB rules, Canada tax, ADHD strategies)
    Tier 2 — Province-specific (BC rent, tenant rights, MSP) [future]
    Tier 3 — Situation-specific (cannabis budgeting, gig worker tips) [future]
    Tier 4 — Personalised (Tim's own patterns and learned strategies) [future]

Spec reference: FINLEY FINANCIAL PROFILING §2 — Knowledge Seeding.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .db import FinleyDB

logger = logging.getLogger("kiro.finley.knowledge")


# ═══════════════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════════════

def add_knowledge(
    db: FinleyDB,
    source_name: str,
    source_type: str,
    topic: str,
    content: str,
    tier: int = 1,
    income_relevance: str = "all",
    adhd_relevant: bool = False,
    canada_specific: bool = False,
    actionability: str = "reference",
) -> int:
    """Insert a knowledge entry. Returns the new row ID."""
    conn = db._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO finley_knowledge
                    (source_name, source_type, topic, content, tier,
                     income_relevance, adhd_relevant, canada_specific, actionability)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (source_name, source_type, topic, content, tier,
                  income_relevance, adhd_relevant, canada_specific, actionability))
            row = cur.fetchone()
        conn.commit()
        return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        db._put(conn)


def get_knowledge_by_topic(db: FinleyDB, topic: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Retrieve knowledge entries matching a topic (substring match)."""
    import psycopg2.extras
    conn = db._conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM finley_knowledge
                WHERE LOWER(topic) LIKE LOWER(%s)
                ORDER BY tier ASC, created_at DESC
                LIMIT %s
            """, (f"%{topic}%", limit))
            return [dict(r) for r in cur.fetchall()]
    finally:
        db._put(conn)


def get_knowledge_for_stage(db: FinleyDB, stage: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve knowledge entries most relevant to a financial stage."""
    import psycopg2.extras

    # Map stages to relevant topics
    stage_topics = {
        "distressed": ["emergency", "crisis", "debt", "overdraft", "basics", "adhd"],
        "fragile": ["budgeting", "recurring", "savings_start", "adhd", "habits"],
        "stabilizing": ["savings", "debt_payoff", "investing_basics", "goals"],
        "grounding": ["investing", "tax_optimization", "long_term", "growth"],
    }

    topics = stage_topics.get(stage, stage_topics["fragile"])
    topic_pattern = "|".join(topics)

    conn = db._conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM finley_knowledge
                WHERE topic ~* %s
                ORDER BY tier ASC, actionability DESC, created_at DESC
                LIMIT %s
            """, (topic_pattern, limit))
            return [dict(r) for r in cur.fetchall()]
    finally:
        db._put(conn)


def get_adhd_knowledge(db: FinleyDB, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve ADHD-specific financial knowledge."""
    import psycopg2.extras
    conn = db._conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM finley_knowledge
                WHERE adhd_relevant = TRUE
                ORDER BY tier ASC, actionability DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        db._put(conn)


def count_knowledge(db: FinleyDB) -> Dict[str, int]:
    """Count knowledge entries by tier."""
    conn = db._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT tier, COUNT(*) FROM finley_knowledge
                GROUP BY tier ORDER BY tier
            """)
            return {f"tier_{r[0]}": r[1] for r in cur.fetchall()}
    finally:
        db._put(conn)


# ═══════════════════════════════════════════════════════════════════════════
# Tier 1 Seed Data
# ═══════════════════════════════════════════════════════════════════════════

TIER_1_SEEDS: List[Dict[str, Any]] = [
    # --- CFPB Framework ---
    {
        "source_name": "CFPB Financial Well-Being Scale",
        "source_type": "framework",
        "topic": "wellbeing,basics,cfpb",
        "content": (
            "Financial well-being is defined by four elements: "
            "(1) having control over day-to-day finances, "
            "(2) having the capacity to absorb a financial shock, "
            "(3) being on track to meet financial goals, and "
            "(4) having the freedom to make choices that allow you to enjoy life. "
            "The CFPB scale measures these on a 0-100 score. "
            "Average score for 18-29 year olds is around 51. "
            "Score under 40 indicates significant financial stress."
        ),
        "tier": 1,
        "income_relevance": "all",
        "adhd_relevant": False,
        "canada_specific": False,
        "actionability": "framework",
    },
    # --- YNAB Four Rules ---
    {
        "source_name": "YNAB Methodology",
        "source_type": "framework",
        "topic": "budgeting,ynab,basics",
        "content": (
            "YNAB's Four Rules: "
            "(1) Give Every Dollar a Job — assign all income to categories before spending. "
            "(2) Embrace Your True Expenses — break large periodic costs into monthly amounts. "
            "(3) Roll With the Punches — move money between categories when plans change (this is NOT failure). "
            "(4) Age Your Money — gradually get to where you're spending money that's at least 30 days old. "
            "Rule 3 is especially important for ADHD: flexibility is built into the system, not a bug."
        ),
        "tier": 1,
        "income_relevance": "all",
        "adhd_relevant": True,
        "canada_specific": False,
        "actionability": "strategy",
    },
    # --- Canada Tax Basics ---
    {
        "source_name": "CRA Tax Fundamentals",
        "source_type": "government",
        "topic": "tax,canada,basics",
        "content": (
            "Key Canadian tax concepts for low-to-moderate income: "
            "• Basic Personal Amount (BPA) ~$15,705 (2024) — first dollars are tax-free. "
            "• TFSA contribution room accumulates from age 18 (~$7,000/year in 2024). "
            "  Any gains inside a TFSA are completely tax-free. Priority #1 for savings. "
            "• GST/HST Credit — quarterly payment for low-income individuals, automatic with tax filing. "
            "• Canada Workers Benefit (CWB) — refundable tax credit for low-income workers. "
            "• RRSP reduces taxable income but locked until retirement (or first home via HBP). "
            "• Filing your taxes is CRITICAL even with low income — unlocks benefits."
        ),
        "tier": 1,
        "income_relevance": "low",
        "adhd_relevant": False,
        "canada_specific": True,
        "actionability": "strategy",
    },
    {
        "source_name": "BC Provincial Benefits",
        "source_type": "government",
        "topic": "tax,canada,bc,benefits",
        "content": (
            "BC-specific financial supports: "
            "• BC Climate Action Tax Credit — quarterly payment, income-tested. "
            "• BC Sales Tax Credit — for individuals with income under ~$20,000. "
            "• Fair PharmaCare — income-based prescription drug coverage (register annually). "
            "• BC Housing programs — rent supplements and housing registry. "
            "• BC Hydro Customer Crisis Fund — one-time bill help for qualifying households. "
            "• Legal cannabis purchases are not tax-deductible (in case you were wondering)."
        ),
        "tier": 1,
        "income_relevance": "low",
        "adhd_relevant": False,
        "canada_specific": True,
        "actionability": "strategy",
    },
    # --- ADHD-Informed Financial Strategies ---
    {
        "source_name": "ADHD Money Management",
        "source_type": "strategy",
        "topic": "adhd,budgeting,habits,basics",
        "content": (
            "ADHD-specific financial strategies: "
            "• Automate everything possible — bills, savings, minimum payments. "
            "  Automation removes the 'remembering' tax. "
            "• Use separate accounts for different purposes (spending, bills, savings). "
            "  Visual separation = mental separation. "
            "• The 24-hour rule: before any purchase over $50, wait 24 hours. "
            "  ADHD impulse = intensity without duration. Most urges pass. "
            "• Round numbers work better than exact budgets. $200/week for food, not $187.43. "
            "• Body doubling works for finances too — do budget reviews with someone (or Finley). "
            "• Expect to overshoot sometimes. Build a 'whoops' category. It's not failure, it's data."
        ),
        "tier": 1,
        "income_relevance": "all",
        "adhd_relevant": True,
        "canada_specific": False,
        "actionability": "strategy",
    },
    {
        "source_name": "ADHD Tax Prevention",
        "source_type": "strategy",
        "topic": "adhd,adhd_tax,debt,overdraft",
        "content": (
            "The 'ADHD Tax' — money lost to executive function gaps: "
            "• Late fees from forgotten bills → Set up autopay for EVERYTHING. "
            "• Overdraft charges → Keep a $100 buffer in checking, treat it as $0. "
            "• Subscription creep → Calendar reminder every 3 months to audit subscriptions. "
            "• Impulse purchases → Unlink cards from one-click shopping. Add friction. "
            "• Replacement purchases (lost/broken items bought twice) → Designate homes for items. "
            "• Payday loan interest → Build even a tiny emergency fund ($200 can prevent a payday loan). "
            "The goal isn't perfection — it's reducing the tax to something manageable."
        ),
        "tier": 1,
        "income_relevance": "all",
        "adhd_relevant": True,
        "canada_specific": False,
        "actionability": "strategy",
    },
    # --- Emergency Fund Basics ---
    {
        "source_name": "Emergency Fund Strategy",
        "source_type": "strategy",
        "topic": "emergency,savings,savings_start,basics",
        "content": (
            "Emergency fund progression for someone starting from scratch: "
            "• Level 0: $0 buffer → dangerous zone (one flat tire = crisis). "
            "• Level 1: $200 — prevents a payday loan for small emergencies. "
            "• Level 2: $500 — covers most car repairs, medical copays. "
            "• Level 3: $1,000 — standard starter emergency fund. "
            "• Level 4: 1 month expenses — real breathing room. "
            "• Level 5: 3 months — conventional advice target. "
            "Don't aim for Level 5 from Level 0. Each step up is a genuine win. "
            "Keep emergency fund in a separate HIGH-INTEREST savings account (like EQ Bank ~4%). "
            "In Canada, put it in a TFSA if you have room — interest is tax-free."
        ),
        "tier": 1,
        "income_relevance": "low",
        "adhd_relevant": False,
        "canada_specific": True,
        "actionability": "strategy",
    },
    # --- Debt Strategy ---
    {
        "source_name": "Debt Management Basics",
        "source_type": "strategy",
        "topic": "debt,debt_payoff,basics",
        "content": (
            "Debt priority for someone with ADHD and limited income: "
            "1. STOP THE BLEEDING: No new payday loans. Ever. 400%+ APR is financial quicksand. "
            "2. Minimum payments on everything — protect credit score from further damage. "
            "3. Debt Avalanche (mathematically optimal) vs Debt Snowball (psychologically motivating). "
            "   For ADHD: Snowball often wins because quick wins maintain dopamine/motivation. "
            "   Pay minimums on everything, throw extra at the SMALLEST balance first. "
            "4. Credit card balance: if you can't pay in full, at least pay more than minimum. "
            "   Minimum payments are designed to keep you in debt for decades. "
            "5. Consider credit counselling (free in Canada through non-profits like Credit Counselling Society). "
            "6. Consumer proposal is better than bankruptcy if debts are overwhelming (talk to a Licensed Insolvency Trustee)."
        ),
        "tier": 1,
        "income_relevance": "low",
        "adhd_relevant": True,
        "canada_specific": True,
        "actionability": "strategy",
    },
    # --- Recurring Bill Management ---
    {
        "source_name": "Bill Management Strategy",
        "source_type": "strategy",
        "topic": "recurring,bills,budgeting,adhd",
        "content": (
            "Recurring bill management for someone with ADHD: "
            "• List ALL recurring charges (Finley can detect these automatically). "
            "• Align bill dates: call providers and move due dates to just after payday. "
            "  Most Canadian companies will change your billing date on request. "
            "• Two-account system: main chequing for bills, separate for spending. "
            "  On payday, auto-transfer bill money into bills account. Spend what's left guilt-free. "
            "• Freedom Mobile, ICBC, utilities — these are non-negotiable. Pay first. "
            "• YMCA, streaming, subscriptions — audit quarterly. "
            "• Cannabis budget: no judgement, but set a weekly amount and use cash if possible. "
            "  Cash makes spending physical and visible."
        ),
        "tier": 1,
        "income_relevance": "all",
        "adhd_relevant": True,
        "canada_specific": True,
        "actionability": "strategy",
    },
]


def seed_tier_1(db: FinleyDB, force: bool = False) -> int:
    """
    Seed Tier 1 foundational knowledge into the database.

    Skips if already seeded (checks by source_name) unless force=True.
    Returns count of entries inserted.
    """
    import psycopg2.extras
    count = 0
    conn = db._conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for entry in TIER_1_SEEDS:
                if not force:
                    cur.execute(
                        "SELECT id FROM finley_knowledge WHERE source_name = %s",
                        (entry["source_name"],),
                    )
                    if cur.fetchone():
                        continue

                cur.execute("""
                    INSERT INTO finley_knowledge
                        (source_name, source_type, topic, content, tier,
                         income_relevance, adhd_relevant, canada_specific, actionability)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    entry["source_name"], entry["source_type"],
                    entry["topic"], entry["content"], entry["tier"],
                    entry["income_relevance"], entry["adhd_relevant"],
                    entry["canada_specific"], entry["actionability"],
                ))
                count += 1

        conn.commit()
        logger.info("Seeded %d Tier 1 knowledge entries.", count)
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        db._put(conn)
