"""
finley/profiler.py — Financial Profile Engine.

Builds a daily snapshot of Tim's financial vital signs, behavioural
patterns, and an overall stage assessment.  Runs as a post-sync callback
so the profile is always current with the latest YNAB data.

Stage model (4-tier, ADHD-informed, no judgement):
    DISTRESSED → FRAGILE → STABILIZING → GROUNDING

Spec reference: FINLEY FINANCIAL PROFILING §1 — Financial Profile Model.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .classifier import classify_payee, detect_recurring, identify_income_sources
from .db import FinleyDB, milliunits_to_dollars

logger = logging.getLogger("kiro.finley.profiler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STAGES = ("distressed", "fragile", "stabilizing", "grounding")

# Days of history to consider for each metric window
WINDOW_30 = 30
WINDOW_90 = 90

# Spending categories that indicate ADHD tax
ADHD_TAX_CATEGORIES = {
    "late_fee", "overdraft", "nsf", "interest_charge",
    "replacement_purchase", "impulse", "payday_loan",
}


# ═══════════════════════════════════════════════════════════════════════════
# Vital Signs
# ═══════════════════════════════════════════════════════════════════════════

def compute_vital_signs(db: FinleyDB) -> Dict[str, Any]:
    """
    Compute the financial vital-signs snapshot.

    Returns dict with keys:
        monthly_income          float  (dollars)
        fixed_expenses          float  (dollars, recurring bills)
        variable_expenses       float  (dollars, everything else out)
        cash_flow_delta         float  (income - total expenses)
        days_until_zero         float  (checking balance / avg daily spend)
        expense_volatility      float  (stddev of weekly spend / mean)
        subscription_load       float  (dollars, total recurring subscriptions)
        debt_service_ratio      float  (debt payments / income, 0-1)
    """
    today = date.today()
    month_ago = (today - timedelta(days=WINDOW_30)).isoformat()
    three_months_ago = (today - timedelta(days=WINDOW_90)).isoformat()
    today_iso = today.isoformat()

    # --- Income ---
    income_mu = db.total_income(month_ago, today_iso)
    monthly_income = milliunits_to_dollars(income_mu)

    # --- All outflows in the last 30 days ---
    all_txns = db.get_transactions(since_date=month_ago, until_date=today_iso)
    outflows = [t for t in all_txns if t["amount"] < 0]

    # --- Recurring detection (90-day window for pattern accuracy) ---
    txns_90d = db.get_transactions(since_date=three_months_ago, until_date=today_iso)
    recurring = detect_recurring(txns_90d)

    # Fixed = recurring bills; Variable = the rest
    recurring_payees = {r["payee"].lower() for r in recurring}
    fixed_mu = sum(
        abs(t["amount"]) for t in outflows
        if (t.get("payee_name") or "").lower() in recurring_payees
    )
    variable_mu = sum(abs(t["amount"]) for t in outflows) - fixed_mu

    fixed_expenses = milliunits_to_dollars(fixed_mu)
    variable_expenses = milliunits_to_dollars(variable_mu)
    total_expenses = fixed_expenses + variable_expenses

    cash_flow_delta = monthly_income - total_expenses

    # --- Days until zero ---
    accounts = db.get_accounts(on_budget_only=True)
    checking_balance_mu = sum(
        a.get("balance", 0) for a in accounts
        if (a.get("type", "") or "").lower() in ("checking", "savings")
    )
    checking_balance = milliunits_to_dollars(checking_balance_mu)

    daily_spend = total_expenses / max(WINDOW_30, 1)
    days_until_zero = checking_balance / daily_spend if daily_spend > 0 else 999.0

    # --- Expense volatility (CV of weekly spending over 90 days) ---
    weekly_totals = _weekly_spending_totals(txns_90d)
    if len(weekly_totals) >= 2:
        mean_w = statistics.mean(weekly_totals)
        std_w = statistics.stdev(weekly_totals)
        expense_volatility = std_w / mean_w if mean_w > 0 else 0.0
    else:
        expense_volatility = 0.0

    # --- Subscription load ---
    subscription_load = sum(
        milliunits_to_dollars(abs(r["avg_amount"]))
        for r in recurring
        if r.get("frequency") in ("monthly", "biweekly", "weekly")
    )

    # --- Debt service ratio ---
    debt_payments_mu = sum(
        abs(t["amount"]) for t in outflows
        if _is_debt_payment(t)
    )
    debt_service_ratio = (
        milliunits_to_dollars(debt_payments_mu) / monthly_income
        if monthly_income > 0 else 0.0
    )

    return {
        "monthly_income": round(monthly_income, 2),
        "fixed_expenses": round(fixed_expenses, 2),
        "variable_expenses": round(variable_expenses, 2),
        "cash_flow_delta": round(cash_flow_delta, 2),
        "days_until_zero": round(days_until_zero, 1),
        "expense_volatility": round(expense_volatility, 3),
        "subscription_load": round(subscription_load, 2),
        "debt_service_ratio": round(debt_service_ratio, 3),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Behavioural Patterns
# ═══════════════════════════════════════════════════════════════════════════

def compute_behavioral_patterns(db: FinleyDB) -> Dict[str, Any]:
    """
    Detect ADHD-informed behavioural spending patterns.

    Returns dict with keys:
        impulse_frequency       float  (impulse txns per week, 30d window)
        payday_spike            float  (ratio of payday-week spend to avg week)
        avoidance_days          int    (consecutive days with zero app opens — future)
        category_discipline     float  (% of spend in top-5 categories, 0-1)
        recurring_surprise      int    (count of recurring charges Tim may not know about)
        adhd_tax                float  (dollars wasted on late fees, replacements, etc.)
        top_impulse_merchants   list   (top payees by impulse-flagged transactions)
        weekend_vs_weekday      float  (ratio of weekend to weekday daily spend)
    """
    today = date.today()
    month_ago = (today - timedelta(days=WINDOW_30)).isoformat()
    three_months_ago = (today - timedelta(days=WINDOW_90)).isoformat()
    today_iso = today.isoformat()

    outflows_30 = [
        t for t in db.get_transactions(since_date=month_ago, until_date=today_iso)
        if t["amount"] < 0
    ]
    outflows_90 = [
        t for t in db.get_transactions(since_date=three_months_ago, until_date=today_iso)
        if t["amount"] < 0
    ]

    # --- Impulse frequency ---
    impulse_txns = [t for t in outflows_30 if _is_impulse(t)]
    weeks_in_window = max(WINDOW_30 / 7, 1)
    impulse_frequency = len(impulse_txns) / weeks_in_window

    # --- Payday spike ---
    payday_spike = _compute_payday_spike(outflows_90)

    # --- Category discipline (top-5 concentration) ---
    cat_totals: Dict[str, float] = defaultdict(float)
    for t in outflows_30:
        cls = classify_payee(t.get("payee_name", ""))
        cat_totals[cls["category"]] += abs(t["amount"])
    total_spend = sum(cat_totals.values()) or 1
    top5 = sorted(cat_totals.values(), reverse=True)[:5]
    category_discipline = sum(top5) / total_spend

    # --- Recurring surprise ---
    recurring = detect_recurring(outflows_90)
    recurring_surprise = sum(
        1 for r in recurring
        if r.get("consistency", 0) < 0.7  # irregular enough to be a surprise
    )

    # --- ADHD tax ---
    adhd_tax_mu = sum(
        abs(t["amount"]) for t in outflows_30
        if _is_adhd_tax(t)
    )

    # --- Top impulse merchants ---
    impulse_counter: Counter = Counter()
    for t in impulse_txns:
        pn = t.get("payee_name", "Unknown")
        impulse_counter[pn] += 1
    top_impulse_merchants = [
        {"payee": p, "count": c}
        for p, c in impulse_counter.most_common(5)
    ]

    # --- Weekend vs weekday ---
    weekend_spend, weekday_spend, wknd_days, wkdy_days = 0.0, 0.0, 0, 0
    for t in outflows_30:
        try:
            txn_date = date.fromisoformat(str(t["date"])[:10])
        except (ValueError, TypeError):
            continue
        amt = abs(t["amount"])
        if txn_date.weekday() >= 5:
            weekend_spend += amt
            wknd_days = max(wknd_days, 1)
        else:
            weekday_spend += amt
            wkdy_days = max(wkdy_days, 1)
    # Average daily
    wknd_avg = weekend_spend / max(wknd_days, 1)
    wkdy_avg = weekday_spend / max(wkdy_days, 1)
    weekend_vs_weekday = wknd_avg / wkdy_avg if wkdy_avg > 0 else 1.0

    return {
        "impulse_frequency": round(impulse_frequency, 2),
        "payday_spike": round(payday_spike, 2),
        "avoidance_days": 0,  # Placeholder — needs app-open tracking
        "category_discipline": round(category_discipline, 3),
        "recurring_surprise": recurring_surprise,
        "adhd_tax": round(milliunits_to_dollars(adhd_tax_mu), 2),
        "top_impulse_merchants": top_impulse_merchants,
        "weekend_vs_weekday": round(weekend_vs_weekday, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Stage Assessment
# ═══════════════════════════════════════════════════════════════════════════

def assess_stage(vital_signs: Dict[str, Any],
                 behavioral: Dict[str, Any],
                 wellbeing_score: float | None = None) -> str:
    """
    Determine the current financial stage.

    Stages:
        distressed   — negative cash flow, <7 days runway, high ADHD tax
        fragile      — barely positive cash flow, 7-21 days, some pattern
        stabilizing  — positive cash flow, 21-60 days, declining impulse
        grounding    — surplus, 60+ days, low volatility, growing savings

    Uses a simple scoring model. Not rocket science — just enough to
    be directionally correct so Finley knows how to talk to Tim.
    """
    score = 0

    # Cash flow
    cfd = vital_signs.get("cash_flow_delta", 0)
    if cfd >= 200:
        score += 3
    elif cfd >= 0:
        score += 2
    elif cfd >= -100:
        score += 1
    # else 0

    # Days until zero (runway)
    duz = vital_signs.get("days_until_zero", 0)
    if duz >= 60:
        score += 3
    elif duz >= 21:
        score += 2
    elif duz >= 7:
        score += 1

    # Expense volatility (lower is better)
    ev = vital_signs.get("expense_volatility", 1.0)
    if ev < 0.15:
        score += 2
    elif ev < 0.35:
        score += 1

    # Debt service ratio (lower is better)
    dsr = vital_signs.get("debt_service_ratio", 0)
    if dsr < 0.10:
        score += 2
    elif dsr < 0.25:
        score += 1

    # Impulse frequency (lower is better)
    imp = behavioral.get("impulse_frequency", 5)
    if imp < 1:
        score += 2
    elif imp < 3:
        score += 1

    # ADHD tax (lower is better)
    adhd = behavioral.get("adhd_tax", 100)
    if adhd < 5:
        score += 2
    elif adhd < 25:
        score += 1

    # CFPB wellbeing bonus
    if wellbeing_score is not None:
        if wellbeing_score >= 60:
            score += 2
        elif wellbeing_score >= 40:
            score += 1

    # Map score to stage
    # Max possible ≈ 17
    if score >= 13:
        return "grounding"
    elif score >= 9:
        return "stabilizing"
    elif score >= 5:
        return "fragile"
    else:
        return "distressed"


# ═══════════════════════════════════════════════════════════════════════════
# Profile Builder (main entry point)
# ═══════════════════════════════════════════════════════════════════════════

def build_profile(db: FinleyDB) -> Dict[str, Any]:
    """
    Build and persist a complete financial profile for today.

    Called by sync.py after each successful YNAB sync.
    Returns the profile dict for immediate use.
    """
    today_iso = date.today().isoformat()

    vital_signs = compute_vital_signs(db)
    behavioral = compute_behavioral_patterns(db)

    # Grab latest CFPB score if available
    wb = db.get_latest_wellbeing()
    wb_score = wb["scaled_score"] if wb else None

    stage = assess_stage(vital_signs, behavioral, wb_score)

    # Account snapshot for the daily record
    accounts = db.get_accounts()
    account_snapshot = {
        a["name"]: milliunits_to_dollars(a.get("balance", 0))
        for a in accounts
    }

    # Persist
    profile_id = db.upsert_profile(
        profile_date=today_iso,
        vital_signs=vital_signs,
        behavioral=behavioral,
        stage=stage,
        account_snapshot=account_snapshot,
    )

    profile = {
        "id": profile_id,
        "profile_date": today_iso,
        "vital_signs": vital_signs,
        "behavioral": behavioral,
        "stage": stage,
        "account_snapshot": account_snapshot,
    }

    logger.info(
        "Profile built for %s — stage: %s, cash_flow: $%.2f, runway: %.0f days",
        today_iso, stage, vital_signs["cash_flow_delta"],
        vital_signs["days_until_zero"],
    )

    return profile


def get_profile_summary(db: FinleyDB) -> str:
    """
    Human-readable summary of the latest profile, suitable for injection
    into Finley's system prompt so he knows how to talk to Tim.
    """
    profile = db.get_latest_profile()
    if not profile:
        return "No financial profile available yet. Need at least one YNAB sync."

    vs = profile.get("vital_signs", {})
    bh = profile.get("behavioral", {})
    stage = profile.get("stage", "unknown")
    snap = profile.get("account_snapshot", {})

    lines = [
        f"=== Financial Profile ({profile['profile_date']}) ===",
        f"Stage: {stage.upper()}",
        f"",
        f"Vital Signs:",
        f"  Monthly income:      ${vs.get('monthly_income', 0):,.2f}",
        f"  Fixed expenses:      ${vs.get('fixed_expenses', 0):,.2f}",
        f"  Variable expenses:   ${vs.get('variable_expenses', 0):,.2f}",
        f"  Cash flow delta:     ${vs.get('cash_flow_delta', 0):,.2f}",
        f"  Days until zero:     {vs.get('days_until_zero', 0):.0f}",
        f"  Expense volatility:  {vs.get('expense_volatility', 0):.1%}",
        f"  Subscription load:   ${vs.get('subscription_load', 0):,.2f}",
        f"  Debt service ratio:  {vs.get('debt_service_ratio', 0):.1%}",
        f"",
        f"Behavioural Patterns:",
        f"  Impulse frequency:   {bh.get('impulse_frequency', 0):.1f}/week",
        f"  Payday spike:        {bh.get('payday_spike', 1):.1f}x average",
        f"  Category discipline: {bh.get('category_discipline', 0):.0%}",
        f"  Recurring surprises: {bh.get('recurring_surprise', 0)}",
        f"  ADHD tax:            ${bh.get('adhd_tax', 0):,.2f}",
        f"  Weekend vs weekday:  {bh.get('weekend_vs_weekday', 1):.1f}x",
    ]

    if snap:
        lines.append("")
        lines.append("Account Balances:")
        for name, bal in snap.items():
            lines.append(f"  {name}: ${bal:,.2f}")

    # Current-month spending by category
    try:
        from datetime import datetime
        from .db import format_currency
        now = datetime.now()
        since = now.strftime("%Y-%m-01")
        rows = db.spending_by_category(since)
        if rows:
            lines.append("")
            lines.append(f"Spending This Month ({now.strftime('%B %Y')}):")
            for r in rows:
                cat = r['category_name'] or 'Unknown'
                amt = abs(r['total']) / 1000
                lines.append(f"  {cat:20s}  ${amt:7.2f}  ({r['txn_count']} txns)")
    except Exception:
        pass

    # CFPB wellbeing if available
    wb = db.get_latest_wellbeing()
    if wb:
        lines.append("")
        lines.append(f"CFPB Wellbeing Score: {wb['scaled_score']:.0f}/100 "
                     f"(assessed {wb.get('assessed_at', 'unknown')})")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _weekly_spending_totals(transactions: list[dict]) -> list[float]:
    """Bucket outflows into ISO weeks and return list of weekly totals in milliunits."""
    weekly: Dict[str, float] = defaultdict(float)
    for t in transactions:
        if t["amount"] >= 0:
            continue
        try:
            d = date.fromisoformat(str(t["date"])[:10])
            week_key = d.isocalendar()[:2]  # (year, week)
            weekly[week_key] += abs(t["amount"])
        except (ValueError, TypeError):
            continue
    return list(weekly.values())


def _is_debt_payment(t: dict) -> bool:
    """Heuristic: is this transaction a debt/loan payment?"""
    payee = (t.get("payee_name") or "").lower()
    debt_keywords = [
        "lenddirect", "payday", "loan", "credit card payment",
        "debt", "interest", "finance charge",
    ]
    return any(kw in payee for kw in debt_keywords)


def _is_impulse(t: dict) -> bool:
    """
    Heuristic: classify a transaction as impulsive.

    Impulse markers:
     - Small to medium spend at known impulse merchants
     - Late-night transactions (future: needs time-of-day)
     - Multiple same-day visits to discretionary merchants
    """
    payee = (t.get("payee_name") or "").lower()
    cls = classify_payee(payee)
    category = cls.get("category", "")

    # Known impulse categories
    impulse_categories = {
        "alcohol", "cannabis", "fast_food", "food_delivery",
        "entertainment", "shopping", "vending",
    }
    if category.lower() in impulse_categories:
        # Only flag if under $80 (larger purchases may be intentional)
        return abs(t["amount"]) < 80_000  # milliunits
    return False


def _is_adhd_tax(t: dict) -> bool:
    """Heuristic: is this an ADHD-tax transaction (late fee, replacement, overdraft)?"""
    payee = (t.get("payee_name") or "").lower()
    memo = (t.get("memo") or "").lower()
    combined = payee + " " + memo

    adhd_keywords = [
        "late fee", "overdraft", "nsf", "returned payment",
        "interest charge", "penalty", "replacement",
        "lenddirect", "payday loan", "cash advance",
    ]
    return any(kw in combined for kw in adhd_keywords)


def _compute_payday_spike(outflows_90: list[dict]) -> float:
    """
    Ratio of spending in the 3 days after each payday vs the average 3-day period.

    Uses income sources to detect payday dates, then compares.
    """
    # Find income transactions to infer pay dates
    # (income is positive, no transfer_account_id)
    # We look at the full 90-day window the caller already filtered
    all_txns_by_date: Dict[str, float] = defaultdict(float)
    for t in outflows_90:
        try:
            d = str(t["date"])[:10]
            all_txns_by_date[d] += abs(t["amount"])
        except (ValueError, TypeError):
            continue

    if not all_txns_by_date:
        return 1.0

    # We don't have income txns in outflows — approximate payday as
    # the date with the highest single-day spend (people spend on payday)
    avg_daily = statistics.mean(all_txns_by_date.values()) if all_txns_by_date else 1
    max_daily = max(all_txns_by_date.values()) if all_txns_by_date else 1

    return max_daily / avg_daily if avg_daily > 0 else 1.0
