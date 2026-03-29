"""
finley/config.py — Configuration management for the Finley financial layer.

Secrets (YNAB token, plan_id, server_knowledge) stay in the JSON file
at ~/.kiro/finley_config.json (chmod 600).

Tunable thresholds and behaviour settings live in the finley_config PG
table (config-over-code — seeded by migration 002).

Database connection credentials come from ~/.kiro/jack_config.yaml
(shared kiro PostgreSQL instance used by all personas).
"""

from __future__ import annotations

import json
import logging
import os
import stat
from typing import Any, Dict

import yaml

logger = logging.getLogger("kiro.finley.config")

CONFIG_PATH = os.path.expanduser("~/.kiro/finley_config.json")
JACK_CONFIG_PATH = os.path.expanduser("~/.kiro/jack_config.yaml")

# Secrets / sync-cursor defaults (JSON file only)
DEFAULT_CONFIG: Dict[str, Any] = {
    "ynab_token": "",                # Personal Access Token — user fills in
    "plan_id": "",                   # Auto-populated on first sync
    "last_server_knowledge": {},     # Keyed by endpoint name
    "category_aliases": {
        "Dining Out": ["restaurants", "eating out", "takeout", "food delivery", "delivery"],
        "Groceries": ["grocery", "food shopping", "supermarket"],
        "Cannabis": ["weed", "dispensary", "local cannabis"],
    },
}


def load_config() -> Dict[str, Any]:
    """Load Finley secrets config from disk, merging with defaults for any missing keys."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # Merge defaults for any keys added in newer versions
            for key, default_val in DEFAULT_CONFIG.items():
                if key not in cfg:
                    cfg[key] = default_val
            return cfg
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load finley config: %s — using defaults", exc)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: Dict[str, Any]) -> None:
    """Write secrets config to disk with restrictive permissions (owner-only read/write)."""
    config_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(config_dir, exist_ok=True)

    # Convert any non-JSON-serializable types (e.g., UUID) to strings
    serializable_cfg = {}
    for key, val in cfg.items():
        if val is None or isinstance(val, (str, int, float, bool, list, dict)):
            serializable_cfg[key] = val
        else:
            serializable_cfg[key] = str(val)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(serializable_cfg, f, indent=2)

    try:
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.warning("Could not set restrictive permissions on %s", CONFIG_PATH)


def load_db_config() -> Dict[str, Any]:
    """
    Load PostgreSQL connection config from Jack's config file.
    Returns dict with 'database' key matching the pattern used
    by JackDB and AmbientDB.
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


def get_server_knowledge(cfg: Dict[str, Any], endpoint: str) -> int | None:
    """Get cached server_knowledge for a given endpoint, or None if not yet synced."""
    return cfg.get("last_server_knowledge", {}).get(endpoint)


def set_server_knowledge(cfg: Dict[str, Any], endpoint: str, value: int) -> None:
    """Update server_knowledge for an endpoint (call save_config after)."""
    if "last_server_knowledge" not in cfg:
        cfg["last_server_knowledge"] = {}
    cfg["last_server_knowledge"][endpoint] = value


def resolve_category(query_term: str, cfg: Dict[str, Any], db_categories: list[str]) -> str | None:
    """
    Match a spoken term to a YNAB category name.

    Resolution order:
      1. Exact match (case-insensitive) against DB category names
      2. Alias match from config
      3. Substring match (either direction)
      4. None — caller should ask for clarification
    """
    query_lower = query_term.lower().strip()

    # 1. Direct match
    for cat in db_categories:
        if query_lower == cat.lower():
            return cat

    # 2. Alias match
    for cat_name, aliases in cfg.get("category_aliases", {}).items():
        if query_lower in [a.lower() for a in aliases]:
            return cat_name

    # 3. Substring match
    for cat in db_categories:
        if query_lower in cat.lower() or cat.lower() in query_lower:
            return cat

    # 4. No match — return None so caller can ask for clarification
    return None
