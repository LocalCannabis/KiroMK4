#!/usr/bin/env python3
"""
jack/migrations/004_sensor_rollups.py — Hourly and daily rollup tables + retention.

Schema additions:
  - sensor_readings_hourly: Aggregated sensor stats per grow per hour.
    Populated by the rollup routine. Kept indefinitely.
  - sensor_readings_daily: Aggregated sensor stats per grow per day.
    Populated by the rollup routine. Kept indefinitely.

These tables let Jack answer trend questions ("how has VPD tracked this week?")
without querying millions of raw rows, and allow raw data pruning after the
configured retention window (default: 7 days).
"""

SQL = """
-- =============================================================================
-- Migration 004: Sensor Rollups + Retention
-- =============================================================================

-- Hourly aggregated sensor stats. One row per grow per hour.
-- Populated by the rollup routine, never by the ESP32 directly.
CREATE TABLE IF NOT EXISTS sensor_readings_hourly (
    id              BIGSERIAL PRIMARY KEY,
    grow_id         INTEGER REFERENCES grows(id) ON DELETE CASCADE,
    bucket_hour     TIMESTAMPTZ NOT NULL,       -- truncated to the hour

    reading_count   INTEGER NOT NULL DEFAULT 0,

    temp_min        DECIMAL(5,2),
    temp_max        DECIMAL(5,2),
    temp_avg        DECIMAL(5,2),

    humidity_min    DECIMAL(5,2),
    humidity_max    DECIMAL(5,2),
    humidity_avg    DECIMAL(5,2),

    vpd_min         DECIMAL(4,2),
    vpd_max         DECIMAL(4,2),
    vpd_avg         DECIMAL(4,2),

    ppfd_min        DECIMAL(7,2),
    ppfd_max        DECIMAL(7,2),
    ppfd_avg        DECIMAL(7,2),

    soil1_min       INTEGER,
    soil1_max       INTEGER,
    soil1_avg       DECIMAL(5,1),

    soil2_min       INTEGER,
    soil2_max       INTEGER,
    soil2_avg       DECIMAL(5,1),

    relay1_on_pct   DECIMAL(5,1),               -- % of readings with relay1 on
    relay2_on_pct   DECIMAL(5,1),

    UNIQUE (grow_id, bucket_hour)
);

CREATE INDEX IF NOT EXISTS idx_sensor_hourly_grow_time
    ON sensor_readings_hourly (grow_id, bucket_hour DESC);

COMMENT ON TABLE sensor_readings_hourly IS
    'Hourly rollups of raw sensor_readings. Kept indefinitely for trend analysis.';


-- Daily aggregated sensor stats. One row per grow per day.
CREATE TABLE IF NOT EXISTS sensor_readings_daily (
    id              BIGSERIAL PRIMARY KEY,
    grow_id         INTEGER REFERENCES grows(id) ON DELETE CASCADE,
    bucket_date     DATE NOT NULL,

    reading_count   INTEGER NOT NULL DEFAULT 0,

    temp_min        DECIMAL(5,2),
    temp_max        DECIMAL(5,2),
    temp_avg        DECIMAL(5,2),

    humidity_min    DECIMAL(5,2),
    humidity_max    DECIMAL(5,2),
    humidity_avg    DECIMAL(5,2),

    vpd_min         DECIMAL(4,2),
    vpd_max         DECIMAL(4,2),
    vpd_avg         DECIMAL(4,2),

    ppfd_min        DECIMAL(7,2),
    ppfd_max        DECIMAL(7,2),
    ppfd_avg        DECIMAL(7,2),

    soil1_avg       DECIMAL(5,1),
    soil2_avg       DECIMAL(5,1),

    -- Light hours: how many hours had PPFD > 10 (lights on)
    light_hours     DECIMAL(4,1),
    -- DLI approximation: sum of hourly avg PPFD * 3600 / 1e6
    dli_mol         DECIMAL(5,2),

    relay1_on_pct   DECIMAL(5,1),
    relay2_on_pct   DECIMAL(5,1),

    UNIQUE (grow_id, bucket_date)
);

CREATE INDEX IF NOT EXISTS idx_sensor_daily_grow_date
    ON sensor_readings_daily (grow_id, bucket_date DESC);

COMMENT ON TABLE sensor_readings_daily IS
    'Daily rollups of sensor data. Includes DLI estimate and light-hours. Kept indefinitely.';
""".strip()

if __name__ == "__main__":
    print(SQL)
