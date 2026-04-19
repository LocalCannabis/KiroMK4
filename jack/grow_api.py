"""
jack/grow_api.py — Flask blueprint for ESP32 sensor data and relay control.

Provides REST endpoints for:
  - Sensor reading ingestion (from ESP32 over WiFi)
  - Real-time and historical sensor data queries
  - Relay control commands (forwarded to ESP32)
  - Threshold management for auto-alerts

Mount on the Kiro server:
    from jack.grow_api import grow_bp
    app.register_blueprint(grow_bp, url_prefix="/api/grow")
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from .config import load_jack_config
from .db import JackDB

logger = logging.getLogger("jack.grow_api")

grow_bp = Blueprint("grow", __name__)

# Lazy-initialized singletons
_db: Optional[JackDB] = None
_cfg: Optional[Dict[str, Any]] = None

# ESP32 relay command queue — the dashboard/API writes commands here,
# the ESP32 polls GET /api/grow/relay/pending to pick them up.
_relay_command_queue: list = []


def _get_db() -> JackDB:
    global _db, _cfg
    if _db is None:
        _cfg = load_jack_config()
        _db = JackDB(_cfg)
    return _db


def _get_cfg() -> Dict[str, Any]:
    global _cfg
    if _cfg is None:
        _cfg = load_jack_config()
    return _cfg


def _json_safe(obj):
    """Convert non-serializable types for JSON response."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "__float__"):
        return float(obj)
    return str(obj)


def _clean(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _json_safe(v) if not isinstance(v, (str, int, float, bool, list, type(None))) else v
            for k, v in row.items()}


def _resolve_grow_id() -> Optional[int]:
    """
    Resolve grow_id from query params, or fall back to the single active indoor grow.
    """
    gid = request.args.get("grow_id") or request.json.get("grow_id") if request.is_json else request.args.get("grow_id")
    if gid:
        return int(gid)
    # Default: most recent active indoor grow
    db = _get_db()
    grow = db.get_active_grow_by_type("indoor")
    if grow:
        return grow["id"]
    # Fall back to any active grow
    grow = db.get_active_grow()
    return grow["id"] if grow else None


# =============================================================================
# Sensor Reading Ingestion (ESP32 → Beast)
# =============================================================================

