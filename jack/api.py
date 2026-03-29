"""
jack/api.py — Flask blueprint for Jack's grow state API.

Provides REST endpoints for CRUD on grows, tent_config, log entries,
feeding schedules, and strain profiles. These support both voice-driven
updates (Jack parses conversational input → structured data via tools)
and any future UI.

Mount this blueprint on the Kiro server:
    from jack.api import jack_bp
    app.register_blueprint(jack_bp, url_prefix="/jack")

Hard rules:
  - PostgreSQL only (no SQLite)
  - Flask + Jinja2 + Tailwind for any UI (no React, no Vue)
  - All responses are JSON for API calls
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from .config import load_jack_config
from .db import JackDB
from .checkin import CheckinEngine

logger = logging.getLogger("jack.api")

jack_bp = Blueprint("jack", __name__)

# Lazy-initialized singletons
_db: Optional[JackDB] = None
_cfg: Optional[Dict[str, Any]] = None
_engine: Optional[CheckinEngine] = None


def _get_db() -> JackDB:
    global _db, _cfg
    if _db is None:
        _cfg = load_jack_config()
        _db = JackDB(_cfg)
    return _db


def _get_engine() -> CheckinEngine:
    global _engine
    if _engine is None:
        db = _get_db()
        _engine = CheckinEngine(db, _cfg)
    return _engine


def _json_serializable(obj):
    """Convert non-serializable types for JSON response."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, '__float__'):
        return float(obj)
    return str(obj)


