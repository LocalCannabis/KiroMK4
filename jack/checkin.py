"""
jack/checkin.py — Checkin engine for Jack's grow state assessment.

Assembles Jack's context before each interaction:
  1. Fetches current grow + tent config
  2. Loads last N log entries
  3. Computes VPD, DLI from most recent readings
  4. Pulls stage-appropriate targets
  5. Identifies stale data and trending environmental factors
  6. Generates flags for out-of-range readings

This context gets injected into Jack's system prompt so he never gives
advice in a vacuum.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .db import JackDB
from .config import load_jack_config

logger = logging.getLogger("jack.checkin")


# Vancouver, BC — average peak sun hours by month (accounts for cloud cover)
_VANCOUVER_SUN_HOURS = {
    1: 2.1, 2: 3.5, 3: 5.3, 4: 7.0, 5: 8.5, 6: 9.5,
    7: 10.2, 8: 8.8, 9: 6.5, 10: 4.2, 11: 2.3, 12: 1.8,
}


def estimate_outdoor_dli_vancouver(month: int) -> float:
    """
    Estimate outdoor DLI in mol/m²/day for Vancouver, BC.

    Uses monthly average peak sun hours with ~400 µmol/m²/s average
    effective PPFD (accounting for Vancouver's cloud cover). This is an
    order-of-magnitude guide — actual DLI on clear summer days is much
    higher. Outdoor DLI is not a primary control variable.
    """
    sun_hours = _VANCOUVER_SUN_HOURS.get(month, 6.0)
    avg_ppfd = 400.0  # µmol/m²/s effective (conservative for cloudy coastal climate)
    dli = avg_ppfd * sun_hours * 3600 / 1_000_000
    return round(dli, 1)

def compute_vpd(temp_c: float, humidity_pct: float, leaf_offset_c: float = 2.0) -> float:
    """
    Calculate Vapor Pressure Deficit (VPD) in kPa.

    Uses the leaf temperature offset model: leaf temp is assumed to be
    ~2°C below air temp (configurable). This is the standard approach
    from Bugbee's research and Pulse/Dimlux charts.

    Args:
        temp_c: Air temperature at canopy level (°C)
        humidity_pct: Relative humidity (%)
        leaf_offset_c: Leaf temperature offset below air temp (default 2°C)

    Returns:
        VPD in kPa
    """
    # Saturation vapor pressure at air temp (Tetens formula)
    svp_air = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))

    # Saturation vapor pressure at leaf temp
    leaf_temp = temp_c - leaf_offset_c
    svp_leaf = 0.6108 * math.exp((17.27 * leaf_temp) / (leaf_temp + 237.3))

    # Actual vapor pressure
    avp = svp_air * (humidity_pct / 100.0)

    # VPD = leaf SVP - actual VP
    vpd = svp_leaf - avp
    return round(max(0.0, vpd), 2)


def compute_dli(
    light_wattage: int,
    light_distance_cm: int,
    photoperiod_hours: float,
    tent_size_sqft: float = 4.0,
    efficiency_umol_per_watt: float = 1.7,
) -> float:
    """
    Estimate Daily Light Integral (DLI) in mol/m²/day.

    This is an estimate based on light specs, distance, and photoperiod.
    Actual PAR measurement would be more accurate, but this gives Jack
    a working number for assessment.

    Default efficiency is 1.7 µmol/J — calibrated for Tim's GrowHub 800C
    (4× CREE COB, 200W). Modern Samsung/Osram diode boards hit 2.0-2.8,
    but older COB tech is lower.

    Args:
        light_wattage: Light power draw in watts
        light_distance_cm: Distance from canopy in cm
        photoperiod_hours: Hours of light per day
        tent_size_sqft: Tent footprint in square feet (default 4 = 2x2)
        efficiency_umol_per_watt: LED efficiency (µmol/s per watt)

    Returns:
        Estimated DLI in mol/m²/day
    """
    if light_wattage <= 0 or light_distance_cm <= 0 or photoperiod_hours <= 0:
        return 0.0

    # Convert tent area to m²
    tent_area_m2 = tent_size_sqft * 0.0929

    # Total PPFD (µmol/s) at the source
    total_ppfd = light_wattage * efficiency_umol_per_watt

    # Rough inverse-square distance adjustment
    # Reference distance: 30cm (typical close hang)
    ref_distance_cm = 30.0
    distance_factor = (ref_distance_cm / light_distance_cm) ** 2
    distance_factor = min(distance_factor, 3.0)  # Cap unrealistic close distances

    # PPFD at canopy (µmol/m²/s)
    ppfd_at_canopy = (total_ppfd * distance_factor) / tent_area_m2

    # DLI = PPFD × photoperiod_seconds / 1,000,000
    dli = ppfd_at_canopy * (photoperiod_hours * 3600) / 1_000_000

    return round(dli, 1)


def parse_light_schedule(schedule: str) -> Tuple[float, float]:
    """Parse a light schedule string like '18/6' into (light_hours, dark_hours)."""
    try:
        parts = schedule.split("/")
        light = float(parts[0])
        dark = float(parts[1]) if len(parts) > 1 else 24 - light
        return light, dark
    except (ValueError, IndexError):
        return 18.0, 6.0  # Default to veg schedule


# =============================================================================
# Trending analysis
# =============================================================================

def compute_trends(logs: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """
    Compute trending direction for key environmental factors over the
    last 3-5 log entries.

    Returns a dict mapping field → 'rising' | 'falling' | 'stable' | None
    """
    if len(logs) < 2:
        return {}

    # Logs come newest-first; reverse for chronological order
    chronological = list(reversed(logs))

    trend_fields = {
        "humidity_tent": "humidity",
        "temp_canopy_c": "temperature",
        "vpd_kpa": "vpd",
    }

    trends = {}
    for col, label in trend_fields.items():
        values = [log[col] for log in chronological if log.get(col) is not None]
        if len(values) < 2:
            trends[label] = None
            continue

        # Simple linear trend: compare first half average to second half
        mid = len(values) // 2
        first_avg = sum(float(v) for v in values[:mid]) / mid
        second_avg = sum(float(v) for v in values[mid:]) / (len(values) - mid)

        diff = second_avg - first_avg
        threshold = 0.05 * first_avg if first_avg != 0 else 1.0  # 5% change threshold

        if diff > threshold:
            trends[label] = "rising"
        elif diff < -threshold:
            trends[label] = "falling"
        else:
            trends[label] = "stable"

    return trends


# =============================================================================
# Flag generation
# =============================================================================

def generate_flags(
    readings: Dict[str, Any],
    targets: Dict[str, Any],
    trends: Dict[str, Optional[str]],
) -> List[str]:
    """
    Generate concern flags by comparing current readings against
    stage-appropriate target ranges.

    Returns a list of flag strings like 'vpd_high', 'humidity_low',
    'trend_humidity_rising', etc.
    """
    flags = []

    # Helper: check if a reading is within a [min, max] range
    def _check_range(value, range_key: str, flag_prefix: str):
        target_range = targets.get(range_key)
        if target_range is None or value is None:
            return
        lo, hi = target_range
        if float(value) < lo:
            flags.append(f"{flag_prefix}_low")
        elif float(value) > hi:
            flags.append(f"{flag_prefix}_high")

    _check_range(readings.get("humidity_tent"), "humidity_pct", "humidity")
    _check_range(readings.get("temp_canopy_c"), "temp_canopy_c", "temp")
    _check_range(readings.get("vpd_kpa"), "vpd_kpa", "vpd")
    _check_range(readings.get("dli_estimate"), "dli_mol", "dli")

    # Trend flags
    for field, direction in trends.items():
        if direction and direction != "stable":
            flags.append(f"trend_{field}_{direction}")

    return flags


def generate_outdoor_flags(
    readings: Dict[str, Any],
    targets: Dict[str, Any],
    trends: Dict[str, Optional[str]],
) -> List[str]:
    """
    Generate outdoor-specific concern flags.

    Outdoor VPD and DLI are not controlled, so flags focus on:
    - Humidity (Botrytis risk in flower)
    - Temperature (cold snaps, heat stress)
    - Soil temperature (transplant readiness, biology activation)
    """
    flags = []

    def _check_range(value, range_key: str, flag_prefix: str):
        target_range = targets.get(range_key)
        if target_range is None or value is None:
            return
        lo, hi = target_range
        if float(value) < lo:
            flags.append(f"{flag_prefix}_low")
        elif float(value) > hi:
            flags.append(f"{flag_prefix}_high")

    _check_range(readings.get("humidity_tent") or readings.get("humidity_ambient"), "humidity_pct", "humidity")
    _check_range(readings.get("temp_canopy_c") or readings.get("temp_ambient_c"), "temp_canopy_c", "temp")

    # Soil temperature check (outdoor — critical for biology activation)
    soil_temp_min = targets.get("soil_temp_min_c")
    soil_temp = readings.get("soil_temp_c")
    if soil_temp is not None and soil_temp_min is not None:
        if float(soil_temp) < soil_temp_min:
            flags.append("soil_temp_low")

    # Botrytis risk: humidity > 65% in flower/flush
    hum = readings.get("humidity_tent") or readings.get("humidity_ambient")
    if hum is not None and float(hum) > 65:
        flags.append("botrytis_risk_humidity")

    # Trend flags
    for field, direction in trends.items():
        if direction and direction != "stable":
            flags.append(f"trend_{field}_{direction}")

    return flags

class CheckinEngine:
    """
    Assembles Jack's full context for a given interaction.

    Called before every Jack interaction to build the grow state snapshot
    that gets injected into the system prompt.
    """

    def __init__(self, db: JackDB, cfg: Dict[str, Any]) -> None:
        self.db = db
        self.cfg = cfg
        self.checkin_cfg = cfg.get("checkin", {})
        self.stage_targets = cfg.get("stage_targets", {})
        self.recent_count = int(self.checkin_cfg.get("recent_entries_count", 5))

    def get_grow_snapshot(self, grow_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Build a complete grow state snapshot for system prompt injection.

        Returns a dict with:
          - grow: active grow record
          - tent: tent configuration
          - recent_logs: last N log entries
          - feeding: feeding schedule for current stage
          - strain: strain profile (if available)
          - computed: VPD, DLI, trends, flags
          - stale: which readings need refreshing
          - snapshot_text: human-readable summary for the system prompt
        """
        # Get grow
        if grow_id:
            grow = self.db.get_grow(grow_id)
        else:
            grow = self.db.get_active_grow()

        if not grow:
            return {
                "grow": None,
                "snapshot_text": "No active grow found. Tim hasn't started a grow yet.",
            }

        gid = grow["id"]
        stage = grow["current_stage"]

        # Get related data
        tent = self.db.get_tent_config()
        recent_logs = self.db.get_recent_logs(gid, limit=self.recent_count)
        feeding = self.db.get_feeding_schedule(gid, stage=stage)
        strain = self.db.get_strain_profile(grow["strain"])
        stale_thresholds = self.checkin_cfg.get("stale_thresholds", {})
        stale = self.db.get_stale_readings(gid, stale_thresholds)

        # Compute environmental metrics from latest readings
        computed = {}
        latest = recent_logs[0] if recent_logs else {}

        # VPD
        if latest.get("temp_canopy_c") and latest.get("humidity_tent"):
            vpd = compute_vpd(float(latest["temp_canopy_c"]), float(latest["humidity_tent"]))
            computed["vpd_kpa"] = vpd

        # DLI
        if tent and tent.get("light_wattage") and latest.get("light_distance_cm"):
            light_hours, _ = parse_light_schedule(
                latest.get("light_schedule") or grow.get("light_schedule") or "18/6"
            )
            dli = compute_dli(
                light_wattage=tent["light_wattage"],
                light_distance_cm=latest["light_distance_cm"],
                photoperiod_hours=light_hours,
            )
            computed["dli_estimate"] = dli

        # Trends
        trends = compute_trends(recent_logs)
        computed["trends"] = trends

        # Flags
        grow_type = grow.get("grow_type", "indoor")
        if grow_type == "outdoor":
            targets = self.cfg.get("outdoor_stage_targets", {}).get(stage, {})
            readings = {**latest, **computed}
            flags = generate_outdoor_flags(readings, targets, trends)
        else:
            targets = self.stage_targets.get(stage, {})
            readings = {**latest, **computed}
            flags = generate_flags(readings, targets, trends)
        computed["flags"] = flags

        # Day number
        start = grow["start_date"]
        if isinstance(start, str):
            start = date.fromisoformat(start)
        day_number = (date.today() - start).days + 1

        # Build human-readable snapshot (branches on grow_type)
        grow_type = grow.get("grow_type", "indoor")
        if grow_type == "outdoor":
            targets_for_snapshot = self.cfg.get("outdoor_stage_targets", {}).get(stage, {})
        else:
            targets_for_snapshot = self.stage_targets.get(stage, {})

        snapshot_text = self._format_snapshot(
            grow, tent, recent_logs, feeding, strain, computed, stale, day_number, targets_for_snapshot
        )

        return {
            "grow": grow,
            "tent": tent,
            "recent_logs": recent_logs,
            "feeding": feeding,
            "strain": strain,
            "computed": computed,
            "stale": stale,
            "targets": targets_for_snapshot,
            "day_number": day_number,
            "snapshot_text": snapshot_text,
        }

    def _format_snapshot(
        self,
        grow: Dict,
        tent: Optional[Dict],
        logs: List[Dict],
        feeding: List[Dict],
        strain: Optional[Dict],
        computed: Dict,
        stale: Dict,
        day_number: int,
        targets: Dict,
    ) -> str:
        """Format grow state into a concise text block for system prompt injection."""
        grow_type = grow.get("grow_type", "indoor")
        if grow_type == "outdoor":
            return self._format_outdoor_snapshot(grow, logs, feeding, strain, computed, stale, day_number, targets)
        return self._format_indoor_snapshot(grow, tent, logs, feeding, strain, computed, stale, day_number, targets)

    def _format_indoor_snapshot(
        self,
        grow: Dict,
        tent: Optional[Dict],
        logs: List[Dict],
        feeding: List[Dict],
        strain: Optional[Dict],
        computed: Dict,
        stale: Dict,
        day_number: int,
        targets: Dict,
    ) -> str:
        """Format indoor (Grow A) snapshot for system prompt."""
        lines = ["[GROW A — INDOOR TENT]"]

        lines.append(f"Strain: {grow['strain']}")
        if grow.get("genetics"):
            lines.append(f"Genetics: {grow['genetics']}")
        lines.append(f"Stage: {grow['current_stage']} (day {day_number})")
        lines.append(f"Medium: {grow.get('medium', 'peat-based (WP420)')} | Fertility: managed")
        if grow.get("pot_size"):
            lines.append(f"Pot: {grow['pot_size']} {grow.get('pot_type', '')}")
        lines.append(f"Light schedule: {grow.get('light_schedule', 'unknown')}")
        if grow.get("plant_count", 1) > 1:
            lines.append(f"Plants: {grow['plant_count']}")

        # Tent config
        if tent:
            lines.append("")
            lines.append(f"Tent: {tent.get('tent_size', '2x2')} x {tent.get('tent_height', '4ft')}")
            if tent.get("light_model"):
                lines.append(f"Light: {tent['light_model']} ({tent.get('light_wattage', '?')}W)")
            if tent.get("medium_details"):
                lines.append(f"Soil mix: {tent['medium_details']}")
        else:
            lines.append("")
            lines.append("Tent config: NOT SET — use jack_setup_tent with known equipment specs from persona.")

        # Latest readings
        if logs:
            latest = logs[0]
            lines.append("")
            lines.append(f"Last checkin: {latest['logged_at'].strftime('%b %d %H:%M') if hasattr(latest['logged_at'], 'strftime') else latest['logged_at']}")
            if latest.get("humidity_tent") is not None:
                lines.append(f"Tent humidity: {latest['humidity_tent']}%")
            if latest.get("temp_canopy_c") is not None:
                lines.append(f"Canopy temp: {latest['temp_canopy_c']}°C")
            if latest.get("light_distance_cm") is not None:
                lines.append(f"Light distance: {latest['light_distance_cm']}cm")
            if latest.get("soil_moisture"):
                lines.append(f"Soil moisture: {latest['soil_moisture']}")
            if latest.get("runoff_ec") is not None:
                lines.append(f"Runoff EC: {latest['runoff_ec']} mS/cm")
            if latest.get("runoff_ph") is not None:
                lines.append(f"Runoff pH: {latest['runoff_ph']}")
            if latest.get("plant_observations"):
                lines.append(f"Observations: {latest['plant_observations']}")
            if latest.get("pest_notes"):
                lines.append(f"Pest notes: {latest['pest_notes']}")

        # Computed metrics
        if computed.get("vpd_kpa"):
            vpd_range = targets.get("vpd_kpa")
            range_str = f" (target: {vpd_range[0]}-{vpd_range[1]} kPa)" if vpd_range else ""
            lines.append(f"Current VPD: {computed['vpd_kpa']} kPa{range_str}")
        if computed.get("dli_estimate"):
            dli_range = targets.get("dli_mol")
            range_str = f" (target: {dli_range[0]}-{dli_range[1]} mol/m²/day)" if dli_range else ""
            lines.append(f"Estimated DLI: {computed['dli_estimate']} mol/m²/day{range_str}")

        self._append_common_sections(lines, computed, feeding, strain)
        return "\n".join(lines)

    def _format_outdoor_snapshot(
        self,
        grow: Dict,
        logs: List[Dict],
        feeding: List[Dict],
        strain: Optional[Dict],
        computed: Dict,
        stale: Dict,
        day_number: int,
        targets: Dict,
    ) -> str:
        """Format outdoor (Grow B) snapshot for system prompt."""
        lines = ["[GROW B — OUTDOOR CONTAINERS]"]

        lines.append(f"Strain: {grow['strain']}")
        if grow.get("genetics"):
            lines.append(f"Genetics: {grow['genetics']}")
        lines.append(f"Stage: {grow['current_stage']} (day {day_number})")
        lines.append(f"Medium: {grow.get('medium', 'living soil')} | Fertility: biology-first")
        location = grow.get("location") or "Vancouver, BC"
        lines.append(f"Location: {location}")
        if grow.get("pot_size"):
            lines.append(f"Container: {grow['pot_size']} {grow.get('pot_type', 'fabric')}")
        if grow.get("plant_count", 1) >= 1:
            lines.append(f"Plants: {grow.get('plant_count', 4)}")

        # Outdoor DLI estimate
        outdoor_dli = estimate_outdoor_dli_vancouver(date.today().month)
        lines.append(f"Estimated outdoor DLI: ~{outdoor_dli} mol/m²/day (Vancouver {date.today().strftime('%B')} average)")

        # Latest readings
        if logs:
            latest = logs[0]
            lines.append("")
            lines.append(f"Last checkin: {latest['logged_at'].strftime('%b %d %H:%M') if hasattr(latest['logged_at'], 'strftime') else latest['logged_at']}")
            if latest.get("humidity_ambient") is not None:
                lines.append(f"Ambient humidity: {latest['humidity_ambient']}%")
            if latest.get("temp_ambient_c") is not None:
                lines.append(f"Ambient temp: {latest['temp_ambient_c']}°C")
            if latest.get("temp_canopy_c") is not None:
                lines.append(f"Canopy temp: {latest['temp_canopy_c']}°C")
            if latest.get("soil_temp_c") is not None:
                lines.append(f"Soil temp: {latest['soil_temp_c']}°C")
            if latest.get("soil_moisture"):
                lines.append(f"Soil moisture: {latest['soil_moisture']}")
            if latest.get("brix") is not None:
                lines.append(f"Brix: {latest['brix']}")
            if latest.get("pest_notes"):
                lines.append(f"Pest notes: {latest['pest_notes']}")
            if latest.get("biology_observations"):
                lines.append(f"Biology: {latest['biology_observations']}")
            if latest.get("weather_notes"):
                lines.append(f"Weather: {latest['weather_notes']}")
            if latest.get("plant_observations"):
                lines.append(f"Observations: {latest['plant_observations']}")

        # VPD advisory (outdoor — not controlled)
        if computed.get("vpd_kpa"):
            lines.append(f"VPD (advisory): {computed['vpd_kpa']} kPa")

        self._append_common_sections(lines, computed, feeding, strain)
        return "\n".join(lines)

    def _append_common_sections(
        self,
        lines: List[str],
        computed: Dict,
        feeding: List[Dict],
        strain: Optional[Dict],
    ) -> None:
        """Append trends, flags, feeding schedule, and strain notes to snapshot lines."""
        # Trends
        trends = computed.get("trends", {})
        if any(v and v != "stable" for v in trends.values()):
            trend_parts = [f"{k}: {v}" for k, v in trends.items() if v and v != "stable"]
            lines.append(f"Trends: {', '.join(trend_parts)}")

        # Flags
        flags = computed.get("flags", [])
        if flags:
            lines.append(f"⚠ Flags: {', '.join(flags)}")

        # Feeding schedule
        if feeding:
            lines.append("")
            lines.append("Current feeding schedule:")
            for f in feeding:
                method = f.get("method", "water only")
                interval = f.get("interval_days")
                interval_str = f" every {interval} days" if interval else ""
                lines.append(f"  - {method}{interval_str}")
                if f.get("recipe"):
                    lines.append(f"    Recipe: {f['recipe']}")

        # Strain notes
        if strain:
            lines.append("")
            lines.append(f"Strain profile ({strain.get('confidence', 'medium')} confidence):")
            if strain.get("typical_flower_days"):
                lines.append(f"  Typical flower: {strain['typical_flower_days']} days")
            if strain.get("known_sensitivities"):
                lines.append(f"  Sensitivities: {strain['known_sensitivities']}")
            if strain.get("grow_tips"):
                lines.append(f"  Tips: {strain['grow_tips']}")

    def get_flags_text(self, grow_id: Optional[int] = None) -> str:
        """Get just the active flags as a text string for prompt injection."""
        snapshot = self.get_grow_snapshot(grow_id)
        flags = snapshot.get("computed", {}).get("flags", [])
        if not flags:
            return ""
        return "Active concerns: " + ", ".join(flags)

    def get_all_grows_snapshots(self) -> Dict[str, str]:
        """
        Build snapshots for all active grows.

        Returns a dict with:
          - 'indoor': snapshot text for the indoor grow (empty if none)
          - 'outdoor': snapshot text for the outdoor grow (empty if none)
          - 'all_flags': combined flags text from all grows
        """
        result = {"indoor": "", "outdoor": "", "all_flags": ""}
        flag_parts = []

        for grow in self.db.get_active_grows():
            grow_id = grow["id"]
            grow_type = grow.get("grow_type", "indoor")
            try:
                snapshot = self.get_grow_snapshot(grow_id=grow_id)
                snap_text = snapshot.get("snapshot_text", "")
                flags = snapshot.get("computed", {}).get("flags", [])

                if grow_type == "outdoor":
                    result["outdoor"] = snap_text
                else:
                    result["indoor"] = snap_text

                if flags:
                    label = "Grow A (indoor)" if grow_type == "indoor" else "Grow B (outdoor)"
                    flag_parts.append(f"{label}: {', '.join(flags)}")
            except Exception as exc:
                logger.warning("Snapshot failed for grow %d (%s): %s", grow_id, grow_type, exc)

        result["all_flags"] = "\n".join(flag_parts)
        return result
