"""
finley — Kiro's YNAB-powered financial intelligence layer.

Provides the Finley persona with real transaction data, budget tracking,
spending analysis, and proactive financial insights.
"""

from .config import load_config, save_config
from .db import FinleyDB, milliunits_to_dollars, format_currency

__all__ = [
    "load_config",
    "save_config",
    "FinleyDB",
    "milliunits_to_dollars",
    "format_currency",
]
