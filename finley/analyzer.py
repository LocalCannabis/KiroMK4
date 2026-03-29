"""
finley/analyzer.py — Financial analysis functions for the Finley persona.

All functions operate on the local SQLite cache (FinleyDB) for near-instant
responses. Each returns a dict suitable for GPT to format conversationally.

Currency values are in YNAB milliunits unless otherwise noted.
Negative amounts = outflows (spending). Positive = inflows (income).
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .config import load_config, resolve_category
from .db import FinleyDB, format_currency, milliunits_to_dollars

logger = logging.getLogger("kiro.finley.analyzer")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _first_of_month(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-01")


def _last_of_month(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    _, last_day = monthrange(dt.year, dt.month)
    return dt.strftime(f"%Y-%m-{last_day:02d}")


def _resolve_period(period: str) -> tuple[str, str]:
    """
    Convert a human-friendly period string to (since_date, until_date).

    Supported: 'this_month', 'last_month', 'this_week', 'last_7_days',
               'last_30_days', 'last_90_days', or 'YYYY-MM' for a specific month.
    """
    now = datetime.now()

    if period == "this_month":
        return _first_of_month(now), _today()
    elif period == "last_month":
        first_this = now.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return _first_of_month(last_prev), _last_of_month(last_prev)
    elif period == "this_week":
        monday = now - timedelta(days=now.weekday())
        return monday.strftime("%Y-%m-%d"), _today()
    elif period == "last_7_days":
        return (now - timedelta(days=7)).strftime("%Y-%m-%d"), _today()
    elif period == "last_30_days":
        return (now - timedelta(days=30)).strftime("%Y-%m-%d"), _today()
    elif period == "last_90_days":
        return (now - timedelta(days=90)).strftime("%Y-%m-%d"), _today()
    elif len(period) == 7 and period[4] == "-":
        # YYYY-MM format
        try:
            dt = datetime.strptime(period + "-01", "%Y-%m-%d")
            return _first_of_month(dt), _last_of_month(dt)
        except ValueError:
            pass

    # Fallback: this month
    return _first_of_month(now), _today()


def _days_in_period(since: str, until: str) -> int:
    """Number of days in a date range (inclusive)."""
    d1 = datetime.strptime(since, "%Y-%m-%d")
    d2 = datetime.strptime(until, "%Y-%m-%d")
    return max((d2 - d1).days + 1, 1)


def _days_remaining_in_month() -> int:
    now = datetime.now()
    _, last_day = monthrange(now.year, now.month)
    return max(last_day - now.day, 0)


# ---------------------------------------------------------------------------
# Spending queries
# ---------------------------------------------------------------------------

def spending_by_category(
    db: FinleyDB,
    period: str = "this_month",
    category: str | None = None,
    cfg: dict | None = None,
) -> dict:
    """
    Total spending per category for a given period.
    If category is specified, returns just that one.
    """
    since, until = _resolve_period(period)

    if category:
        if cfg is None:
            cfg = load_config()
        resolved = resolve_category(category, cfg, db.get_all_category_names())
        if resolved is None:
            return {
                "error": True,
                "message": f"I couldn't find a category matching '{category}'. "
                           "Could you be more specific?",
            }
        rows = db.spending_by_category(since, until)
        match = next((r for r in rows if r["category_name"] == resolved), None)
        if match:
            return {
                "category": resolved,
                "total": match["total"],
                "total_formatted": format_currency(match["total"]),
                "txn_count": match["txn_count"],
                "period": period,
                "since": since,
                "until": until,
            }
        return {
            "category": resolved,
            "total": 0,
            "total_formatted": "$0.00",
            "txn_count": 0,
            "period": period,
            "since": since,
            "until": until,
        }

    rows = db.spending_by_category(since, until)
    return {
        "categories": [
            {
                "name": r["category_name"],
                "total": r["total"],
                "total_formatted": format_currency(r["total"]),
                "txn_count": r["txn_count"],
            }
            for r in rows
        ],
        "period": period,
        "since": since,
        "until": until,
    }


def spending_by_payee(db: FinleyDB, period: str = "this_month") -> dict:
    """Total spending grouped by payee/merchant for a period."""
    since, until = _resolve_period(period)
    rows = db.spending_by_payee(since, until)
    return {
        "payees": [
            {
                "name": r["payee_name"],
                "total": r["total"],
                "total_formatted": format_currency(r["total"]),
                "txn_count": r["txn_count"],
            }
            for r in rows
        ],
        "period": period,
        "since": since,
        "until": until,
    }


def spending_trend(
    db: FinleyDB,
    category: str,
    months: int = 3,
    cfg: dict | None = None,
) -> dict:
    """Month-over-month spending trend for a category."""
    if cfg is None:
        cfg = load_config()
    resolved = resolve_category(category, cfg, db.get_all_category_names())
    if resolved is None:
        return {"error": True, "message": f"No category matching '{category}'."}

    now = datetime.now()
    trend = []
    for i in range(months):
        dt = now.replace(day=1) - timedelta(days=i * 30)
        since = _first_of_month(dt)
        until = _last_of_month(dt)
        rows = db.spending_by_category(since, until)
        match = next((r for r in rows if r["category_name"] == resolved), None)
        total = match["total"] if match else 0
        trend.append({
            "month": dt.strftime("%Y-%m"),
            "total": total,
            "total_formatted": format_currency(total),
        })

    trend.reverse()  # oldest first

    # Direction indicator
    if len(trend) >= 2 and trend[-2]["total"] != 0:
        prev = abs(trend[-2]["total"])
        curr = abs(trend[-1]["total"])
        if prev > 0:
            pct_change = int((curr / prev - 1) * 100)
        else:
            pct_change = 0
    else:
        pct_change = 0

    return {
        "category": resolved,
        "months": trend,
        "direction": "up" if pct_change > 5 else "down" if pct_change < -5 else "flat",
        "pct_change": pct_change,
    }


def daily_spending_rate(db: FinleyDB, period: str = "this_month") -> dict:
    """Average daily spend for the period."""
    since, until = _resolve_period(period)
    total = db.total_spending(since, until)
    days = _days_in_period(since, until)
    daily = int(total / days) if days > 0 else 0
    return {
        "daily_rate": daily,
        "daily_rate_formatted": format_currency(daily),
        "total_spending": total,
        "total_formatted": format_currency(total),
        "days": days,
        "period": period,
    }


def top_transactions(
    db: FinleyDB,
    n: int = 5,
    period: str = "this_month",
) -> dict:
    """Largest transactions (by absolute amount) in a period."""
    since, until = _resolve_period(period)
    txns = db.get_transactions(since_date=since, until_date=until)
    # Exclude system entries (starting balances, transfers to self)
    txns = [t for t in txns if t.get("payee_name") != "Starting Balance"
            and t.get("classified_category") != "System"]
    # Sort by absolute amount descending
    txns.sort(key=lambda t: abs(t["amount"]), reverse=True)
    top = txns[:n]
    return {
        "transactions": [
            {
                "date": t["date"],
                "payee": t["payee_name"],
                "category": t.get("classified_category") or t.get("category_name") or "Unknown",
                "amount": t["amount"],
                "amount_formatted": format_currency(t["amount"]),
            }
            for t in top
        ],
        "period": period,
        "since": since,
        "until": until,
    }


# ---------------------------------------------------------------------------
# Budget health
# ---------------------------------------------------------------------------

def budget_vs_actual(db: FinleyDB, month: str = "this_month") -> dict:
    """Per-category budgeted vs. actual spending for a month."""
    cats = db.get_categories()
    results = []
    for cat in cats:
        budgeted = cat.get("budgeted", 0)
        activity = cat.get("activity", 0)  # negative = spending
        balance = cat.get("balance", 0)
        if budgeted == 0 and activity == 0:
            continue
        results.append({
            "category": cat["name"],
            "group": cat.get("group_name", ""),
            "budgeted": budgeted,
            "budgeted_formatted": format_currency(budgeted),
            "spent": activity,
            "spent_formatted": format_currency(activity),
            "remaining": balance,
            "remaining_formatted": format_currency(balance),
            "over_budget": balance < 0,
        })

    over_count = sum(1 for r in results if r["over_budget"])
    return {
        "categories": results,
        "month": month,
        "total_over_budget": over_count,
    }


def overspent_categories(db: FinleyDB) -> dict:
    """Categories where spending has exceeded the budget (balance < 0)."""
    cats = db.get_categories()
    overspent = [
        {
            "category": c["name"],
            "over_by": abs(c["balance"]),
            "over_by_formatted": format_currency(abs(c["balance"])),
            "budgeted": c.get("budgeted", 0),
            "budgeted_formatted": format_currency(c.get("budgeted", 0)),
        }
        for c in cats
        if c.get("balance", 0) < 0
    ]
    return {"overspent": overspent, "count": len(overspent)}


def remaining_budget(
    db: FinleyDB,
    category: str,
    cfg: dict | None = None,
) -> dict:
    """How much is left in a specific category's budget."""
    if cfg is None:
        cfg = load_config()
    resolved = resolve_category(category, cfg, db.get_all_category_names())
    if resolved is None:
        return {"error": True, "message": f"No category matching '{category}'."}

    cats = db.get_categories()
    match = next((c for c in cats if c["name"] == resolved), None)
    if not match:
        return {"error": True, "message": f"Category '{resolved}' not found in budget."}

    return {
        "category": resolved,
        "budgeted": match.get("budgeted", 0),
        "budgeted_formatted": format_currency(match.get("budgeted", 0)),
        "spent": match.get("activity", 0),
        "spent_formatted": format_currency(match.get("activity", 0)),
        "remaining": match.get("balance", 0),
        "remaining_formatted": format_currency(match.get("balance", 0)),
        "over_budget": match.get("balance", 0) < 0,
    }


