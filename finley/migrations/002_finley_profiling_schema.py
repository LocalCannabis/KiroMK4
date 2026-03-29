"""
002_finley_profiling_schema.py — Financial profiling, wellbeing, knowledge, engagement tables.

These power the profiler, CFPB scoring, RAG knowledge base, and
proactive engagement engine.
"""

SQL = """
-- =========================================================================
-- Financial Profile — vital signs + behavioral patterns + stage
-- =========================================================================

CREATE TABLE IF NOT EXISTS finley_profile (
    id              SERIAL PRIMARY KEY,
    profile_date    DATE NOT NULL DEFAULT CURRENT_DATE,

    -- A. Vital signs (objective, from YNAB data)
    vital_signs     JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Expected keys:
    --   monthly_income, monthly_fixed_expenses, monthly_variable_expenses,
    --   cash_flow_delta, days_until_zero, expense_volatility,
    --   largest_unplanned_expense, subscription_load

    -- B. Behavioral patterns (inferred)
    behavioral      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Expected keys:
    --   impulse_frequency, payday_spike_pct, avoidance_days,
    --   category_discipline_pct, recurring_surprise_count, adhd_tax_total

    -- C. Composite stage assessment
    stage           TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (stage IN ('distressed', 'fragile', 'stabilizing', 'grounding', 'unknown')),
    stage_detail    TEXT,

    -- D. Account snapshot at time of profile
    account_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,

    created_at      TIMESTAMP DEFAULT NOW(),

    UNIQUE (profile_date)
);

CREATE INDEX IF NOT EXISTS idx_fp_date  ON finley_profile(profile_date);
CREATE INDEX IF NOT EXISTS idx_fp_stage ON finley_profile(stage);

-- =========================================================================
-- CFPB Financial Well-Being Assessments
-- =========================================================================

CREATE TABLE IF NOT EXISTS finley_wellbeing (
    id              SERIAL PRIMARY KEY,
    assessed_at     TIMESTAMP DEFAULT NOW(),

    -- Raw responses (1-5 per item, 5 items)
    responses       JSONB NOT NULL,
    -- Expected: {"q1": 3, "q2": 2, "q3": 4, "q4": 1, "q5": 3}

    raw_score       INTEGER NOT NULL,       -- Sum of coded responses (5-25 range)
    scaled_score    INTEGER NOT NULL,        -- 0-100 CFPB IRT-scaled score
    context_notes   TEXT,                    -- Any context from the conversation

    -- Which question set was used
    scale_version   TEXT DEFAULT 'abbreviated_5item'
);

CREATE INDEX IF NOT EXISTS idx_fw_date ON finley_wellbeing(assessed_at);

-- =========================================================================
-- Finley Knowledge Base (RAG/HRR layer)
-- =========================================================================

-- Ensure pgvector extension (already created by Jack's migration, but idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS finley_knowledge (
    id                  SERIAL PRIMARY KEY,
    source_name         VARCHAR(255) NOT NULL,
    source_type         VARCHAR(50) NOT NULL
                        CHECK (source_type IN ('framework', 'book', 'article', 'government', 'strategy')),
    topic               VARCHAR(100) NOT NULL,
    content             TEXT NOT NULL,
    income_relevance    VARCHAR(10) DEFAULT 'all'
                        CHECK (income_relevance IN ('low', 'mid', 'high', 'all')),
    adhd_relevant       BOOLEAN DEFAULT FALSE,
    canada_specific     BOOLEAN DEFAULT FALSE,
    actionability       VARCHAR(20) DEFAULT 'principle'
                        CHECK (actionability IN ('principle', 'framework', 'strategy', 'tactic', 'reference')),
    tier                INTEGER DEFAULT 3 CHECK (tier BETWEEN 1 AND 4),
    embedding           vector(1536),           -- OpenAI text-embedding-3-small
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fk_topic     ON finley_knowledge(topic);
CREATE INDEX IF NOT EXISTS idx_fk_tier      ON finley_knowledge(tier);
CREATE INDEX IF NOT EXISTS idx_fk_relevance ON finley_knowledge(income_relevance, adhd_relevant);
-- HNSW index for vector similarity search
CREATE INDEX IF NOT EXISTS idx_fk_embedding ON finley_knowledge
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- =========================================================================
-- Proactive Engagement Log
-- =========================================================================

CREATE TABLE IF NOT EXISTS finley_engagements (
    id                  SERIAL PRIMARY KEY,
    trigger_type        VARCHAR(50) NOT NULL,
    trigger_detail      JSONB,
    message_text        TEXT NOT NULL,
    delivery_channel    VARCHAR(20) DEFAULT 'voice'
                        CHECK (delivery_channel IN ('voice', 'dashboard', 'log_only')),
    delivered_at        TIMESTAMP DEFAULT NOW(),
    acknowledged        BOOLEAN DEFAULT FALSE,
    acknowledged_at     TIMESTAMP,
    response_summary    TEXT,
    follow_up_needed    BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_fe_trigger   ON finley_engagements(trigger_type);
CREATE INDEX IF NOT EXISTS idx_fe_delivered ON finley_engagements(delivered_at);
CREATE INDEX IF NOT EXISTS idx_fe_ack      ON finley_engagements(acknowledged);

-- =========================================================================
-- Finley config (config-over-code, matching kiro_ambient_config pattern)
-- =========================================================================

CREATE TABLE IF NOT EXISTS finley_config (
    config_key      VARCHAR(100) PRIMARY KEY,
    config_value    JSONB NOT NULL,
    description     TEXT,
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- Seed defaults
INSERT INTO finley_config (config_key, config_value, description) VALUES
    ('sync_interval_minutes',   '30',           'YNAB sync interval'),
    ('large_txn_threshold',     '100000',       'Milliunits threshold for large transaction alerts ($100)'),
    ('payday_spike_threshold',  '0.40',         'Fraction of income spent within 48h to trigger alert'),
    ('impulse_window_hours',    '2',            'Hours between transactions to flag as impulse cluster'),
    ('avoidance_gap_days',      '5',            'Days of no categorization to flag avoidance'),
    ('adhd_tax_keywords',       '["late fee", "nsf", "overdraft", "interest charge", "duplicate"]', 'Payee/memo keywords for ADHD tax detection'),
    ('anti_nag_max_daily',      '2',            'Max proactive messages per day'),
    ('anti_nag_min_gap_hours',  '4',            'Min hours between proactive messages'),
    ('anti_nag_same_trigger_cooldown_hours', '72', 'Hours before re-raising same trigger type'),
    ('anti_nag_declined_cooldown_days',      '7',  'Days before re-raising a declined suggestion'),
    ('weekly_pulse_day',        '"sunday"',     'Day of week for weekly pulse briefing'),
    ('weekly_pulse_hour',       '18',           'Hour (24h) for weekly pulse'),
    ('cfpb_interval_days',      '30',           'Days between CFPB wellbeing assessments'),
    ('monthly_income_estimate', '0',            'User-confirmed monthly income (milliunits). 0 = auto-detect from transactions.')
ON CONFLICT (config_key) DO NOTHING;
"""
