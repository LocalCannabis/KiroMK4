#!/usr/bin/env python3
"""
ambient/migrations/001_ambient_schema.py — PostgreSQL schema for the Ambient Intelligence Layer.

Creates: kiro_events, kiro_insights, kiro_briefings, kiro_ambient_config, kiro_ambient_log
Seeds: default ambient config values

Run via the ambient migration runner:
    python -m ambient.migrate
"""

SQL = """
-- =============================================================================
-- Kiro Ambient Intelligence Layer — PostgreSQL Schema
-- =============================================================================
-- Requires: PostgreSQL 14+
-- Database: kiro (shared with Jack's grow state)
-- =============================================================================

-- =============================================================================
-- 1. Core Event Store
-- =============================================================================

CREATE TABLE IF NOT EXISTS kiro_events (
    id              SERIAL PRIMARY KEY,
    source          VARCHAR(30) NOT NULL,
    source_id       VARCHAR(200),
    event_type      VARCHAR(50),
    occurred_at     TIMESTAMP NOT NULL,
    ingested_at     TIMESTAMP DEFAULT NOW(),
    metadata        JSONB NOT NULL DEFAULT '{}',
    raw_content     TEXT,
    content_purged  BOOLEAN DEFAULT FALSE,
    processed       BOOLEAN DEFAULT FALSE,
    processed_at    TIMESTAMP,
    tags            TEXT[],
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_kiro_events_source
    ON kiro_events(source, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_kiro_events_unprocessed
    ON kiro_events(processed) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_kiro_events_tags
    ON kiro_events USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_kiro_events_metadata
    ON kiro_events USING GIN(metadata);

-- =============================================================================
-- 2. Insights Store
-- =============================================================================

CREATE TABLE IF NOT EXISTS kiro_insights (
    id                  SERIAL PRIMARY KEY,
    insight_type        VARCHAR(30) NOT NULL,
    persona             VARCHAR(20),
    summary             TEXT NOT NULL,
    detail              TEXT,
    confidence          VARCHAR(20) NOT NULL DEFAULT 'medium',
    priority            INTEGER NOT NULL DEFAULT 5,
    source_event_ids    INTEGER[],
    related_insight_ids INTEGER[],
    tags                TEXT[],
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMP DEFAULT NOW(),
    expires_at          TIMESTAMP,
    surfaced            BOOLEAN DEFAULT FALSE,
    surfaced_at         TIMESTAMP,
    surfaced_in         INTEGER,
    dismissed           BOOLEAN DEFAULT FALSE,
    acted_on            BOOLEAN DEFAULT FALSE,
    superseded_by       INTEGER REFERENCES kiro_insights(id),
    evolved_from        INTEGER REFERENCES kiro_insights(id)
);

CREATE INDEX IF NOT EXISTS idx_kiro_insights_unsurfaced
    ON kiro_insights(surfaced, priority)
    WHERE surfaced = FALSE AND dismissed = FALSE;
CREATE INDEX IF NOT EXISTS idx_kiro_insights_persona
    ON kiro_insights(persona, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kiro_insights_type
    ON kiro_insights(insight_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kiro_insights_tags
    ON kiro_insights USING GIN(tags);

-- =============================================================================
-- 3. Briefings Store
-- =============================================================================

CREATE TABLE IF NOT EXISTS kiro_briefings (
    id                  SERIAL PRIMARY KEY,
    briefing_type       VARCHAR(20) NOT NULL,
    insight_ids         INTEGER[] NOT NULL,
    briefing_text       TEXT NOT NULL,
    persona_segments    JSONB,
    delivered_at        TIMESTAMP DEFAULT NOW(),
    delivery_method     VARCHAR(20) DEFAULT 'voice',
    feedback            VARCHAR(20),
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_kiro_briefings_type
    ON kiro_briefings(briefing_type, delivered_at DESC);

-- =============================================================================
-- 4. Learning Configuration
-- =============================================================================

CREATE TABLE IF NOT EXISTS kiro_ambient_config (
    id              SERIAL PRIMARY KEY,
    config_key      VARCHAR(100) UNIQUE NOT NULL,
    config_value    JSONB NOT NULL,
    description     TEXT,
    updated_at      TIMESTAMP DEFAULT NOW()
);

INSERT INTO kiro_ambient_config (config_key, config_value, description) VALUES
    ('briefing_schedule',
     '{"morning": "07:00", "commute": "trigger:location", "evening": "21:00"}'::jsonb,
     'When briefings are assembled and delivered'),
    ('max_insights_per_briefing',
     '7'::jsonb,
     'Maximum number of insights in a single briefing. Less is more.'),
    ('priority_threshold',
     '6'::jsonb,
     'Only surface insights with priority <= this value. 1=critical, 10=trivial.'),
    ('pattern_detection_window_days',
     '7'::jsonb,
     'How many days of events to analyze for pattern recognition'),
    ('trend_alert_threshold_days',
     '3'::jsonb,
     'Number of consecutive days a metric must trend before flagging'),
    ('knowledge_research_enabled',
     'true'::jsonb,
     'Whether Kiro actively searches for new knowledge base content'),
    ('knowledge_research_interval_hours',
     '12'::jsonb,
     'How often Kiro searches for new knowledge'),
    ('content_purge_after_hours',
     '72'::jsonb,
     'Hours after processing before raw_content is purged from sensitive sources (gmail)'),
    ('stream_polling',
     '{"gcal": 900, "gmail": 600, "ynab": 1800, "feeds": 3600}'::jsonb,
     'Polling intervals in seconds per data stream'),
    ('model_routing',
     '{"tagger": "anthropic/claude-3.5-haiku", "patterns": "anthropic/claude-sonnet-4.5", "bridger": "anthropic/claude-sonnet-4.5", "knowledge": "anthropic/claude-sonnet-4.5", "briefing": "anthropic/claude-sonnet-4.5", "alert": "anthropic/claude-sonnet-4.5"}'::jsonb,
     'OpenRouter model IDs for each processing task. Cheapest model that can do the job.')
ON CONFLICT (config_key) DO NOTHING;

-- =============================================================================
-- 5. Ambient Worker Log (audit trail)
-- =============================================================================

CREATE TABLE IF NOT EXISTS kiro_ambient_log (
    id              SERIAL PRIMARY KEY,
    worker          VARCHAR(50) NOT NULL,
    level           VARCHAR(10) NOT NULL DEFAULT 'INFO',
    message         TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kiro_ambient_log_worker
    ON kiro_ambient_log(worker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kiro_ambient_log_level
    ON kiro_ambient_log(level, created_at DESC);
""".strip()

if __name__ == "__main__":
    print(SQL)