@grow_bp.route("/readings", methods=["POST"])
def ingest_reading():
    """
    Receive a sensor reading from the ESP32.

    Expected JSON from ESP32:
    {
        "temp": 24.5,
        "humidity": 62.3,
        "ppfd": 485.2,
        "soil1": 55,
        "soil2": 48,
        "humidifier": "ON",
        "ssr2": "OFF"
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    db = _get_db()

    # Resolve grow_id from payload or default to active indoor grow
    grow_id = data.get("grow_id")
    if not grow_id:
        grow = db.get_active_grow_by_type("indoor")
        if not grow:
            grow = db.get_active_grow()
        if not grow:
            return jsonify({"error": "No active grow found"}), 404
        grow_id = grow["id"]

    # Map ESP32 field names → DB column names
    reading_kwargs = {}
    if data.get("temp") is not None:
        reading_kwargs["temp_c"] = float(data["temp"])
    if data.get("humidity") is not None:
        reading_kwargs["humidity_pct"] = float(data["humidity"])
    if data.get("ppfd") is not None:
        reading_kwargs["ppfd"] = float(data["ppfd"])
    if data.get("soil1") is not None:
        reading_kwargs["soil_moisture_1"] = int(data["soil1"])
    if data.get("soil2") is not None:
        reading_kwargs["soil_moisture_2"] = int(data["soil2"])

    # Relay states
    reading_kwargs["relay_1_on"] = str(data.get("humidifier", "")).upper() == "ON"
    reading_kwargs["relay_2_on"] = str(data.get("ssr2", "")).upper() == "ON"
    reading_kwargs["source"] = data.get("source", "esp32")

    try:
        row = db.insert_sensor_reading(grow_id, **reading_kwargs)

        # Build response — include any pending relay commands for ESP32 to pick up
        response = {"status": "ok", "id": row["id"]}
        if _relay_command_queue:
            response["commands"] = _relay_command_queue.copy()
            _relay_command_queue.clear()

        return jsonify(response), 201
    except Exception as exc:
        logger.error("Sensor ingest failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


# =============================================================================
# Sensor Reading Queries
# =============================================================================

@grow_bp.route("/readings", methods=["GET"])
def get_latest_reading():
    """
    Get the most recent sensor reading.
    Optional query param: ?grow_id=N
    """
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    reading = db.get_latest_sensor_reading(grow_id)
    if not reading:
        return jsonify({"error": "No sensor readings yet"}), 404

    return jsonify(_clean(reading))


@grow_bp.route("/readings/history", methods=["GET"])
def get_reading_history():
    """
    Get sensor reading history.
    Query params:
        ?grow_id=N  (optional, defaults to active indoor)
        ?minutes=60 (time window, default 60)
        ?limit=500  (max rows, default 500)
    """
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    minutes = int(request.args.get("minutes", 60))
    limit = int(request.args.get("limit", 500))

    readings = db.get_sensor_history(grow_id, minutes=minutes, limit=limit)
    return jsonify([_clean(r) for r in readings])


@grow_bp.route("/readings/stats", methods=["GET"])
def get_reading_stats():
    """
    Get aggregated sensor stats (min/max/avg) over a time window.
    Query params:
        ?grow_id=N
        ?minutes=60
    """
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    minutes = int(request.args.get("minutes", 60))
    stats = db.get_sensor_stats(grow_id, minutes=minutes)
    return jsonify(_clean(stats))


@grow_bp.route("/readings/hourly", methods=["GET"])
def get_hourly_readings():
    """
    Hourly rollup data.  ?grow_id=N&hours=24
    """
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    hours = int(request.args.get("hours", 24))
    rows = db.get_hourly_history(grow_id, hours=hours)
    return jsonify([_clean(r) for r in rows])


@grow_bp.route("/readings/daily", methods=["GET"])
def get_daily_readings():
    """
    Daily rollup data.  ?grow_id=N&days=30
    """
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    days = int(request.args.get("days", 30))
    rows = db.get_daily_history(grow_id, days=days)
    return jsonify([_clean(r) for r in rows])


# =============================================================================
# Relay Control
# =============================================================================

@grow_bp.route("/relay/<int:relay_id>", methods=["POST"])
def control_relay(relay_id: int):
    """
    Queue a relay command for the ESP32 to pick up.

    JSON body:
        {"action": "on" | "off", "source": "api" | "jack" | "manual"}
    
    The ESP32 picks up commands in the response to its next POST /readings.
    """
    if relay_id not in (1, 2):
        return jsonify({"error": "relay_id must be 1 or 2"}), 400

    data = request.get_json(silent=True)
    if not data or "action" not in data:
        return jsonify({"error": "JSON body with 'action' required"}), 400

    action = data["action"].lower()
    if action not in ("on", "off"):
        return jsonify({"error": "action must be 'on' or 'off'"}), 400

    source = data.get("source", "api")
    reason = data.get("reason", "")

    # Queue command for ESP32
    cmd = f"{'ON' if action == 'on' else 'OFF'}{relay_id}"
    _relay_command_queue.append(cmd)

    # Log the relay event
    db = _get_db()
    grow_id = _resolve_grow_id()
    event = db.insert_relay_event(
        grow_id=grow_id,
        relay_id=relay_id,
        action=action,
        source=source,
        reason=reason,
    )

    return jsonify({
        "status": "queued",
        "command": cmd,
        "event_id": event["id"],
    })


@grow_bp.route("/relay/events", methods=["GET"])
def get_relay_events():
    """Get relay event history. ?grow_id=N&relay_id=1&limit=20"""
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    relay_id = request.args.get("relay_id", type=int)
    limit = int(request.args.get("limit", 20))

    events = db.get_relay_events(grow_id, relay_id=relay_id, limit=limit)
    return jsonify([_clean(e) for e in events])


# =============================================================================
# Threshold Management
# =============================================================================

@grow_bp.route("/thresholds", methods=["GET"])
def get_thresholds():
    """Get alert thresholds for a grow. ?grow_id=N"""
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    thresholds = db.get_thresholds(grow_id)
    if not thresholds:
        # Return defaults
        cfg = _get_cfg()
        return jsonify({
            "grow_id": grow_id,
            "temp_min_c": 20.0, "temp_max_c": 28.0,
            "humidity_min_pct": 50.0, "humidity_max_pct": 70.0,
            "vpd_min_kpa": 0.8, "vpd_max_kpa": 1.2,
            "ppfd_min": 400.0, "ppfd_max": 800.0,
            "soil_moisture_min": 40, "soil_moisture_max": 70,
            "humidifier_on_below": 55.0,
            "alert_cooldown_min": 30,
            "is_default": True,
        })

    return jsonify(_clean(thresholds))


@grow_bp.route("/thresholds", methods=["POST", "PUT"])
def upsert_thresholds():
    """
    Create or update alert thresholds for a grow.
    JSON body with threshold fields (all optional, only provided fields update).
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    db = _get_db()
    grow_id = data.pop("grow_id", None) or _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    thresholds = db.upsert_thresholds(int(grow_id), **data)
    return jsonify(_clean(thresholds))