def days_until_broke(
    db: FinleyDB,
    category: str,
    cfg: dict | None = None,
) -> dict:
    """At the current daily spending rate, when will this category hit zero?"""
    if cfg is None:
        cfg = load_config()
    resolved = resolve_category(category, cfg, db.get_all_category_names())
    if resolved is None:
        return {"error": True, "message": f"No category matching '{category}'."}

    cats = db.get_categories()
    match = next((c for c in cats if c["name"] == resolved), None)
    if not match:
        return {"error": True, "message": f"Category '{resolved}' not found."}

    balance = match.get("balance", 0)
    if balance <= 0:
        return {
            "category": resolved,
            "days_left": 0,
            "message": f"Already overspent by {format_currency(abs(balance))}.",
            "balance": balance,
            "balance_formatted": format_currency(balance),
        }

    # Calculate daily rate for this category this month
    since = _first_of_month()
    rows = db.spending_by_category(since, _today())
    cat_row = next((r for r in rows if r["category_name"] == resolved), None)
    if not cat_row or cat_row["total"] == 0:
        return {
            "category": resolved,
            "days_left": None,
            "message": f"No spending yet this month — full {format_currency(balance)} remaining.",
            "balance": balance,
            "balance_formatted": format_currency(balance),
        }

    days_elapsed = _days_in_period(since, _today())
    daily_rate = abs(cat_row["total"]) / max(days_elapsed, 1)
    days_left = int(balance / daily_rate) if daily_rate > 0 else None
    days_remaining = _days_remaining_in_month()

    return {
        "category": resolved,
        "days_left": days_left,
        "days_remaining_in_month": days_remaining,
        "will_last": days_left >= days_remaining if days_left is not None else True,
        "balance": balance,
        "balance_formatted": format_currency(balance),
        "daily_rate": int(daily_rate),
        "daily_rate_formatted": format_currency(int(daily_rate)),
    }


