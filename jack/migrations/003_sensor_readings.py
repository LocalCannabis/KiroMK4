#!/usr/bin/env python3
"""
jack/migrations/003_sensor_readings.py — ESP32 sensor integration tables.

Schema additions:
  - sensor_readings: Time-series table for ESP32 environmental readings
    (temp, humidity, PPFD, soil moisture ×2, relay states). Ingested via
    POST /api/grow/readings from the ESP32 over WiFi.
  - relay_events: Audit log of every relay state change (manual or auto).
  - grow_thresholds: Per-grow configurable alert thresholds that Jack and
    the auto-monitoring routine use to flag out-of-range conditions.

These tables are purpose-built for high-frequency IoT data (every 2-10s)
and sit alongside the existing grow_log_entries (human-reported checkins).
"""

SQL = """
-- =============================================================================
-- Migration 003: ESP32 Sensor Integration
-- =============================================================================

-- Time-series sensor readings from the ESP32 environmental monitor.
-- Ingested at ~2-10 second intervals via POST /api/grow/readings.
-- Designed for fast writes and time-range queries.
CREATE TABLE IF NOT EXISTS sensor_readings (
    id              BIGSERIAL PRIMARY KEY,
    grow_id         INTEGER REFERENCES grows(id) ON DELETE CASCADE,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Environmental readings (from ESP32 sensors)
    temp_c          DECIMAL(5,2),         -- DHT20: air temperature °C
    humidity_pct    DECIMAL(5,2),         -- DHT20: relative humidity %
    ppfd            DECIMAL(7,2),         -- BH1750: µmol/m²/s (converted from lux)
    soil_moisture_1 INTEGER,              -- HW-390 sensor 1: 0-100%
    soil_moisture_2 INTEGER,              -- HW-390 sensor 2: 0-100%

    -- Relay states at time of reading
    relay_1_on      BOOLEAN DEFAULT FALSE,  -- SSR1 (humidifier)
    relay_2_on      BOOLEAN DEFAULT FALSE,  -- SSR2 (reserved/AC)

    -- Computed at ingest time
    vpd_kpa         DECIMAL(4,2),         -- VPD computed from temp + humidity

    -- Source metadata
    source          VARCHAR(20) DEFAULT 'esp32'  -- 'esp32', 'manual', 'serial'
);

-- Primary query pattern: latest readings + time-range queries for a grow
CREATE INDEX IF NOT EXISTS idx_sensor_readings_grow_time
    ON sensor_readings (grow_id, recorded_at DESC);

-- For dashboard: fast latest-reading lookup
CREATE INDEX IF NOT EXISTS idx_sensor_readings_latest
    ON sensor_readings (recorded_at DESC);

COMMENT ON TABLE sensor_readings IS 'High-frequency ESP32 sensor readings. ~2s interval. Distinct from human-reported grow_log_entries.';


-- Relay state change audit log.
-- Every ON/OFF transition is recorded with source attribution.
CREATE TABLE IF NOT EXISTS relay_events (
    id              BIGSERIAL PRIMARY KEY,
    grow_id         INTEGER REFERENCES grows(id) ON DELETE SET NULL,
    relay_id        INTEGER NOT NULL,           -- 1 = humidifier, 2 = reserved/AC
    action          VARCHAR(10) NOT NULL,       -- 'on' or 'off'
    source          VARCHAR(20) NOT NULL,       -- 'auto', 'manual', 'api', 'jack'
    reason          TEXT,                       -- Why the relay was toggled
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_relay_events_grow_time
    ON relay_events (grow_id, recorded_at DESC);

COMMENT ON TABLE relay_events IS 'Audit log for every relay state change — who/what toggled it and why.';


-- Per-grow alert thresholds. Jack and the auto-monitoring routine
-- compare sensor_readings against these to generate flags and alerts.
-- One row per grow. Upserted via API or Jack tools.
CREATE TABLE IF NOT EXISTS grow_thresholds (
    id              SERIAL PRIMARY KEY,
    grow_id         INTEGER REFERENCES grows(id) ON DELETE CASCADE UNIQUE,

    -- Temperature range (°C)
    temp_min_c      DECIMAL(4,1) DEFAULT 20.0,
    temp_max_c      DECIMAL(4,1) DEFAULT 28.0,

    -- Humidity range (%)
    humidity_min_pct DECIMAL(4,1) DEFAULT 50.0,
    humidity_max_pct DECIMAL(4,1) DEFAULT 70.0,

    -- VPD range (kPa)
    vpd_min_kpa     DECIMAL(4,2) DEFAULT 0.8,
    vpd_max_kpa     DECIMAL(4,2) DEFAULT 1.2,

    -- PPFD range (µmol/m²/s)
    ppfd_min        DECIMAL(6,1) DEFAULT 400.0,
    ppfd_max        DECIMAL(6,1) DEFAULT 800.0,

    -- Soil moisture range (%)
    soil_moisture_min INTEGER DEFAULT 40,
    soil_moisture_max INTEGER DEFAULT 70,

    -- Humidifier auto-control threshold (% RH — SSR1 turns on below this)
    humidifier_on_below DECIMAL(4,1) DEFAULT 55.0,

    -- Alert cooldown: don't re-alert within this many minutes
    alert_cooldown_min INTEGER DEFAULT 30,

    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE grow_thresholds IS 'Per-grow configurable thresholds for sensor alerts and auto-control.';
""".strip()

if __name__ == "__main__":
    print(SQL)