# =============================================================================
# Dashboard data (combined endpoint for the UI)
# =============================================================================

@grow_bp.route("/dashboard", methods=["GET"])
def dashboard_data():
    """
    Combined endpoint for the grow dashboard UI.
    Returns latest reading, stats, thresholds, relay events, and grow info
    in a single request.
    
    ?grow_id=N&stats_minutes=60
    """
    db = _get_db()
    grow_id = _resolve_grow_id()
    if not grow_id:
        return jsonify({"error": "No active grow found"}), 404

    stats_minutes = int(request.args.get("stats_minutes", 60))

    grow = db.get_grow(grow_id)
    latest = db.get_latest_sensor_reading(grow_id)
    stats = db.get_sensor_stats(grow_id, minutes=stats_minutes)
    thresholds = db.get_thresholds(grow_id)
    relay_events = db.get_relay_events(grow_id, limit=10)

    # Day number
    day_number = None
    if grow and grow.get("start_date"):
        start = grow["start_date"]
        if isinstance(start, str):
            start = date.fromisoformat(start)
        day_number = (date.today() - start).days + 1

    result = {
        "grow": _clean(grow) if grow else None,
        "day_number": day_number,
        "latest": _clean(latest) if latest else None,
        "stats": _clean(stats) if stats else None,
        "thresholds": _clean(thresholds) if thresholds else None,
        "relay_events": [_clean(e) for e in relay_events],
    }

    # Check for out-of-range flags
    if latest and thresholds:
        flags = []
        if latest.get("temp_c") is not None:
            t = float(latest["temp_c"])
            if t < float(thresholds["temp_min_c"]):
                flags.append("temp_low")
            elif t > float(thresholds["temp_max_c"]):
                flags.append("temp_high")
        if latest.get("humidity_pct") is not None:
            h = float(latest["humidity_pct"])
            if h < float(thresholds["humidity_min_pct"]):
                flags.append("humidity_low")
            elif h > float(thresholds["humidity_max_pct"]):
                flags.append("humidity_high")
        if latest.get("vpd_kpa") is not None:
            v = float(latest["vpd_kpa"])
            if v < float(thresholds["vpd_min_kpa"]):
                flags.append("vpd_low")
            elif v > float(thresholds["vpd_max_kpa"]):
                flags.append("vpd_high")
        if latest.get("ppfd") is not None:
            p = float(latest["ppfd"])
            if p < float(thresholds["ppfd_min"]):
                flags.append("ppfd_low")
            elif p > float(thresholds["ppfd_max"]):
                flags.append("ppfd_high")
        if latest.get("soil_moisture_1") is not None:
            s = int(latest["soil_moisture_1"])
            if s < int(thresholds["soil_moisture_min"]):
                flags.append("soil1_dry")
            elif s > int(thresholds["soil_moisture_max"]):
                flags.append("soil1_wet")
        result["flags"] = flags

    return jsonify(result)
