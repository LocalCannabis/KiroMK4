"""
jack/config.py — Configuration management for the Jack master grower persona.

Jack's config lives at ~/.kiro/jack_config.yaml or a path specified
in the main Kiro config.yaml under jack.config_path.

Config-over-code: stage targets, checkin schedules, confidence thresholds,
and environmental ranges are all configurable — not hardcoded.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger("jack.config")

CONFIG_PATH = os.path.expanduser("~/.kiro/jack_config.yaml")

# =============================================================================
# Default configuration — written on first run if no config exists
# =============================================================================
DEFAULT_CONFIG: Dict[str, Any] = {
    # ── Database ──────────────────────────────────────────────────────────
    "database": {
        "host": "localhost",
        "port": 5432,
        "dbname": "kiro",
        "user": "kiro",
        "password": "",       # Set via env JACK_DB_PASSWORD or in config
    },

    # ── Embeddings ────────────────────────────────────────────────────────
    "embeddings": {
        "provider": "openai",            # openai | sentence_transformers
        "openai_model": "text-embedding-ada-002",
        "dimension": 1536,
        "st_model": "all-MiniLM-L6-v2",  # fallback for sentence_transformers
    },

    # ── Knowledge retrieval ───────────────────────────────────────────────
    "knowledge": {
        "retrieval_top_k": 5,
        "min_similarity": 0.70,          # cosine similarity threshold
    },

    # ── Tim's default equipment ───────────────────────────────────────────
    "tent": {
        "tent_size": "2x2",
        "tent_height": "4ft",
        "medium": "peat-based (WP420)",
    },
    "light": {
        "model": "Indo GrowHub 800C",
        "wattage": 200,                  # 4× 50W CREE COB, actual wall draw
        "spectrum": "full spectrum 3000K",
        "efficiency_umol_per_watt": 1.7,  # CREE COB ~1.6-1.8 µmol/J
        "has_dimmer": False,              # No dimmer — distance is the only control
        "fan_cfm": 105,                  # 3-speed integrated exhaust
    },

    # ── Checkin schedule ──────────────────────────────────────────────────
    "checkin": {
        "enabled": True,
        "default_time": "12:00",          # 24h format, local time
        "stale_thresholds": {
            # Hours since last reading before Jack proactively asks
            "humidity": 24,
            "temperature": 24,
            "light_distance": 48,
            "soil_moisture": 72,
            "watering": 72,
        },
        "recent_entries_count": 5,        # How many log entries Jack loads per interaction
    },

    # ── Environmental targets by growth stage ─────────────────────────────
    # These power Jack's flag system. All ranges are [min, max].
    "stage_targets": {
        "seedling": {
            "humidity_pct": [65, 80],
            "temp_canopy_c": [22, 26],
            "vpd_kpa": [0.4, 0.8],
            "dli_mol": [12, 18],
            "light_schedule": "18/6",
        },
        "veg": {
            "humidity_pct": [55, 70],
            "temp_canopy_c": [22, 28],
            "vpd_kpa": [0.8, 1.2],
            "dli_mol": [18, 30],
            "light_schedule": "18/6",
        },
        "transition": {
            "humidity_pct": [50, 65],
            "temp_canopy_c": [22, 28],
            "vpd_kpa": [0.9, 1.3],
            "dli_mol": [25, 40],
            "light_schedule": "12/12",
        },
        "flower": {
            "humidity_pct": [40, 55],
            "temp_canopy_c": [20, 27],
            "vpd_kpa": [1.0, 1.5],
            "dli_mol": [30, 45],
            "light_schedule": "12/12",
        },
        "flush": {
            "humidity_pct": [40, 55],
            "temp_canopy_c": [20, 26],
            "vpd_kpa": [1.0, 1.5],
            "dli_mol": [25, 35],
            "light_schedule": "12/12",
        },
        "dry": {
            "humidity_pct": [55, 65],
            "temp_canopy_c": [16, 21],
            "vpd_kpa": None,             # Not tracked during dry
            "dli_mol": None,
            "light_schedule": "0/24",    # Darkness
        },
        "cure": {
            "humidity_pct": [58, 65],
            "temp_canopy_c": [16, 21],
            "vpd_kpa": None,
            "dli_mol": None,
            "light_schedule": "0/24",
        },
    },

    # ── Confidence framework ──────────────────────────────────────────────
    "confidence": {
        "high_min_sources": 2,
        "medium_min_sources": 1,
    },

    # ── Outdoor grow defaults ─────────────────────────────────────────────
    # Vancouver, BC — PNW coastal climate
    "outdoor": {
        "location": "Vancouver, BC",
        "latitude": 49.28,
        "transplant_earliest": "05-10",   # After last frost risk
        "transplant_target": "05-20",
        "harvest_latest": "10-15",        # Before sustained fall rains
        "plant_count": 4,
        "container_size": "15gal",
        "container_type": "fabric",
        "default_medium": "living soil",
    },

    # ── Outdoor stage targets (advisory — can't control the environment) ──
    # VPD is advisory-only outdoors. DLI comes from sun, not calculated.
    # Humidity targets flag Botrytis risk (>65% in flower = danger zone).
    "outdoor_stage_targets": {
        "seedling": {
            "humidity_pct": [50, 85],
            "temp_canopy_c": [15, 30],
            "soil_temp_min_c": 15,
        },
        "veg": {
            "humidity_pct": [40, 80],
            "temp_canopy_c": [18, 32],
            "soil_temp_min_c": 18,
        },
        "transition": {
            "humidity_pct": [35, 75],
            "temp_canopy_c": [15, 30],
        },
        "flower": {
            "humidity_pct": [35, 65],  # >65 = Botrytis risk flag
            "temp_canopy_c": [12, 28],
        },
        "flush": {
            "humidity_pct": [30, 60],
            "temp_canopy_c": [10, 25],
        },
        "dry": {
            "humidity_pct": [55, 65],
            "temp_canopy_c": [16, 21],
        },
        "cure": {
            "humidity_pct": [58, 65],
            "temp_canopy_c": [16, 21],
        },
    },
}


def load_jack_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load Jack config from disk, merging with defaults for any missing keys.
    Falls back to the default path (~/.kiro/jack_config.yaml) if no path given.
    """
    config_path = path or CONFIG_PATH

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            # Deep-merge with defaults (one level)
            merged = DEFAULT_CONFIG.copy()
            for key, default_val in DEFAULT_CONFIG.items():
                if key in cfg:
                    if isinstance(default_val, dict) and isinstance(cfg[key], dict):
                        merged[key] = {**default_val, **cfg[key]}
                    else:
                        merged[key] = cfg[key]
            logger.info("Jack config loaded from %s", config_path)
            return merged
        except (yaml.YAMLError, OSError) as exc:
            logger.error("Failed to load Jack config from %s: %s — using defaults", config_path, exc)

    logger.info("No Jack config found at %s — using defaults", config_path)
    return DEFAULT_CONFIG.copy()


def save_jack_config(cfg: Dict[str, Any], path: Optional[str] = None) -> None:
    """Write Jack config to disk."""
    config_path = path or CONFIG_PATH
    config_dir = os.path.dirname(config_path)
    os.makedirs(config_dir, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

    # Restrictive permissions (contains DB password potentially)
    os.chmod(config_path, 0o600)
    logger.info("Jack config saved to %s", config_path)