# ---------------------------------------------------------------------------
# Account overview
# ---------------------------------------------------------------------------

def recent_transactions(
    db: FinleyDB,
    n: int = 5,
    account: str = "",
    period: str = "last_30_days",
) -> dict:
    """
    Most recent transactions, optionally filtered by account name.
    Returns them in reverse chronological order (newest first).
    """
    since, until = _resolve_period(period)
    kwargs: dict[str, Any] = {"since_date": since, "until_date": until, "limit": n}
    if account:
        kwargs["account_name"] = account
    txns = db.get_transactions(**kwargs)
    return {
        "transactions": [
            {
                "date": t["date"],
                "payee": t["payee_name"],
                "category": t["category_name"],
                "account": t["account_name"],
                "amount": t["amount"],
                "amount_formatted": format_currency(t["amount"]),
                "cleared": t.get("cleared", ""),
            }
            for t in txns
        ],
        "account_filter": account or "all",
        "period": period,
        "since": since,
        "until": until,
    }


def account_balances(db: FinleyDB) -> dict:
    """Current balance for all open accounts."""
    accounts = db.get_accounts(on_budget_only=False)
    return {
        "accounts": [
            {
                "name": a["name"],
                "type": a["type"],
                "balance": a["balance"],
                "balance_formatted": format_currency(a["balance"]),
                "on_budget": bool(a["on_budget"]),
            }
            for a in accounts
        ],
    }


