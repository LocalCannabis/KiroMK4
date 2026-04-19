"""
jack/intent_router.py — Tool schemas and dispatch for Jack's grow management tools.

Follows the same pattern as finley/intent_router.py:
  - JACK_TOOL_SCHEMAS: OpenAI function-calling format tool definitions
  - execute_jack_tool(): dispatch function called by tools/registry.py

Jack's tools let the LLM:
  - Record checkin data (environmental readings, observations)
  - Query grow state (status, logs, feeding schedule)
  - Update grow stage transitions
  - Log watering/feeding events
  - Compute VPD and DLI
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jack.intent_router")

# Lazy-loaded singletons
_jack_db = None
_jack_cfg = None
_checkin_engine = None
_knowledge_store = None
_embedder = None


def _get_db():
    global _jack_db
    if _jack_db is None:
        from .config import load_jack_config
        from .db import JackDB
        cfg = _get_cfg()
        _jack_db = JackDB(cfg)
    return _jack_db


def _get_cfg():
    global _jack_cfg
    if _jack_cfg is None:
        from .config import load_jack_config
        _jack_cfg = load_jack_config()
    return _jack_cfg


def _get_checkin_engine():
    global _checkin_engine
    if _checkin_engine is None:
        from .checkin import CheckinEngine
        _checkin_engine = CheckinEngine(_get_db(), _get_cfg())
    return _checkin_engine


def _get_knowledge_store():
    global _knowledge_store, _embedder
    if _knowledge_store is None:
        from .knowledge import KnowledgeStore, EmbeddingProvider
        cfg = _get_cfg()
        try:
            _embedder = EmbeddingProvider(cfg)
            _knowledge_store = KnowledgeStore(cfg.get("database", {}), _embedder)
        except Exception as exc:
            logger.warning("Knowledge store unavailable: %s", exc)
            return None
    return _knowledge_store


# =============================================================================
# Grow resolution helpers
# =============================================================================

def _resolve_grow(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Resolve which grow to target for a tool call.

    Priority:
      1. grow_id explicitly provided — use that grow
      2. grow_type explicitly provided — use most recent active grow of that type
      3. Single active grow — use it
      4. Multiple grows, no hint — return None (caller must ask which grow)
    """
    db = _get_db()

    if args.get("grow_id"):
        return db.get_grow(int(args["grow_id"]))

    if args.get("grow_type"):
        return db.get_active_grow_by_type(args["grow_type"])

    active = db.get_active_grows()
    if len(active) == 1:
        return active[0]
    if len(active) == 0:
        return None
    # Ambiguous — caller handles
    return None


def _resolve_grow_or_err(args: Dict[str, Any]) -> tuple:
    """Returns (grow, None) on success or (None, error_string) on failure."""
    db = _get_db()

    if args.get("grow_id"):
        grow = db.get_grow(int(args["grow_id"]))
        if not grow:
            return None, f"No grow found with ID {args['grow_id']}."
        return grow, None

    if args.get("grow_type"):
        grow = db.get_active_grow_by_type(args["grow_type"])
        if not grow:
            return None, f"No active {args['grow_type']} grow found."
        return grow, None

    active = db.get_active_grows()
    if len(active) == 0:
        return None, "No active grow found. Start a grow first."
    if len(active) == 1:
        return active[0], None

    # Multiple grows — ask which one
    summaries = [f"{g['grow_type']} ({g['strain']}, id={g['id']})" for g in active]
    return None, f"Multiple active grows: {', '.join(summaries)}. Specify grow_type ('indoor' or 'outdoor') or grow_id."


_GROW_PARAMS = {
    "grow_id": {
        "type": "integer",
        "description": "Specific grow ID if known. Use jack_list_grows to see IDs.",
    },
    "grow_type": {
        "type": "string",
        "description": "'indoor' or 'outdoor' — which grow to target. Required if both are active.",
        "enum": ["indoor", "outdoor"],
    },
}

