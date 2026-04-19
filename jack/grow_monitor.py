"""
jack/grow_monitor.py — Background sensor-monitoring daemon for the grow tent.

Watches sensor_readings and compares them against grow_thresholds.
When a reading goes out of range, it logs the flag and can fire a
one-liner alert that Jack picks up in the next interaction.

Design goals:
  - Self-contained daemon thread (same pattern as Finley SyncDaemon)
  - Cooldown per flag type to avoid alert fatigue
  - Automatic relay control when thresholds are configured (e.g. humidifier)
  - Ships as jack.grow_monitor.GrowMonitorDaemon, started from ui/app.py
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kiro.jack.grow_monitor")


class GrowMonitorDaemon:
    """
    Polls sensor_readings every `check_interval_s` seconds and
    fires alerts when values breach grow_thresholds.

    Usage:
        daemon = GrowMonitorDaemon(check_interval_s=30)
        daemon.start()
        ...
        daemon.stop()
    """

    def __init__(
        self,
        check_interval_s: int = 30,
        auto_relay: bool = True,
    ):
        self._interval = check_interval_s
        self._auto_relay = auto_relay
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Cooldown tracking: { "grow_id:flag_name": last_alert_utc }
        self._cooldowns: Dict[str, datetime] = {}

        # Recent alerts for API/dashboard consumption
        self._active_flags: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

        # Rollup tracking — run once per hour, not every check cycle
        self._last_rollup: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("Grow monitor daemon already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="jack-grow-monitor", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def active_flags(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._active_flags)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _loop(self):
        logger.info(
            "Grow monitor daemon started (check every %ds, auto_relay=%s)",
            self._interval, self._auto_relay,
        )

        while not self._stop_event.is_set():
            try:
                self._check_all_grows()
            except Exception as exc:
                logger.error("Monitor check failed: %s", exc, exc_info=True)

            # Sleep in small increments for responsive shutdown
            slept = 0
            while slept < self._interval and not self._stop_event.is_set():
                time.sleep(min(5, self._interval - slept))
                slept += 5

        logger.info("Grow monitor daemon stopped.")

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------
    def _check_all_grows(self):
        from .db import JackDB
        from .config import load_jack_config

        cfg = load_jack_config()
        db = JackDB(cfg)
        try:
            # Get all active grows
            grows = [g for g in db.list_grows(limit=20) if g.get("status") == "active"]
            if not grows:
                return

            new_flags: List[Dict[str, Any]] = []

            for grow in grows:
                grow_id = grow["id"]
                latest = db.get_latest_sensor_reading(grow_id)
                if not latest:
                    continue

                # Check data freshness — skip if reading is old (>5 min)
                if latest.get("recorded_at"):
                    recorded = latest["recorded_at"]
                    if isinstance(recorded, str):
                        recorded = datetime.fromisoformat(recorded)
                    if recorded.tzinfo is None:
                        recorded = recorded.replace(tzinfo=timezone.utc)
                    age_s = (datetime.now(timezone.utc) - recorded).total_seconds()
                    if age_s > 300:
                        continue  # Stale data, ESP32 probably offline

                thresholds = db.get_thresholds(grow_id)
                if not thresholds:
                    continue

                cooldown_min = int(thresholds.get("alert_cooldown_min", 15))
                flags = self._evaluate_thresholds(latest, thresholds)

                for flag in flags:
                    flag_key = f"{grow_id}:{flag['name']}"
                    if self._is_cooling_down(flag_key, cooldown_min):
                        continue

                    # Record the alert
                    self._cooldowns[flag_key] = datetime.now(timezone.utc)
                    flag["grow_id"] = grow_id
                    flag["strain"] = grow.get("strain", "Unknown")
                    flag["recorded_at"] = datetime.now(timezone.utc).isoformat()
                    new_flags.append(flag)

                    logger.warning(
                        "🌱 GROW ALERT [%s] %s: %s (value=%.2f, range=%.2f–%.2f)",
                        grow.get("strain", f"grow#{grow_id}"),
                        flag["name"],
                        flag["message"],
                        flag["value"],
                        flag["min"],
                        flag["max"],
                    )

                # Auto-relay: humidifier control based on humidity threshold
                if self._auto_relay and thresholds.get("humidifier_on_below"):
                    self._auto_humidifier(
                        db, grow_id, latest, thresholds
                    )

            with self._lock:
                self._active_flags = new_flags

            # --- Sensor rollup / prune (runs once per hour) ---
            retention = cfg.get("sensor_retention", {})
            interval_min = int(retention.get("rollup_interval_minutes", 60))
            now = datetime.now(timezone.utc)
            if (
                self._last_rollup is None
                or (now - self._last_rollup).total_seconds() >= interval_min * 60
            ):
                try:
                    for grow in grows:
                        gid = grow["id"]
                        db.rollup_hourly(
                            gid,
                            hours_back=int(retention.get("hourly_lookback_hours", 2)),
                        )
                        db.rollup_daily(
                            gid,
                            days_back=int(retention.get("daily_lookback_days", 2)),
                        )
                        db.prune_raw_readings(
                            gid,
                            retain_days=int(retention.get("raw_retain_days", 7)),
                        )
                    self._last_rollup = now
                    logger.info("Sensor rollup/prune completed for %d grow(s).", len(grows))
                except Exception as exc:
                    logger.error("Sensor rollup failed: %s", exc, exc_info=True)

        finally:
            db.close()

    # ------------------------------------------------------------------
    # Threshold evaluation
    # ------------------------------------------------------------------
    def _evaluate_thresholds(
        self, reading: Dict, thresholds: Dict
    ) -> List[Dict[str, Any]]:
        """Compare a sensor reading against thresholds, return list of flags."""
        flags = []

        checks = [
            ("temp_c",         "temp_min_c",        "temp_max_c",        "Temperature"),
            ("humidity_pct",   "humidity_min_pct",   "humidity_max_pct",  "Humidity"),
            ("vpd_kpa",        "vpd_min_kpa",        "vpd_max_kpa",      "VPD"),
            ("ppfd",           "ppfd_min",            "ppfd_max",         "PPFD"),
            ("soil_moisture_1","soil_moisture_min",   "soil_moisture_max", "Soil moisture (probe 1)"),
            ("soil_moisture_2","soil_moisture_min",   "soil_moisture_max", "Soil moisture (probe 2)"),
        ]

        for reading_key, min_key, max_key, label in checks:
            val = reading.get(reading_key)
            if val is None:
                continue
            val = float(val)
            lo = float(thresholds.get(min_key, 0))
            hi = float(thresholds.get(max_key, 9999))

            if val < lo:
                flags.append({
                    "name": f"{reading_key}_low",
                    "message": f"{label} too low ({val:.1f} < {lo:.1f})",
                    "value": val,
                    "min": lo,
                    "max": hi,
                    "severity": "warning",
                })
            elif val > hi:
                flags.append({
                    "name": f"{reading_key}_high",
                    "message": f"{label} too high ({val:.1f} > {hi:.1f})",
                    "value": val,
                    "min": lo,
                    "max": hi,
                    "severity": "warning",
                })

        return flags

    # ------------------------------------------------------------------
    # Cooldown check
    # ------------------------------------------------------------------
    def _is_cooling_down(self, flag_key: str, cooldown_min: int) -> bool:
        last = self._cooldowns.get(flag_key)
        if last is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < (cooldown_min * 60)

    # ------------------------------------------------------------------
    # Automatic humidifier control
    # ------------------------------------------------------------------
    def _auto_humidifier(
        self,
        db,
        grow_id: int,
        reading: Dict,
        thresholds: Dict,
    ):
        """
        Turn humidifier on/off based on the humidifier_on_below threshold.
        Relay 1 = humidifier (matches the ESP32 wiring).
        """
        humidity = reading.get("humidity_pct")
        if humidity is None:
            return

        humidity = float(humidity)
        trigger = float(thresholds["humidifier_on_below"])
        relay_currently_on = bool(reading.get("relay_1_on"))

        if humidity < trigger and not relay_currently_on:
            # Need to turn humidifier ON
            try:
                from .grow_api import _relay_command_queue
                _relay_command_queue.append("ON1")
                db.insert_relay_event(
                    grow_id, relay_id=1, action="on",
                    source="auto-monitor",
                    reason=f"Humidity {humidity:.1f}% < threshold {trigger:.1f}%",
                )
                logger.info(
                    "🌫 Auto-humidifier ON for grow %d (%.1f%% < %.1f%%)",
                    grow_id, humidity, trigger,
                )
            except Exception as exc:
                logger.error("Failed to auto-enable humidifier: %s", exc)

        elif humidity >= trigger + 5 and relay_currently_on:
            # 5% hysteresis before turning off to avoid rapid cycling
            try:
                from .grow_api import _relay_command_queue
                _relay_command_queue.append("OFF1")
                db.insert_relay_event(
                    grow_id, relay_id=1, action="off",
                    source="auto-monitor",
                    reason=f"Humidity {humidity:.1f}% >= threshold {trigger:.1f}% + 5% hysteresis",
                )
                logger.info(
                    "🌫 Auto-humidifier OFF for grow %d (%.1f%% >= %.1f%%)",
                    grow_id, humidity, trigger + 5,
                )
            except Exception as exc:
                logger.error("Failed to auto-disable humidifier: %s", exc)
