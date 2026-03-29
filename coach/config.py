"""
coach/config.py — Configuration management for the Coach executive function layer.

Database connection credentials come from ~/.kiro/jack_config.yaml
(shared kiro PostgreSQL instance used by all personas).

Coach-specific behavioural settings live in the coach_config PG table
(config-over-code — seeded by migration 001).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import yaml

logger = logging.getLogger("kiro.coach.config")

JACK_CONFIG_PATH = os.path.expanduser("~/.kiro/jack_config.yaml")


def load_db_config() -> Dict[str, Any]:
    """
    Load PostgreSQL connection config from Jack's config file.
    Returns dict with 'database' key matching the pattern used
    by all persona DB classes.
    """
    if os.path.exists(JACK_CONFIG_PATH):
        try:
            with open(JACK_CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as exc:
            logger.error("Failed to load jack_config.yaml: %s — using defaults", exc)
    # Fallback
    return {
        "database": {
            "host": "localhost",
            "port": 5432,
            "dbname": "kiro",
            "user": "kiro",
            "password": "",
        }
    }