JACK_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "jack_log_checkin",
            "description": (
                "Record environmental readings and observations from a grow checkin. "
                "Call this when Tim reports tent conditions: humidity, temperature, "
                "light distance, soil moisture, or visual observations. "
                "All fields are optional — log whatever Tim reports."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "humidity_tent": {
                        "type": "number",
                        "description": "Tent humidity in percent (e.g. 64.5)",
                    },
                    "humidity_ambient": {
                        "type": "number",
                        "description": "Ambient humidity outside tent in percent",
                    },
                    "temp_canopy_c": {
                        "type": "number",
                        "description": "Temperature at canopy/light level in Celsius",
                    },
                    "temp_pot_c": {
                        "type": "number",
                        "description": "Temperature at pot/soil level in Celsius",
                    },
                    "temp_ambient_c": {
                        "type": "number",
                        "description": "Ambient temperature outside tent in Celsius",
                    },
                    "light_distance_cm": {
                        "type": "integer",
                        "description": "Light distance from canopy in centimeters",
                    },
                    "soil_moisture": {
                        "type": "string",
                        "description": "Soil moisture reading: 'dry', 'moist', 'wet', or probe value",
                    },
                    "plant_observations": {
                        "type": "string",
                        "description": "Visual observations: leaf color, drooping, new growth, spots, etc.",
                    },
                    "water_ph": {
                        "type": "number",
                        "description": "pH of water used (if watering was done)",
                    },
                    "runoff_ec": {
                        "type": "number",
                        "description": "Runoff EC in mS/cm — managed fertility feedback (Grow A)",
                    },
                    "runoff_ph": {
                        "type": "number",
                        "description": "Runoff pH — managed fertility feedback (Grow A)",
                    },
                    "soil_temp_c": {
                        "type": "number",
                        "description": "Soil temperature in Celsius (outdoor, or pot temp)",
                    },
                    "brix": {
                        "type": "number",
                        "description": "Brix reading — plant health/sugar indicator",
                    },
                    "pest_notes": {
                        "type": "string",
                        "description": "Pest and disease observations (both grows)",
                    },
                    "biology_observations": {
                        "type": "string",
                        "description": "Soil biology indicators: earthworm activity, fungal threads, compost quality (Grow B)",
                    },
                    "weather_notes": {
                        "type": "string",
                        "description": "Weather conditions at time of log (outdoor primarily)",
                    },
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_get_grow_status",
            "description": (
                "Get the full current grow snapshot: strain, stage, day number, "
                "tent config, latest readings, VPD, DLI, trends, and flags. "
                "Call this at the start of a conversation or when you need context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_get_recent_logs",
            "description": (
                "Fetch the most recent checkin log entries with environmental data, "
                "observations, and Jack's assessments. Use to review history or trends."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent entries to fetch (default 5, max 20)",
                        "default": 5,
                    },
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_update_grow_stage",
            "description": (
                "Advance the grow to a new growth stage. Valid stages: "
                "seedling, veg, transition, flower, flush, dry, cure, complete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "new_stage": {
                        "type": "string",
                        "description": "The new growth stage",
                        "enum": ["seedling", "veg", "transition", "flower", "flush", "dry", "cure", "complete"],
                    },
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": ["new_stage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_log_watering",
            "description": (
                "Record a watering or feeding event. Call when Tim reports "
                "watering, top dressing, compost tea, or any soil amendment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "water_volume_ml": {
                        "type": "integer",
                        "description": "Volume of water in milliliters",
                    },
                    "water_ph": {
                        "type": "number",
                        "description": "pH of the water",
                    },
                    "feed_details": {
                        "type": "string",
                        "description": "What was applied: nutrients, compost tea recipe, top dress details, amendment, etc.",
                    },
                    "is_feed": {
                        "type": "boolean",
                        "description": "True if this is a feeding (not just plain water)",
                        "default": False,
                    },
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_get_feeding_schedule",
            "description": "Get the feeding schedule for the current growth stage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_compute_vpd",
            "description": (
                "Calculate VPD (Vapor Pressure Deficit) from temperature and humidity. "
                "Returns the VPD value and what it means for the plant at its current stage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "temp_c": {
                        "type": "number",
                        "description": "Air temperature at canopy level in Celsius",
                    },
                    "humidity_pct": {
                        "type": "number",
                        "description": "Relative humidity in percent",
                    },
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": ["temp_c", "humidity_pct"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_compute_dli",
            "description": (
                "Estimate DLI (Daily Light Integral) from light specs and distance. "
                "Indoor only — uses tent config for wattage. Returns the estimated DLI "
                "and whether it's in the optimal range for the current stage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "light_distance_cm": {
                        "type": "integer",
                        "description": "Distance from light to canopy in centimeters",
                    },
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": ["light_distance_cm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_create_grow",
            "description": (
                "Create a new grow record. Call this when Tim is starting a new grow and "
                "you have collected the key details. Do NOT call until you have at least "
                "strain and start_date. After creating an indoor grow, immediately call "
                "jack_setup_tent with the known equipment specs from the persona prompt — "
                "do not ask Tim for information you already have."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "strain": {
                        "type": "string",
                        "description": "Strain name (e.g. 'Wedding Cake', 'Zkittlez')",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Date the grow started, YYYY-MM-DD format (e.g. '2026-03-17')",
                    },
                    "genetics": {
                        "type": "string",
                        "description": "Genetic lineage if known (e.g. 'Triangle Kush x Animal Mints')",
                    },
                    "source": {
                        "type": "string",
                        "description": "Seed bank or clone source",
                    },
                    "seed_or_clone": {
                        "type": "string",
                        "description": "'seed' or 'clone'",
                        "enum": ["seed", "clone"],
                    },
                    "pot_size": {
                        "type": "string",
                        "description": "Pot size (e.g. '3gal', '5gal')",
                    },
                    "pot_type": {
                        "type": "string",
                        "description": "Pot type: 'fabric', 'plastic', 'air pot', etc.",
                    },
                    "plant_count": {
                        "type": "integer",
                        "description": "Number of plants (default 1)",
                    },
                    "current_stage": {
                        "type": "string",
                        "description": "Current growth stage",
                        "enum": ["seedling", "veg", "transition", "flower", "flush", "dry", "cure"],
                    },
                    "light_schedule": {
                        "type": "string",
                        "description": "Light schedule (e.g. '18/6', '12/12', '20/4')",
                    },
                    "medium": {
                        "type": "string",
                        "description": "Growing medium (e.g. 'peat-based (WP420)', 'living soil')",
                    },
                    "grow_type": {
                        "type": "string",
                        "description": "'indoor' or 'outdoor' (default: 'indoor')",
                        "enum": ["indoor", "outdoor"],
                    },
                    "location": {
                        "type": "string",
                        "description": "Location description (e.g. 'tent', 'Vancouver backyard patio')",
                    },
                    "fertility_model": {
                        "type": "string",
                        "description": "'managed' (peat + nutrients) or 'biology_first' (living soil)",
                        "enum": ["managed", "biology_first"],
                    },
                    "notes": {
                        "type": "string",
                        "description": "Any other notes about this grow",
                    },
                },
                "required": ["strain", "start_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_setup_tent",
            "description": (
                "Set up or update tent configuration — light, fans, filter, medium details, etc. "
                "Call this when Tim describes his equipment. Safe to call multiple times; "
                "it upserts the tent config."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "light_model": {
                        "type": "string",
                        "description": "Light model name (e.g. 'HLG 65 V2', 'Mars Hydro TS600')",
                    },
                    "light_wattage": {
                        "type": "integer",
                        "description": "Light wattage draw (actual watts, not 'equivalent')",
                    },
                    "light_spectrum": {
                        "type": "string",
                        "description": "Spectrum description (e.g. 'full spectrum', '3500K', 'mixed')",
                    },
                    "tent_size": {
                        "type": "string",
                        "description": "Tent footprint in feet (default '2x2')",
                    },
                    "tent_height": {
                        "type": "string",
                        "description": "Tent height (e.g. '4ft', '5ft')",
                    },
                    "fan_model": {
                        "type": "string",
                        "description": "Inline or circulation fan model",
                    },
                    "filter_model": {
                        "type": "string",
                        "description": "Carbon filter model",
                    },
                    "humidifier": {
                        "type": "string",
                        "description": "Humidifier make/model if used",
                    },
                    "dehumidifier": {
                        "type": "string",
                        "description": "Dehumidifier make/model if used",
                    },
                    "medium_details": {
                        "type": "string",
                        "description": "Soil recipe or mix description (e.g. 'BuildASoil 3.0 with 30% perlite')",
                    },
                    "other_equipment": {
                        "type": "string",
                        "description": "Any other notable equipment",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_list_grows",
            "description": (
                "List all active grows with their type (indoor/outdoor), strain, stage, "
                "day number, and grow_id. Use this when it's unclear which grow is being "
                "discussed, or before any tool that needs a specific grow_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # ── ESP32 Sensor Tools ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "jack_get_sensor_readings",
            "description": (
                "Get the latest real-time sensor readings from the ESP32 in the grow tent. "
                "Returns temperature, humidity, PPFD, soil moisture (2 probes), VPD, "
                "and relay states (humidifier, SSR2). Call this when Tim asks about "
                "current tent conditions or when you need live data for analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_get_sensor_history",
            "description": (
                "Get historical sensor data from the ESP32 over a time window. "
                "Returns min/max/avg stats and reading count. Use this for trend analysis, "
                "diagnosing overnight swings, or answering questions about conditions "
                "over the last N minutes/hours."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "Time window in minutes (default 60, max 1440 = 24h)",
                        "default": 60,
                    },
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_set_relay",
            "description": (
                "Send a command to toggle a relay on the ESP32. "
                "Relay 1 = humidifier, Relay 2 = reserved (future AC). "
                "Use this when Tim asks to turn something on/off in the tent, "
                "or when sensor data indicates an intervention is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "relay_id": {
                        "type": "integer",
                        "description": "Which relay: 1 (humidifier) or 2 (reserved/AC)",
                        "enum": [1, 2],
                    },
                    "action": {
                        "type": "string",
                        "description": "'on' or 'off'",
                        "enum": ["on", "off"],
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why the relay is being toggled (logged for audit)",
                    },
                },
                "required": ["relay_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_update_thresholds",
            "description": (
                "Update the alert thresholds for a grow. These control when Jack flags "
                "out-of-range conditions and when the humidifier auto-triggers. "
                "Only provide fields you want to change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "temp_min_c": {"type": "number", "description": "Min acceptable temp (°C)"},
                    "temp_max_c": {"type": "number", "description": "Max acceptable temp (°C)"},
                    "humidity_min_pct": {"type": "number", "description": "Min acceptable humidity (%)"},
                    "humidity_max_pct": {"type": "number", "description": "Max acceptable humidity (%)"},
                    "vpd_min_kpa": {"type": "number", "description": "Min acceptable VPD (kPa)"},
                    "vpd_max_kpa": {"type": "number", "description": "Max acceptable VPD (kPa)"},
                    "ppfd_min": {"type": "number", "description": "Min acceptable PPFD (µmol/m²/s)"},
                    "ppfd_max": {"type": "number", "description": "Max acceptable PPFD (µmol/m²/s)"},
                    "soil_moisture_min": {"type": "integer", "description": "Min acceptable soil moisture (%)"},
                    "soil_moisture_max": {"type": "integer", "description": "Max acceptable soil moisture (%)"},
                    "humidifier_on_below": {"type": "number", "description": "Humidity % below which humidifier turns on"},
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jack_get_sensor_analysis",
            "description": (
                "Get a comprehensive sensor analysis: latest readings, 1h and 24h stats, "
                "current thresholds, active flags, and relay event history. Use this for "
                "a full tent health assessment or when Tim asks 'how's the tent doing?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "grow_id": _GROW_PARAMS["grow_id"],
                    "grow_type": _GROW_PARAMS["grow_type"],
                },
                "required": [],
            },
        },
    },
]


# =============================================================================
# Tool execution
# =============================================================================

def execute_jack_tool(name: str, args: Dict[str, Any]) -> str:
    """
    Dispatch a Jack tool call. Called by tools/registry.py.

    Returns a result string for the LLM to format conversationally.
    """
    try:
        if name == "jack_log_checkin":
            return _tool_log_checkin(args)
        elif name == "jack_get_grow_status":
            return _tool_get_grow_status(args)
        elif name == "jack_get_recent_logs":
            return _tool_get_recent_logs(args)
        elif name == "jack_update_grow_stage":
            return _tool_update_grow_stage(args)
        elif name == "jack_log_watering":
            return _tool_log_watering(args)
        elif name == "jack_get_feeding_schedule":
            return _tool_get_feeding_schedule(args)
        elif name == "jack_compute_vpd":
            return _tool_compute_vpd(args)
        elif name == "jack_compute_dli":
            return _tool_compute_dli(args)
        elif name == "jack_create_grow":
            return _tool_create_grow(args)
        elif name == "jack_setup_tent":
            return _tool_setup_tent(args)
        elif name == "jack_list_grows":
            return _tool_list_grows(args)
        elif name == "jack_get_sensor_readings":
            return _tool_get_sensor_readings(args)
        elif name == "jack_get_sensor_history":
            return _tool_get_sensor_history(args)
        elif name == "jack_set_relay":
            return _tool_set_relay(args)
        elif name == "jack_update_thresholds":
            return _tool_update_thresholds(args)
        elif name == "jack_get_sensor_analysis":
            return _tool_get_sensor_analysis(args)
        else:
            return f"Unknown Jack tool: {name}"
    except Exception as exc:
        logger.error("Jack tool '%s' failed: %s", name, exc)
        return f"Tool error: {exc}"


def _tool_log_checkin(args: Dict[str, Any]) -> str:
    """Log a checkin with environmental readings."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    # Compute VPD if we have both temp and humidity
    from .checkin import compute_vpd
    if args.get("temp_canopy_c") and args.get("humidity_tent"):
        args["vpd_kpa"] = compute_vpd(
            float(args["temp_canopy_c"]),
            float(args["humidity_tent"]),
        )

    # Compute DLI if we have light distance (indoor only)
    if args.get("light_distance_cm") and grow.get("grow_type", "indoor") == "indoor":
        db = _get_db()
        tent = db.get_tent_config(grow_id=grow["id"])
        if tent and tent.get("light_wattage"):
            from .checkin import compute_dli, parse_light_schedule
            light_hours, _ = parse_light_schedule(grow.get("light_schedule", "18/6"))
            args["dli_estimate"] = compute_dli(
                light_wattage=tent["light_wattage"],
                light_distance_cm=args["light_distance_cm"],
                photoperiod_hours=light_hours,
            )

    entry = _get_db().log_entry(grow["id"], **args)

    parts = [f"Checkin logged for {grow['strain']} ({grow.get('grow_type', 'indoor')}, day {entry.get('day_number', '?')})."]
    if entry.get("vpd_kpa"):
        parts.append(f"VPD: {entry['vpd_kpa']} kPa.")
    if entry.get("dli_estimate"):
        parts.append(f"Estimated DLI: {entry['dli_estimate']} mol/m²/day.")

    return " ".join(parts)


def _tool_get_grow_status(args: Dict[str, Any]) -> str:
    """Return the full grow state snapshot."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err
    engine = _get_checkin_engine()
    snapshot = engine.get_grow_snapshot(grow_id=grow["id"])
    return snapshot.get("snapshot_text", "No grow state available.")


def _tool_get_recent_logs(args: Dict[str, Any]) -> str:
    """Return formatted recent log entries."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    db = _get_db()
    count = min(int(args.get("count", 5)), 20)
    logs = db.get_recent_logs(grow["id"], limit=count)

    if not logs:
        return "No checkin entries yet for this grow."

    lines = [f"Last {len(logs)} checkins for {grow['strain']}:"]
    for log in logs:
        parts = [f"Day {log.get('day_number', '?')}"]
        ts = log.get("logged_at")
        if hasattr(ts, "strftime"):
            parts[0] += f" ({ts.strftime('%b %d %H:%M')})"

        readings = []
        if log.get("humidity_tent") is not None:
            readings.append(f"humidity {log['humidity_tent']}%")
        if log.get("temp_canopy_c") is not None:
            readings.append(f"temp {log['temp_canopy_c']}°C")
        if log.get("vpd_kpa") is not None:
            readings.append(f"VPD {log['vpd_kpa']} kPa")
        if log.get("soil_moisture"):
            readings.append(f"soil: {log['soil_moisture']}")
        if readings:
            parts.append(", ".join(readings))

        if log.get("plant_observations"):
            parts.append(f"Notes: {log['plant_observations']}")
        if log.get("flags"):
            parts.append(f"Flags: {', '.join(log['flags'])}")

        lines.append(" | ".join(parts))

    return "\n".join(lines)


def _tool_update_grow_stage(args: Dict[str, Any]) -> str:
    """Advance the grow to a new stage."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    new_stage = args["new_stage"]
    old_stage = grow["current_stage"]
    _get_db().update_grow_stage(grow["id"], new_stage)
    return f"Grow stage updated: {old_stage} → {new_stage}. She's moving along."


def _tool_log_watering(args: Dict[str, Any]) -> str:
    """Record a watering or feeding event."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    log_args = {}
    if args.get("water_volume_ml"):
        log_args["water_volume_ml"] = int(args["water_volume_ml"])
    if args.get("water_ph"):
        log_args["water_ph"] = float(args["water_ph"])
    if args.get("feed_details"):
        log_args["feed_details"] = args["feed_details"]

    now = datetime.now()
    log_args["last_watered"] = now
    if args.get("is_feed"):
        log_args["last_feed"] = now

    entry = _get_db().log_entry(grow["id"], **log_args)

    is_feed = args.get("is_feed", False)
    action = "Feeding" if is_feed else "Watering"
    parts = [f"{action} logged for {grow['strain']} (day {entry.get('day_number', '?')})."]
    if args.get("water_volume_ml"):
        parts.append(f"{args['water_volume_ml']}ml.")
    if args.get("water_ph"):
        parts.append(f"pH {args['water_ph']}.")
    if args.get("feed_details"):
        parts.append(f"Applied: {args['feed_details']}")
    return " ".join(parts)


def _tool_get_feeding_schedule(args: Dict[str, Any]) -> str:
    """Return the feeding schedule for the current stage."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    db = _get_db()
    stage = grow["current_stage"]
    schedule = db.get_feeding_schedule(grow["id"], stage=stage)

    if not schedule:
        return f"No feeding schedule configured for {stage} stage."

    lines = [f"Feeding schedule for {grow['strain']} in {stage}:"]
    for entry in schedule:
        method = entry.get("method", "water only")
        interval = entry.get("interval_days")
        parts = [f"- {method}"]
        if interval:
            parts.append(f"every {interval} days")
        if entry.get("recipe"):
            parts.append(f"({entry['recipe']})")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _tool_compute_vpd(args: Dict[str, Any]) -> str:
    """Compute VPD and contextualize for the current stage."""
    from .checkin import compute_vpd

    temp = float(args["temp_c"])
    humidity = float(args["humidity_pct"])
    vpd = compute_vpd(temp, humidity)

    grow, _ = _resolve_grow_or_err(args)
    cfg = _get_cfg()

    result = f"VPD: {vpd} kPa (at {temp}°C, {humidity}% RH)."
    if grow:
        stage = grow["current_stage"]
        grow_type = grow.get("grow_type", "indoor")
        targets_key = "outdoor_stage_targets" if grow_type == "outdoor" else "stage_targets"
        targets = cfg.get(targets_key, {}).get(stage, {})
        vpd_range = targets.get("vpd_kpa")
        advisory = " (advisory — not controlled outdoors)" if grow_type == "outdoor" else ""
        if vpd_range:
            lo, hi = vpd_range
            if vpd < lo:
                result += f" Below target for {stage} ({lo}-{hi} kPa){advisory}."
            elif vpd > hi:
                result += f" Above target for {stage} ({lo}-{hi} kPa){advisory}."
            else:
                result += f" Right in the sweet spot for {stage} ({lo}-{hi} kPa){advisory}."
    return result


def _tool_compute_dli(args: Dict[str, Any]) -> str:
    """Estimate DLI and contextualize for the current stage (indoor only)."""
    from .checkin import compute_dli, parse_light_schedule

    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    if grow.get("grow_type") == "outdoor":
        from .checkin import estimate_outdoor_dli_vancouver
        import datetime as _dt
        outdoor_dli = estimate_outdoor_dli_vancouver(_dt.date.today().month)
        return (
            f"Outdoor DLI (estimated for Vancouver this month): ~{outdoor_dli} mol/m²/day. "
            "This is an average — actual DLI on sunny summer days is much higher. "
            "Outdoor DLI isn't a primary control variable — position and timing matter more."
        )

    db = _get_db()
    tent = db.get_tent_config(grow_id=grow["id"])
    cfg = _get_cfg()

    if not tent or not tent.get("light_wattage"):
        return "Need tent config with light wattage to estimate DLI."

    distance = int(args["light_distance_cm"])
    light_hours, _ = parse_light_schedule(grow.get("light_schedule", "18/6"))

    dli = compute_dli(
        light_wattage=tent["light_wattage"],
        light_distance_cm=distance,
        photoperiod_hours=light_hours,
    )

    result = f"Estimated DLI: {dli} mol/m²/day (light at {distance}cm, {light_hours}h photoperiod)."
    stage = grow["current_stage"]
    targets = cfg.get("stage_targets", {}).get(stage, {})
    dli_range = targets.get("dli_mol")
    if dli_range:
        lo, hi = dli_range
        if dli < lo:
            result += f" Below target for {stage} ({lo}-{hi}). Consider moving the light closer."
        elif dli > hi:
            result += f" Above target for {stage} ({lo}-{hi}). Might want to raise the light a bit."
        else:
            result += f" Right where she wants to be for {stage} ({lo}-{hi})."
    return result


def _tool_create_grow(args: Dict[str, Any]) -> str:
    """Create a new grow record from collected details."""
    db = _get_db()

    new_type = args.get("grow_type", "indoor")

    # Check for an existing active grow of the same type
    existing = db.get_active_grow_by_type(new_type)
    if existing:
        start = existing.get("start_date")
        try:
            from datetime import date as _date
            if isinstance(start, str):
                start = _date.fromisoformat(start)
            days = (_date.today() - start).days
        except Exception:
            days = "?"
        return (
            f"There's already an active {new_type} grow: {existing['strain']} (day {days}). "
            f"Mark it complete first if you want to start a new {new_type} grow."
        )

    create_kwargs: Dict[str, Any] = {
        "strain": args["strain"],
        "start_date": args["start_date"],
    }
    for field in ("genetics", "source", "seed_or_clone", "pot_size", "pot_type",
                  "plant_count", "current_stage", "light_schedule", "medium",
                  "notes", "grow_type", "location", "fertility_model"):
        if args.get(field) is not None:
            create_kwargs[field] = args[field]

    grow = db.create_grow(**create_kwargs)

    stage = grow.get("current_stage", "seedling")
    plant_count = grow.get("plant_count", 1)
    plant_word = "plant" if plant_count == 1 else "plants"
    grow_type_str = grow.get("grow_type", "indoor")
    return (
        f"Grow created. {grow['strain']} — {plant_count} {plant_word} ({grow_type_str}), starting in {stage}. "
        f"Start date: {grow['start_date']}. She's in the books."
    )


def _tool_setup_tent(args: Dict[str, Any]) -> str:
    """Upsert the tent configuration."""
    db = _get_db()

    tent_kwargs = {k: v for k, v in args.items()
                   if v is not None and k not in ("grow_id", "grow_type")}
    if not tent_kwargs:
        return "No tent details provided."

    # Associate with a specific grow if we can resolve one
    grow, _ = _resolve_grow_or_err(args)
    grow_id = grow["id"] if grow else None

    tent = db.upsert_tent_config(grow_id=grow_id, **tent_kwargs)

    parts = ["Tent config saved."]
    if tent.get("light_model"):
        parts.append(f"Light: {tent['light_model']}")
        if tent.get("light_wattage"):
            parts[-1] += f" ({tent['light_wattage']}W)"
    if tent.get("tent_size"):
        parts.append(f"Tent: {tent['tent_size']}")
    if tent.get("medium_details"):
        parts.append(f"Medium: {tent['medium_details']}")

    # DLI preview for indoor grows with wattage set
    if grow and grow.get("grow_type", "indoor") == "indoor" and tent.get("light_wattage"):
        from .checkin import compute_dli, parse_light_schedule
        cfg = _get_cfg()
        light_hours, _ = parse_light_schedule(grow.get("light_schedule", "18/6"))
        dli_at_45 = compute_dli(tent["light_wattage"], 45, light_hours)
        stage = grow["current_stage"]
        targets = cfg.get("stage_targets", {}).get(stage, {})
        dli_range = targets.get("dli_mol")
        if dli_range:
            lo, hi = dli_range
            parts.append(
                f"At 45cm, estimated DLI ≈ {dli_at_45:.0f} mol/m²/day "
                f"(target for {stage}: {lo}–{hi})."
            )

    return " ".join(parts)


def _tool_list_grows(args: Dict[str, Any]) -> str:
    """List all active grows."""
    db = _get_db()
    grows = db.get_active_grows()

    if not grows:
        return "No active grows. Use jack_create_grow to start one."

    from datetime import date as _date
    lines = [f"{len(grows)} active grow(s):"]
    for g in grows:
        start = g.get("start_date")
        try:
            if isinstance(start, str):
                start = _date.fromisoformat(start)
            days = (_date.today() - start).days + 1
        except Exception:
            days = "?"
        grow_type = g.get("grow_type", "indoor")
        fertility = "managed" if g.get("fertility_model", "managed") == "managed" else "biology-first"
        label = "Grow A" if grow_type == "indoor" else "Grow B"
        lines.append(
            f"  [{label}] id={g['id']} | {g['strain']} | {grow_type} | {g['current_stage']} day {days} "
            f"| {fertility} | medium: {g.get('medium', 'unspecified')}"
        )

    return "\n".join(lines)


# =============================================================================
# ESP32 Sensor Tool Implementations
# =============================================================================

def _tool_get_sensor_readings(args: Dict[str, Any]) -> str:
    """Get latest real-time sensor readings from ESP32."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    db = _get_db()
    reading = db.get_latest_sensor_reading(grow["id"])
    if not reading:
        return "No sensor readings yet. The ESP32 may not be connected or sending data."

    # Check age of reading
    recorded = reading.get("recorded_at")
    age_str = ""
    if recorded:
        from datetime import datetime as _dt
        if hasattr(recorded, "timestamp"):
            age_sec = (_dt.now(recorded.tzinfo) if recorded.tzinfo else _dt.now()) - recorded
            age_minutes = int(age_sec.total_seconds() / 60)
            if age_minutes > 5:
                age_str = f" ⚠ Last reading is {age_minutes} minutes old — ESP32 may be offline."

    parts = [f"Live sensor data for {grow['strain']} ({grow.get('grow_type', 'indoor')}):"]
    if reading.get("temp_c") is not None:
        parts.append(f"  Temperature: {reading['temp_c']}°C")
    if reading.get("humidity_pct") is not None:
        parts.append(f"  Humidity: {reading['humidity_pct']}%")
    if reading.get("vpd_kpa") is not None:
        parts.append(f"  VPD: {reading['vpd_kpa']} kPa")
    if reading.get("ppfd") is not None:
        parts.append(f"  PPFD: {reading['ppfd']} µmol/m²/s")
    if reading.get("soil_moisture_1") is not None:
        parts.append(f"  Soil moisture 1: {reading['soil_moisture_1']}%")
    if reading.get("soil_moisture_2") is not None:
        parts.append(f"  Soil moisture 2: {reading['soil_moisture_2']}%")
    parts.append(f"  Humidifier (SSR1): {'ON' if reading.get('relay_1_on') else 'OFF'}")
    parts.append(f"  SSR2 (reserved): {'ON' if reading.get('relay_2_on') else 'OFF'}")
    if age_str:
        parts.append(age_str)

    return "\n".join(parts)


def _tool_get_sensor_history(args: Dict[str, Any]) -> str:
    """Get sensor stats over a time window."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    db = _get_db()
    minutes = min(int(args.get("minutes", 60)), 1440)
    stats = db.get_sensor_stats(grow["id"], minutes=minutes)

    if not stats or not stats.get("reading_count"):
        return f"No sensor readings in the last {minutes} minutes."

    count = int(stats["reading_count"])
    parts = [f"Sensor stats for {grow['strain']} — last {minutes} min ({count} readings):"]

    def _fmt(label, mn, mx, avg, unit=""):
        if mn is not None and mx is not None and avg is not None:
            return f"  {label}: {float(mn):.1f}–{float(mx):.1f}{unit} (avg {float(avg):.1f}{unit})"
        return None

    line = _fmt("Temperature", stats.get("temp_min"), stats.get("temp_max"), stats.get("temp_avg"), "°C")
    if line: parts.append(line)
    line = _fmt("Humidity", stats.get("hum_min"), stats.get("hum_max"), stats.get("hum_avg"), "%")
    if line: parts.append(line)
    line = _fmt("VPD", stats.get("vpd_min"), stats.get("vpd_max"), stats.get("vpd_avg"), " kPa")
    if line: parts.append(line)
    line = _fmt("PPFD", stats.get("ppfd_min"), stats.get("ppfd_max"), stats.get("ppfd_avg"), " µmol/m²/s")
    if line: parts.append(line)

    if stats.get("soil1_avg") is not None:
        parts.append(f"  Soil 1 avg: {float(stats['soil1_avg']):.0f}%")
    if stats.get("soil2_avg") is not None:
        parts.append(f"  Soil 2 avg: {float(stats['soil2_avg']):.0f}%")

    return "\n".join(parts)


def _tool_set_relay(args: Dict[str, Any]) -> str:
    """Send a relay control command to the ESP32."""
    relay_id = int(args["relay_id"])
    action = args["action"].lower()
    reason = args.get("reason", "Jack tool call")

    if relay_id not in (1, 2):
        return "Invalid relay_id. Must be 1 (humidifier) or 2 (reserved/AC)."
    if action not in ("on", "off"):
        return "Invalid action. Must be 'on' or 'off'."

    # Queue command via the grow API
    from .grow_api import _relay_command_queue
    cmd = f"{'ON' if action == 'on' else 'OFF'}{relay_id}"
    _relay_command_queue.append(cmd)

    # Log relay event
    db = _get_db()
    grow, _ = _resolve_grow_or_err(args)
    grow_id = grow["id"] if grow else None
    db.insert_relay_event(grow_id, relay_id, action, source="jack", reason=reason)

    relay_names = {1: "Humidifier", 2: "Reserved (AC)"}
    return (
        f"Command queued: {relay_names.get(relay_id, f'Relay {relay_id}')} → {action.upper()}. "
        f"Will take effect on next ESP32 check-in (~2 seconds). "
        f"Note: SSR1 automatic humidity control may override manual commands."
    )


def _tool_update_thresholds(args: Dict[str, Any]) -> str:
    """Update alert thresholds for a grow."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    db = _get_db()
    threshold_fields = {
        k: v for k, v in args.items()
        if k not in ("grow_id", "grow_type") and v is not None
    }
    if not threshold_fields:
        return "No threshold fields provided to update."

    result = db.upsert_thresholds(grow["id"], **threshold_fields)

    parts = ["Thresholds updated:"]
    for k, v in threshold_fields.items():
        label = k.replace("_", " ").title()
        parts.append(f"  {label}: {v}")

    return "\n".join(parts)


def _tool_get_sensor_analysis(args: Dict[str, Any]) -> str:
    """Comprehensive sensor analysis — latest + stats + thresholds + flags."""
    grow, err = _resolve_grow_or_err(args)
    if err:
        return err

    db = _get_db()
    cfg = _get_cfg()
    grow_id = grow["id"]

    latest = db.get_latest_sensor_reading(grow_id)
    if not latest:
        return "No sensor data available. The ESP32 may not be connected."

    stats_1h = db.get_sensor_stats(grow_id, minutes=60)
    stats_24h = db.get_sensor_stats(grow_id, minutes=1440)
    thresholds = db.get_thresholds(grow_id)
    relay_events = db.get_relay_events(grow_id, limit=5)

    # Start date / day number
    from datetime import date as _date
    start = grow.get("start_date")
    if isinstance(start, str):
        start = _date.fromisoformat(start)
    day_number = (_date.today() - start).days + 1 if start else "?"

    lines = [
        f"=== Sensor Analysis: {grow['strain']} (day {day_number}, {grow['current_stage']}) ===",
        "",
        "── Current Readings ──",
    ]

    if latest.get("temp_c") is not None:
        lines.append(f"  Temp: {latest['temp_c']}°C")
    if latest.get("humidity_pct") is not None:
        lines.append(f"  Humidity: {latest['humidity_pct']}%")
    if latest.get("vpd_kpa") is not None:
        lines.append(f"  VPD: {latest['vpd_kpa']} kPa")
    if latest.get("ppfd") is not None:
        lines.append(f"  PPFD: {latest['ppfd']} µmol/m²/s")
    if latest.get("soil_moisture_1") is not None:
        lines.append(f"  Soil 1: {latest['soil_moisture_1']}% | Soil 2: {latest.get('soil_moisture_2', '?')}%")
    lines.append(f"  Humidifier: {'ON' if latest.get('relay_1_on') else 'OFF'}")

    # 1h stats
    if stats_1h and stats_1h.get("reading_count"):
        count = int(stats_1h["reading_count"])
        lines.extend(["", f"── Last Hour ({count} readings) ──"])
        if stats_1h.get("temp_avg") is not None:
            lines.append(f"  Temp: {float(stats_1h['temp_min']):.1f}–{float(stats_1h['temp_max']):.1f}°C (avg {float(stats_1h['temp_avg']):.1f})")
        if stats_1h.get("hum_avg") is not None:
            lines.append(f"  Humidity: {float(stats_1h['hum_min']):.1f}–{float(stats_1h['hum_max']):.1f}% (avg {float(stats_1h['hum_avg']):.1f})")
        if stats_1h.get("vpd_avg") is not None:
            lines.append(f"  VPD: {float(stats_1h['vpd_min']):.2f}–{float(stats_1h['vpd_max']):.2f} kPa (avg {float(stats_1h['vpd_avg']):.2f})")

    # Flags
    flags = []
    if thresholds and latest:
        def _chk(val_key, t_min_key, t_max_key, flag_name):
            v = latest.get(val_key)
            if v is not None and thresholds.get(t_min_key) is not None:
                if float(v) < float(thresholds[t_min_key]):
                    flags.append(f"{flag_name}_low")
                elif float(v) > float(thresholds[t_max_key]):
                    flags.append(f"{flag_name}_high")

        _chk("temp_c", "temp_min_c", "temp_max_c", "temp")
        _chk("humidity_pct", "humidity_min_pct", "humidity_max_pct", "humidity")
        _chk("vpd_kpa", "vpd_min_kpa", "vpd_max_kpa", "vpd")
        _chk("ppfd", "ppfd_min", "ppfd_max", "ppfd")
        s1 = latest.get("soil_moisture_1")
        if s1 is not None:
            if int(s1) < int(thresholds.get("soil_moisture_min", 40)):
                flags.append("soil1_dry")
            elif int(s1) > int(thresholds.get("soil_moisture_max", 70)):
                flags.append("soil1_wet")

    if flags:
        lines.extend(["", f"⚠ Active flags: {', '.join(flags)}"])
    else:
        lines.extend(["", "✓ All readings within thresholds."])

    # Recent relay events
    if relay_events:
        lines.extend(["", "── Recent Relay Activity ──"])
        relay_names = {1: "Humidifier", 2: "SSR2"}
        for ev in relay_events[:5]:
            ts = ev["recorded_at"]
            if hasattr(ts, "strftime"):
                ts = ts.strftime("%H:%M")
            lines.append(f"  {ts} | {relay_names.get(ev['relay_id'], '?')} → {ev['action'].upper()} ({ev['source']})")

    return "\n".join(lines)


# =============================================================================
# Pending insights / proactive context (for prompt injection)
# =============================================================================

def get_jack_context_for_prompt() -> Dict[str, str]:
    """
    Assemble all context for injection into Jack's system prompt.

    Returns a dict with:
      - indoor_snapshot: text block of Grow A (indoor) state
      - outdoor_snapshot: text block of Grow B (outdoor) state
      - knowledge_context: relevant knowledge chunks
      - active_flags: combined flag text from all active grows
    """
    result = {
        "indoor_snapshot": "",
        "outdoor_snapshot": "",
        "knowledge_context": "",
        "active_flags": "",
    }

    try:
        engine = _get_checkin_engine()
        snapshots = engine.get_all_grows_snapshots()
        result["indoor_snapshot"] = snapshots.get("indoor", "")
        result["outdoor_snapshot"] = snapshots.get("outdoor", "")
        result["active_flags"] = snapshots.get("all_flags", "")
    except Exception as exc:
        logger.warning("Could not assemble grow context: %s", exc)

    # Knowledge retrieval — use indoor snapshot as primary query, fall back to outdoor
    try:
        ks = _get_knowledge_store()
        query_text = result["indoor_snapshot"] or result["outdoor_snapshot"]
        if ks and query_text:
            knowledge = ks.search_for_prompt(
                query_text,
                top_k=_get_cfg().get("knowledge", {}).get("retrieval_top_k", 5),
                min_similarity=_get_cfg().get("knowledge", {}).get("min_similarity", 0.70),
            )
            result["knowledge_context"] = knowledge
    except Exception as exc:
        logger.debug("Knowledge retrieval unavailable: %s", exc)

    return result