def _clean_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all values in a row dict are JSON serializable."""
    return {k: _json_serializable(v) if not isinstance(v, (str, int, float, bool, list, type(None))) else v
            for k, v in row.items()}


# =============================================================================
# Health
# =============================================================================

@jack_bp.route("/health", methods=["GET"])
def health():
    """Jack subsystem health check."""
    try:
        db = _get_db()
        grow = db.get_active_grow()
        return jsonify({
            "status": "ok",
            "active_grow": grow["strain"] if grow else None,
            "database": "connected",
        })
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


# =============================================================================
# Grows
# =============================================================================

@jack_bp.route("/grows", methods=["GET"])
def list_grows():
    """List all grows, newest first."""
    db = _get_db()
    limit = int(request.args.get("limit", 10))
    grows = db.list_grows(limit=limit)
    return jsonify([_clean_row(g) for g in grows])


@jack_bp.route("/grows", methods=["POST"])
def create_grow():
    """Create a new grow."""
    db = _get_db()
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    try:
        grow = db.create_grow(**data)
        return jsonify(_clean_row(grow)), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@jack_bp.route("/grows/active", methods=["GET"])
def get_active_grow():
    """Get the current active grow."""
    db = _get_db()
    grow = db.get_active_grow()
    if not grow:
        return jsonify({"error": "No active grow"}), 404
    return jsonify(_clean_row(grow))


@jack_bp.route("/grows/<int:grow_id>", methods=["GET"])
def get_grow(grow_id: int):
    db = _get_db()
    grow = db.get_grow(grow_id)
    if not grow:
        return jsonify({"error": "Grow not found"}), 404
    return jsonify(_clean_row(grow))


@jack_bp.route("/grows/<int:grow_id>", methods=["PATCH"])
def update_grow(grow_id: int):
    """Update a grow record."""
    db = _get_db()
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    grow = db.update_grow(grow_id, **data)
    if not grow:
        return jsonify({"error": "Grow not found"}), 404
    return jsonify(_clean_row(grow))


@jack_bp.route("/grows/<int:grow_id>/stage", methods=["POST"])
def update_stage(grow_id: int):
    """Advance a grow to a new stage."""
    db = _get_db()
    data = request.get_json()
    if not data or "stage" not in data:
        return jsonify({"error": "stage field required"}), 400

    try:
        grow = db.update_grow_stage(grow_id, data["stage"])
        if not grow:
            return jsonify({"error": "Grow not found"}), 404
        return jsonify(_clean_row(grow))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# =============================================================================
# Tent Config
# =============================================================================

@jack_bp.route("/tent", methods=["GET"])
def get_tent():
    db = _get_db()
    tent = db.get_tent_config()
    if not tent:
        return jsonify({"error": "No tent config"}), 404
    return jsonify(_clean_row(tent))


@jack_bp.route("/tent", methods=["POST", "PUT"])
def upsert_tent():
    """Create or update tent configuration."""
    db = _get_db()
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    tent = db.upsert_tent_config(**data)
    return jsonify(_clean_row(tent))


# =============================================================================
# Log Entries
# =============================================================================

@jack_bp.route("/grows/<int:grow_id>/logs", methods=["GET"])
def get_logs(grow_id: int):
    """Get recent log entries for a grow."""
    db = _get_db()
    limit = int(request.args.get("limit", 10))
    logs = db.get_recent_logs(grow_id, limit=limit)
    return jsonify([_clean_row(l) for l in logs])


@jack_bp.route("/grows/<int:grow_id>/logs", methods=["POST"])
def create_log(grow_id: int):
    """Create a checkin log entry."""
    db = _get_db()
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    try:
        entry = db.log_entry(grow_id, **data)
        return jsonify(_clean_row(entry)), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# =============================================================================
# Feeding Schedule
# =============================================================================

@jack_bp.route("/grows/<int:grow_id>/feeding", methods=["GET"])
def get_feeding(grow_id: int):
    """Get feeding schedule for a grow."""
    db = _get_db()
    stage = request.args.get("stage")
    schedule = db.get_feeding_schedule(grow_id, stage=stage)
    return jsonify([_clean_row(s) for s in schedule])


@jack_bp.route("/grows/<int:grow_id>/feeding", methods=["POST"])
def add_feeding(grow_id: int):
    """Add a feeding schedule entry."""
    db = _get_db()
    data = request.get_json()
    if not data or "stage" not in data:
        return jsonify({"error": "stage field required"}), 400

    entry = db.add_feeding_schedule(grow_id, **data)
    return jsonify(_clean_row(entry)), 201


# =============================================================================
# Strain Profiles
# =============================================================================

@jack_bp.route("/strains/<strain_name>", methods=["GET"])
def get_strain(strain_name: str):
    db = _get_db()
    profile = db.get_strain_profile(strain_name)
    if not profile:
        return jsonify({"error": "Strain not found"}), 404
    return jsonify(_clean_row(profile))


@jack_bp.route("/strains", methods=["POST"])
def upsert_strain():
    """Create or update a strain profile."""
    db = _get_db()
    data = request.get_json()
    if not data or "strain_name" not in data:
        return jsonify({"error": "strain_name required"}), 400

    strain_name = data.pop("strain_name")
    profile = db.upsert_strain_profile(strain_name, **data)
    return jsonify(_clean_row(profile))


# =============================================================================
# Checkin Engine (computed state)
# =============================================================================

@jack_bp.route("/snapshot", methods=["GET"])
def get_snapshot():
    """
    Get the full computed grow snapshot — everything Jack knows right now.
    Includes grow state, tent config, recent logs, VPD/DLI, trends, flags.
    """
    engine = _get_engine()
    grow_id = request.args.get("grow_id", type=int)
    snapshot = engine.get_grow_snapshot(grow_id=grow_id)

    # Remove non-serializable fields for JSON
    result = {k: v for k, v in snapshot.items() if k != "snapshot_text"}
    result["snapshot_text"] = snapshot.get("snapshot_text", "")

    # Clean nested dicts
    if result.get("grow"):
        result["grow"] = _clean_row(result["grow"])
    if result.get("tent"):
        result["tent"] = _clean_row(result["tent"])
    if result.get("recent_logs"):
        result["recent_logs"] = [_clean_row(l) for l in result["recent_logs"]]
    if result.get("feeding"):
        result["feeding"] = [_clean_row(f) for f in result["feeding"]]
    if result.get("strain"):
        result["strain"] = _clean_row(result["strain"])

    return jsonify(result)
