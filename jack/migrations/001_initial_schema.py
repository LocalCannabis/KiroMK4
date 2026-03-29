#!/usr/bin/env python3
"""
jack/migrations/001_initial_schema.sql — PostgreSQL schema for Jack grow state.

Run against your PostgreSQL instance:
    psql -U kiro -d kiro -f jack/migrations/001_initial_schema.sql

Or via the migration runner:
    python -m jack.migrate
"""

SQL = """
-- =============================================================================
-- Jack Master Grower — PostgreSQL Schema
-- =============================================================================
-- Requires: PostgreSQL 14+ with pgvector extension
-- Database: kiro (or as configured in jack_config.yaml)
-- =============================================================================

-- Enable pgvector for knowledge embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- Tier 1: Live Grow State
-- =============================================================================

-- Active and historical grows
CREATE TABLE IF NOT EXISTS grows (
    id              SERIAL PRIMARY KEY,
    strain          VARCHAR(100) NOT NULL,
    genetics        VARCHAR(200),
    source          VARCHAR(100),
    medium          VARCHAR(100) NOT NULL DEFAULT 'living soil',
    pot_size        VARCHAR(20),
    pot_type        VARCHAR(50),
    plant_count     INTEGER DEFAULT 1,
    start_date      DATE NOT NULL,
    seed_or_clone   VARCHAR(10) DEFAULT 'seed',
    current_stage   VARCHAR(20) NOT NULL DEFAULT 'seedling',
    stage_changed   DATE,
    light_schedule  VARCHAR(10) DEFAULT '18/6',
    target_harvest  DATE,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE grows IS 'Active and historical grows tracked by Jack.';
COMMENT ON COLUMN grows.current_stage IS 'One of: seedling, veg, transition, flower, flush, dry, cure, complete';
COMMENT ON COLUMN grows.seed_or_clone IS 'One of: seed, clone';

-- Tent and equipment configuration
CREATE TABLE IF NOT EXISTS tent_config (
    id              SERIAL PRIMARY KEY,
    tent_size       VARCHAR(20) NOT NULL DEFAULT '2x2',
    tent_height     VARCHAR(10) DEFAULT '4ft',
    light_model     VARCHAR(100),
    light_wattage   INTEGER,
    light_spectrum  VARCHAR(50),
    fan_model       VARCHAR(100),
    filter_model    VARCHAR(100),
    humidifier      VARCHAR(100),
    dehumidifier    VARCHAR(100),
    medium_details  TEXT,
    other_equipment TEXT,
    updated_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE tent_config IS 'Tent hardware and equipment configuration.';

-- Checkin log entries — the heart of Jack''s data
CREATE TABLE IF NOT EXISTS grow_log_entries (
    id                  SERIAL PRIMARY KEY,
    grow_id             INTEGER REFERENCES grows(id) ON DELETE CASCADE,
    logged_at           TIMESTAMP NOT NULL DEFAULT NOW(),
    day_number          INTEGER,

    -- Environmental readings
    humidity_tent       DECIMAL(4,1),
    humidity_ambient    DECIMAL(4,1),
    temp_canopy_c       DECIMAL(4,1),
    temp_pot_c          DECIMAL(4,1),
    temp_ambient_c      DECIMAL(4,1),
    light_distance_cm   INTEGER,
    light_schedule      VARCHAR(10),

    -- Computed by Jack
    vpd_kpa             DECIMAL(4,2),
    dli_estimate        DECIMAL(5,1),

    -- Soil & water
    soil_moisture       VARCHAR(20),
    last_watered        TIMESTAMP,
    last_feed           TIMESTAMP,
    feed_details        TEXT,
    water_ph            DECIMAL(3,1),
    water_volume_ml     INTEGER,

    -- Observations
    plant_observations  TEXT,
    jack_assessment     TEXT,
    jack_confidence     VARCHAR(20),
    flags               TEXT[],
    actions_recommended TEXT,

    -- Media (future)
    photo_paths         TEXT[],

    created_at          TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE grow_log_entries IS 'Every checkin reading Tim provides, plus Jack''s assessment.';
COMMENT ON COLUMN grow_log_entries.jack_confidence IS 'One of: high, medium, low, conflicting';

CREATE INDEX IF NOT EXISTS idx_grow_log_grow_id ON grow_log_entries(grow_id);
CREATE INDEX IF NOT EXISTS idx_grow_log_logged_at ON grow_log_entries(logged_at DESC);

-- Feeding schedule per grow stage
CREATE TABLE IF NOT EXISTS feeding_schedule (
    id              SERIAL PRIMARY KEY,
    grow_id         INTEGER REFERENCES grows(id) ON DELETE CASCADE,
    stage           VARCHAR(20) NOT NULL,
    interval_days   INTEGER,
    method          VARCHAR(50),
    recipe          TEXT,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE feeding_schedule IS 'Feeding protocol per growth stage.';

-- Strain reference profiles
CREATE TABLE IF NOT EXISTS strain_profiles (
    id                  SERIAL PRIMARY KEY,
    strain_name         VARCHAR(100) NOT NULL,
    breeder             VARCHAR(100),
    genetics            VARCHAR(200),
    typical_flower_days INTEGER,
    stretch_factor      VARCHAR(20),
    known_sensitivities TEXT,
    ideal_environment   TEXT,
    terpene_profile     TEXT,
    grow_tips           TEXT,
    source_references   TEXT[],
    confidence          VARCHAR(20) DEFAULT 'medium',
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE strain_profiles IS 'Curated strain knowledge — built over time from Jack''s experience.';

-- =============================================================================
-- Tier 2: Verified Knowledge Base (pgvector)
-- =============================================================================

-- Ingested reference sources
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    author          VARCHAR(100),
    source_type     VARCHAR(30) NOT NULL,
    domain_tags     TEXT[] NOT NULL,
    base_confidence VARCHAR(20) NOT NULL,
    url             TEXT,
    notes           TEXT,
    ingested        BOOLEAN DEFAULT FALSE,
    ingested_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE knowledge_sources IS 'Curated reference sources with confidence ratings.';
COMMENT ON COLUMN knowledge_sources.source_type IS 'One of: book, paper, lecture, podcast, community, reference_data';
COMMENT ON COLUMN knowledge_sources.base_confidence IS 'One of: very_high, high, medium_high, medium, low';

-- Chunked + embedded knowledge for retrieval
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id              SERIAL PRIMARY KEY,
    source_id       INTEGER REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    topic_tags      TEXT[] NOT NULL,
    chapter         VARCHAR(200),
    page_ref        VARCHAR(50),
    embedding       vector(1536),
    created_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE knowledge_chunks IS 'Embedded knowledge chunks for similarity retrieval. All carry source attribution.';

-- HNSW index for cosine similarity search
-- HNSW works well at any dataset size (unlike IVFFlat which requires lists*16 rows minimum).
-- m=16, ef_construction=64 are sensible defaults for a grow-log-scale knowledge base.
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tags ON knowledge_chunks USING gin(topic_tags);

-- =============================================================================
-- Seed: Initial knowledge sources (not yet ingested)
-- =============================================================================

INSERT INTO knowledge_sources (name, author, source_type, domain_tags, base_confidence, notes)
VALUES
    (
        'Cannabis Light Science — Dr. Bruce Bugbee',
        'Dr. Bruce Bugbee',
        'lecture',
        ARRAY['light', 'DLI', 'photosynthesis', 'spectrum', 'CO2'],
        'very_high',
        'Utah State University. "Cannabis: Watching the Grass Grow" lecture series, DLI target research, controlled environment agriculture papers. Highest-impact variable in a 2x2 tent.'
    ),
    (
        'Peat-Based Soil Growing — WP420 / ProMix',
        NULL,
        'reference_data',
        ARRAY['soil', 'peat', 'watering', 'ph', 'nutrients', 'wp420', 'promix'],
        'high',
        'Peat-based growing reference. WP420 (Canadian) and ProMix style media. Covers watering, pH management, feeding schedules, and common issues.'
    ),
    (
        'VPD & Environmental Reference Data',
        NULL,
        'reference_data',
        ARRAY['vpd', 'temperature', 'humidity', 'environment', 'drying', 'curing'],
        'high',
        'Compiled from Pulse grow room data, Dimlux VPD charts, cross-referenced with Bugbee. Powers checkin assessments.'
    ),
    (
        'Indo GrowHub 800C — Equipment Reference',
        'Indo Products Inc.',
        'reference_data',
        ARRAY['light', 'growhub', 'cob', 'fan', 'ppfd', 'distance', 'equipment'],
        'high',
        'Indo GrowHub 800C all-in-one unit specs: 4x 50W CREE COB (200W), full spectrum 3000K, 105 CFM exhaust fan, charcoal filter, digital timer, temp/humidity display. PPFD distance guide and fan speed recommendations.'
    )
ON CONFLICT DO NOTHING;
""".strip()

if __name__ == "__main__":
    print(SQL)