def net_worth(db: FinleyDB) -> dict:
    """Sum of all on-budget account balances."""
    accounts = db.get_accounts(on_budget_only=True)
    total = sum(a["balance"] for a in accounts)
    return {
        "net_worth": total,
        "net_worth_formatted": format_currency(total),
        "account_count": len(accounts),
    }


def credit_card_balances(db: FinleyDB) -> dict:
    """Balances on credit card accounts."""
    accounts = db.get_accounts()
    cards = [a for a in accounts if a.get("type") == "creditCard"]
    total = sum(a["balance"] for a in cards)
    return {
        "cards": [
            {
                "name": a["name"],
                "balance": a["balance"],
                "balance_formatted": format_currency(a["balance"]),
            }
            for a in cards
        ],
        "total_owed": total,
        "total_owed_formatted": format_currency(total),
    }


# ---------------------------------------------------------------------------
# Upcoming / recurring
# ---------------------------------------------------------------------------

def upcoming_bills(db: FinleyDB, days: int = 7) -> dict:
    """Scheduled transactions in the next N days."""
    cutoff = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    today = _today()
    scheduled = db.get_scheduled_transactions()
    upcoming = [
        s for s in scheduled
        if s.get("date_next") and today <= s["date_next"] <= cutoff
    ]
    return {
        "bills": [
            {
                "payee": s["payee_name"],
                "category": s["category_name"],
                "amount": s["amount"],
                "amount_formatted": format_currency(s["amount"]),
                "date": s["date_next"],
            }
            for s in upcoming
        ],
        "days_ahead": days,
        "total": sum(s.get("amount", 0) for s in upcoming),
        "total_formatted": format_currency(sum(s.get("amount", 0) for s in upcoming)),
    }


def recurring_summary(db: FinleyDB) -> dict:
    """All recurring/scheduled transactions and their frequencies."""
    scheduled = db.get_scheduled_transactions()
    return {
        "recurring": [
            {
                "payee": s["payee_name"],
                "category": s["category_name"],
                "amount": s["amount"],
                "amount_formatted": format_currency(s["amount"]),
                "frequency": s["frequency"],
                "next_date": s["date_next"],
            }
            for s in scheduled
        ],
        "count": len(scheduled),
    }


def income_vs_expenses(db: FinleyDB, month: str = "this_month") -> dict:
    """Total inflow vs total outflow for a month."""
    since, until = _resolve_period(month)
    income = db.total_income(since, until)
    expenses = db.total_spending(since, until)  # negative
    net = income + expenses  # positive = saving, negative = deficit
    return {
        "income": income,
        "income_formatted": format_currency(income),
        "expenses": expenses,
        "expenses_formatted": format_currency(expenses),
        "net": net,
        "net_formatted": format_currency(net),
        "saving": net > 0,
        "month": month,
    }


