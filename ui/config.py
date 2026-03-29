"""
KIRO UI — Configuration

Loads real credentials and persona definitions from the Kiro system.
Database config comes from ~/.kiro/jack_config.yaml (shared by all
personas).  LLM config comes from the main config.yaml ai.openai section.

UI persona visual metadata is defined here; actual system prompts are
built dynamically at request time by the real persona modules
(finley/prompts.py, coach/prompts.py, jack/prompts.py) — the exact
same prompts the voice loop uses.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

# Load .env from project root (same as kiro.py does)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # python-dotenv optional; rely on environment

# ---------------------------------------------------------------------------
# Project root (one level up from ui/)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Database — same source as every other Kiro module
# ---------------------------------------------------------------------------
_JACK_CFG_PATH = Path.home() / ".kiro" / "jack_config.yaml"


def _load_db_url() -> str:
    """Build a PostgreSQL URL from jack_config.yaml."""
    db = {"host": "localhost", "port": 5432, "dbname": "kiro",
          "user": "kiro", "password": "kiro"}
    if _JACK_CFG_PATH.exists():
        try:
            with open(_JACK_CFG_PATH, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            d = cfg.get("database", {})
            db.update({k: d[k] for k in db if k in d})
        except Exception:
            pass
    return "postgresql://{user}:{password}@{host}:{port}/{dbname}".format(**db)


DATABASE_URL = os.getenv("KIRO_DATABASE_URL", _load_db_url())

# ---------------------------------------------------------------------------
# LLM — pull model / key / base_url from config.yaml ai: section
# ---------------------------------------------------------------------------
def _load_llm_config() -> Dict[str, Any]:
    """Read ai.openai from config.yaml, with sensible fallbacks."""
    cfg_path = PROJECT_ROOT / "config.yaml"
    defaults: Dict[str, Any] = {
        "model": "gpt-4o",
        "temperature": 0.4,
        "max_tokens": 4096,       # higher for text chat vs. voice
        "api_key_env": "OPENAI_API_KEY",
        "base_url": None,
    }
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                full = yaml.safe_load(f) or {}
            ai = full.get("ai", {}).get("openai", {})
            defaults.update({k: ai[k] for k in defaults if k in ai})
        except Exception:
            pass
    return defaults


LLM_CONFIG = _load_llm_config()


def _load_routing_config() -> Dict[str, Any]:
    """
    Load the UI model routing config from config.yaml ai.ui_routing.

    Returns a dict with:
      provider, base_url, api_key_env, temperature,
      models: {fast, balanced, deep},
      persona_floor: {persona_key: tier}

    Falls back to OpenAI gpt-4o if OpenRouter key is not set.
    """
    cfg_path = PROJECT_ROOT / "config.yaml"
    defaults: Dict[str, Any] = {
        "provider": "openai",
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
        "temperature": 0.4,
        "models": {
            "fast":     "gpt-4o-mini",
            "balanced": "gpt-4o",
            "deep":     "gpt-4o",
        },
        "persona_floor": {},
    }
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                full = yaml.safe_load(f) or {}
            routing = full.get("ai", {}).get("ui_routing", {})
            # Top-level keys
            for k in ("provider", "base_url", "api_key_env", "temperature"):
                if k in routing:
                    defaults[k] = routing[k]
            if "models" in routing:
                defaults["models"].update(routing["models"])
            if "persona_floor" in routing:
                defaults["persona_floor"].update(routing["persona_floor"])
        except Exception:
            pass
    return defaults


MODEL_ROUTING = _load_routing_config()

# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------
APP_TITLE = "KIRO"
FLASK_PORT = int(os.getenv("KIRO_UI_PORT", "5199"))
SECRET_KEY = os.getenv("KIRO_SECRET", "kiro-dev-key-change-me")

# ---------------------------------------------------------------------------
# Persona Visual Metadata
#
# system_prompt is NOT here.  It's built dynamically at request time by
# get_persona_context() in app.py so the UI always uses the same live
# prompts (with profile data, grow state, task context, etc.) as the
# voice loop.
# ---------------------------------------------------------------------------
PERSONAS: Dict[str, Dict[str, Any]] = {
    "kiro": {
        "name": "KIRO",
        "full_name": "Knowledge Interface and Response Operator",
        "role": "Chief of Staff",
        "accent": "#C9A84C",
        "accent_dim": "#8A7233",
        "avatar": "⚡",
        "greeting": "What do you need, Tim?",
    },
    "jack": {
        "name": "JACK",
        "full_name": "Jack Herer",
        "role": "Cultivation Advisor",
        "accent": "#7CAA2D",
        "accent_dim": "#4A6B1A",
        "avatar": "🌱",
        "greeting": (
            "Hey Tim. What's going on in the tent? "
            "Soil, canopy, environment — whatever's on your mind."
        ),
    },
    "finley": {
        "name": "FINLEY",
        "full_name": "Finley",
        "role": "Financial Advisor",
        "accent": "#4A90A4",
        "accent_dim": "#2E6070",
        "avatar": "💰",
        "greeting": "Alright brother, let's talk money. What's the damage today?",
    },
    "coach": {
        "name": "COACH",
        "full_name": "Coach",
        "role": "Executive Function",
        "accent": "#D4764E",
        "accent_dim": "#9A5536",
        "avatar": "🎯",
        "greeting": "Let's get after it. What are we working on?",
    },
    "chef": {
        "name": "CHEF",
        "full_name": "Chef",
        "role": "Meal Planning",
        "accent": "#B85C38",
        "accent_dim": "#7D3E26",
        "avatar": "🔪",
        "greeting": "Right. What are we making? And don't say cereal.",
    },
    "doc": {
        "name": "DOC",
        "full_name": "Doc",
        "role": "Health & Wellbeing",
        "accent": "#6B8E9B",
        "accent_dim": "#4A6570",
        "avatar": "🩺",
        "greeting": "Hey Tim. What's going on?",
    },
    "sage": {
        "name": "SAGE",
        "full_name": "Sage",
        "role": "Philosophy & Debate",
        "accent": "#8B7D6B",
        "accent_dim": "#5E5347",
        "avatar": "📜",
        "greeting": "What's worth thinking about today?",
    },
}

PERSONA_ORDER = ["kiro", "jack", "finley", "coach", "chef", "doc", "sage"]
