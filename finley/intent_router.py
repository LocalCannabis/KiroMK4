"""
finley/intent_router.py — Dispatches Finley tool calls to analyzer functions.

GPT selects from FINLEY_TOOL_SCHEMAS (OpenAI function-calling format).
This module executes the selected function against the local DB and
returns a JSON-serializable result for GPT to format conversationally.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from .analyzer import ANALYZER_FUNCTIONS
from .config import load_config
from .db import FinleyDB

logger = logging.getLogger("kiro.finley.router")

# ---------------------------------------------------------------------------
# Singleton DB + config (lazy-loaded, reused across calls within a session)
# ---------------------------------------------------------------------------

_db: FinleyDB | None = None
_cfg: dict | None = None


def _get_db() -> FinleyDB:
    global _db
    if _db is None:
        _db = FinleyDB()
    return _db


def _get_cfg() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


# ---------------------------------------------------------------------------
# OpenAI function-calling schemas for Finley's YNAB tools
# ---------------------------------------------------------------------------

FINLEY_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ynab_spending_by_category",
            "description": "Get total spending in a category for a time period. Use when Tim asks about spending on a specific category like groceries, dining, cannabis, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category name or alias (e.g. 'groceries', 'dining out', 'weed'). Omit for all categories.",
                        "default": "",
                    },
                    "period": {
                        "type": "string",
                        "description": "Time period: 'this_month', 'last_month', 'this_week', 'last_7_days', 'last_30_days', or 'YYYY-MM'.",
                        "default": "this_month",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_spending_by_payee",
            "description": "Get spending grouped by merchant/payee. Use when Tim asks where his money is going or about specific merchants.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Time period.",
                        "default": "this_month",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_spending_trend",
            "description": "Show month-over-month spending trend for a category. Use when Tim asks if spending is going up or down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category name or alias.",
                    },
                    "months": {
                        "type": "integer",
                        "description": "How many months to look back.",
                        "default": 3,
                    },
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_daily_spending_rate",
            "description": "Get average daily spend for a period. Use when Tim asks about burn rate or daily spending.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Time period.",
                        "default": "this_month",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_top_transactions",
            "description": "Get the largest transactions in a period. Use when Tim asks about biggest purchases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of transactions to return.",
                        "default": 5,
                    },
                    "period": {
                        "type": "string",
                        "description": "Time period.",
                        "default": "this_month",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_recent_transactions",
            "description": "Get the most recent transactions, optionally filtered by account. Use when Tim asks about the last transaction, recent activity, or transactions on a specific account (e.g. 'last transaction on Coast Capital checking').",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of recent transactions to return.",
                        "default": 5,
                    },
                    "account": {
                        "type": "string",
                        "description": "Account name or partial match (e.g. 'Coast Capital', 'Visa', 'checking'). Omit for all accounts.",
                        "default": "",
                    },
                    "period": {
                        "type": "string",
                        "description": "Time period to search within.",
                        "default": "last_30_days",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_days_until_broke",
            "description": "At current spending rate, how many days until a category budget runs out. Use for runway questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category name or alias.",
                    },
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_account_balances",
            "description": "Get current balances for all accounts. Use when Tim asks about balances.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_net_worth",
            "description": "Calculate total net worth across all on-budget accounts.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_credit_card_balances",
            "description": "Show credit card balances and total owed.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_upcoming_bills",
            "description": "Show scheduled transactions/bills coming up. Use when Tim asks about upcoming bills or what's due.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "How many days ahead to look.",
                        "default": 7,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_recurring_summary",
            "description": "List all recurring/scheduled expenses and their frequencies.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ynab_income_vs_expenses",
            "description": "Compare total income to total expenses. Use when Tim asks if he's saving money.",
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {
                        "type": "string",
                        "description": "'this_month', 'last_month', or 'YYYY-MM'.",
                        "default": "this_month",
                    },
                },
                "required": [],
            },
        },
    },
    # -------------------------------------------------------------------
    # Profiling tools
    # -------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "finley_financial_profile",
            "description": "Get Tim's current financial profile including vital signs, behavioural patterns, and stage. Use when Tim asks 'how am I doing financially' or 'what's my financial situation'.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finley_spending_snapshot",
            "description": "Get a classified breakdown of spending by category with recurring bills, income sources, and impulse patterns. Use when Tim asks 'where is my money going' or 'break down my spending'.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finley_cfpb_start",
            "description": "Start a CFPB Financial Well-Being assessment. Use when Tim says 'check in on how I'm feeling about money' or when Finley proactively suggests a wellbeing check.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finley_cfpb_answer",
            "description": "Record an answer to a CFPB wellbeing question. Use during an active CFPB assessment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "The CFPB question ID (e.g., 'cfpb_3').",
                    },
                    "response": {
                        "type": "integer",
                        "description": "Tim's response on a 1-5 scale.",
                    },
                },
                "required": ["item_id", "response"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Dispatch — called by ToolRegistry.execute()
# ---------------------------------------------------------------------------

# Map from tool name (with ynab_ prefix) to (analyzer_func_name, param_keys)
_DISPATCH_MAP = {
    "ynab_spending_by_category": ("spending_by_category", ["period", "category"]),
    "ynab_spending_by_payee": ("spending_by_payee", ["period"]),
    "ynab_spending_trend": ("spending_trend", ["category", "months"]),
    "ynab_daily_spending_rate": ("daily_spending_rate", ["period"]),
    "ynab_top_transactions": ("top_transactions", ["n", "period"]),
    "ynab_recent_transactions": ("recent_transactions", ["n", "account", "period"]),
    "ynab_days_until_broke": ("days_until_broke", ["category"]),
    "ynab_account_balances": ("account_balances", []),
    "ynab_net_worth": ("net_worth", []),
    "ynab_credit_card_balances": ("credit_card_balances", []),
    "ynab_upcoming_bills": ("upcoming_bills", ["days"]),
    "ynab_recurring_summary": ("recurring_summary", []),
    "ynab_income_vs_expenses": ("income_vs_expenses", ["month"]),
}


def execute_finley_tool(name: str, args: Dict[str, Any]) -> str:
    """
    Execute a Finley YNAB or profiling tool call by name.
    Returns a JSON string of the result for GPT consumption.
    """
    # Handle profiling tools separately
    if name.startswith("finley_"):
        return _execute_profiling_tool(name, args)

    if name not in _DISPATCH_MAP:
        return json.dumps({"error": True, "message": f"Unknown Finley tool: {name}"})

    func_name, param_keys = _DISPATCH_MAP[name]
    func = ANALYZER_FUNCTIONS[func_name]
    db = _get_db()
    cfg = _get_cfg()

    # Build kwargs — db is always first, cfg passed where needed
    kwargs: Dict[str, Any] = {"db": db}

    # Functions that accept cfg for category alias resolution
    needs_cfg = {"spending_by_category", "spending_trend", "remaining_budget", "days_until_broke"}
    if func_name in needs_cfg:
        kwargs["cfg"] = cfg

    # Map args from the tool call
    for key in param_keys:
        if key in args and args[key] != "":
            val = args[key]
            # Type coercion for integers
            if key in ("n", "months", "days"):
                val = int(val)
            kwargs[key] = val

    try:
        result = func(**kwargs)
        return json.dumps(result, default=str)
    except Exception as exc:
        logger.error("Finley tool %s failed: %s", name, exc, exc_info=True)
        return json.dumps({"error": True, "message": f"Tool error: {exc}"})


def get_pending_insights_text(db: FinleyDB | None = None) -> str | None:
    """
    Check for pending proactive insights and return formatted text
    for injection into Finley's conversation, or None if nothing pending.
    Marks returned insights as delivered.
    """
    if db is None:
        db = _get_db()

    pending = db.get_pending_insights(limit=3)
    if not pending:
        return None

    # Mark as delivered immediately
    db.mark_insights_delivered([p["id"] for p in pending])

    if len(pending) == 1:
        return f"[PROACTIVE INSIGHT] {pending[0]['message']}"

    lines = ["[PROACTIVE INSIGHTS]"]
    for p in pending:
        lines.append(f"- {p['message']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Profiling tool executor
# ---------------------------------------------------------------------------

# Session state for multi-turn CFPB assessment
_cfpb_session: Dict[str, int] = {}


def _execute_profiling_tool(name: str, args: Dict[str, Any]) -> str:
    """Dispatch profiling-related tool calls."""
    global _cfpb_session
    db = _get_db()

    try:
        if name == "finley_financial_profile":
            from .profiler import get_profile_summary
            summary = get_profile_summary(db)
            return json.dumps({"profile_summary": summary})

        elif name == "finley_spending_snapshot":
            from .classifier import build_spending_snapshot
            snapshot = build_spending_snapshot(db)
            return json.dumps(snapshot, default=str)

        elif name == "finley_cfpb_start":
            from .cfpb import get_assessment_intro, get_next_question
            _cfpb_session = {}
            intro = get_assessment_intro()
            first_q = get_next_question(_cfpb_session)
            return json.dumps({
                "intro": intro,
                "question": first_q["finley"] if first_q else None,
                "question_id": first_q["id"] if first_q else None,
            })

        elif name == "finley_cfpb_answer":
            from .cfpb import get_next_question, assess_wellbeing, get_assessment_outro
            item_id = args.get("item_id", "")
            response = int(args.get("response", 3))
            _cfpb_session[item_id] = response

            next_q = get_next_question(_cfpb_session)
            if next_q:
                return json.dumps({
                    "recorded": item_id,
                    "question": next_q["finley"],
                    "question_id": next_q["id"],
                    "progress": f"{len(_cfpb_session)}/5",
                })
            else:
                # All answered — score it
                result = assess_wellbeing(db, _cfpb_session)
                outro = get_assessment_outro(result)
                _cfpb_session = {}
                return json.dumps({
                    "complete": True,
                    "result": result,
                    "outro": outro,
                })

        else:
            return json.dumps({"error": True, "message": f"Unknown profiling tool: {name}"})

    except Exception as exc:
        logger.error("Profiling tool %s failed: %s", name, exc, exc_info=True)
        return json.dumps({"error": True, "message": f"Tool error: {exc}"})