# ---------------------------------------------------------------------------
# Proactive insights (run after each sync)
# ---------------------------------------------------------------------------

def generate_insights(db: FinleyDB, cfg: dict | None = None) -> list[str]:
    """
    Analyze current data and return a list of actionable insight messages.
    Called as a post_sync_callback — queues insights into the DB.
    """
    if cfg is None:
        cfg = load_config()

    insights: list[str] = []
    threshold = cfg.get("large_transaction_threshold", 100_000)

    # 1. Overspending alerts
    overspent = overspent_categories(db)
    for cat in overspent.get("overspent", []):
        insights.append(
            f"Heads up — you've overspent in {cat['category']} "
            f"by {cat['over_by_formatted']}."
        )

    # 2. Budget runway warnings (< 7 days at current pace)
    for cat_name in db.get_all_category_names():
        result = days_until_broke(db, cat_name, cfg)
        if result.get("error"):
            continue
        days_left = result.get("days_left")
        if days_left is not None and 0 < days_left < 7:
            insights.append(
                f"Your {result['category']} budget will run out in about "
                f"{days_left} days at current pace. "
                f"{result['balance_formatted']} remaining."
            )

    # 3. Large recent transactions (last 24h)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    recent = db.get_transactions(since_date=yesterday)
    for t in recent:
        if abs(t["amount"]) > threshold:
            insights.append(
                f"Large transaction: {format_currency(t['amount'])} "
                f"at {t['payee_name'] or 'unknown payee'}."
            )

    # 4. Spending velocity — compare this month's daily rate to last month
    current = daily_spending_rate(db, "this_month")
    previous = daily_spending_rate(db, "last_month")
    curr_rate = abs(current.get("daily_rate", 0))
    prev_rate = abs(previous.get("daily_rate", 0))
    if prev_rate > 0 and curr_rate > prev_rate * 1.2:
        pct = int((curr_rate / prev_rate - 1) * 100)
        insights.append(
            f"You're spending about {pct}% faster this month "
            f"than last month at this point."
        )

    # 5. Positive reinforcement — categories significantly under budget
    cats = db.get_categories()
    now = datetime.now()
    day_of_month = now.day
    _, days_in_month = monthrange(now.year, now.month)
    month_progress = day_of_month / days_in_month

    for cat in cats:
        budgeted = cat.get("budgeted", 0)
        activity = abs(cat.get("activity", 0))
        if budgeted <= 0:
            continue
        expected_spent = budgeted * month_progress
        # If spent less than 60% of what we'd expect at this point
        if activity < expected_spent * 0.6 and budgeted > 10_000:
            remaining = cat.get("balance", 0)
            insights.append(
                f"Nice — you're well under budget on {cat['name']}. "
                f"{format_currency(remaining)} remaining with "
                f"{_days_remaining_in_month()} days to go."
            )

    # Queue insights into the DB for delivery
    for msg in insights:
        db.queue_insight(msg, severity="alert" if "overspent" in msg.lower() else "info")

    logger.info("Generated %d proactive insights", len(insights))
    return insights


# ---------------------------------------------------------------------------
# Tool dispatch map — used by intent_router
# ---------------------------------------------------------------------------

ANALYZER_FUNCTIONS = {
    "spending_by_category": spending_by_category,
    "spending_by_payee": spending_by_payee,
    "spending_trend": spending_trend,
    "daily_spending_rate": daily_spending_rate,
    "top_transactions": top_transactions,
    "recent_transactions": recent_transactions,
    "budget_vs_actual": budget_vs_actual,
    "overspent_categories": overspent_categories,
    "remaining_budget": remaining_budget,
    "days_until_broke": days_until_broke,
    "account_balances": account_balances,
    "net_worth": net_worth,
    "credit_card_balances": credit_card_balances,
    "upcoming_bills": upcoming_bills,
    "recurring_summary": recurring_summary,
    "income_vs_expenses": income_vs_expenses,
}
