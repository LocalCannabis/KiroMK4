"""
Coach core schema — Executive Function Support tables.

Design philosophy (from Gospel Spec + Seed Knowledge):
  - GTD-influenced but ADHD-adapted. No guilt infrastructure. No streaks.
  - Status is information, not judgment.
  - Externalize everything that lives in Tim's head (Barkley).
  - Projects require >1 action to complete (GTD).
  - Energy-aware task selection (Barkley EF fuel tank).
  - Voice dump capture → structured task pipeline.

Table prefix: coach_ (consistent with finley_ convention).
"""

SQL = """
-- ═══════════════════════════════════════════════════════════════════════
-- PROJECTS — Multi-step outcomes Tim is committed to
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS coach_projects (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'someday', 'completed', 'dropped')),
    priority        INTEGER DEFAULT 3
                    CHECK (priority BETWEEN 1 AND 5),
    area            VARCHAR(50) DEFAULT 'general',
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    completed_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_coach_projects_status
    ON coach_projects(status);

-- ═══════════════════════════════════════════════════════════════════════
-- TASKS — Next actions, waiting-for, someday/maybe
-- GTD-inspired statuses with ADHD energy awareness
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS coach_tasks (
    id              SERIAL PRIMARY KEY,
    project_id      INTEGER REFERENCES coach_projects(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'inbox'
                    CHECK (status IN (
                        'inbox',        -- Unclarified capture
                        'next',         -- Defined next action, ready to do
                        'waiting',      -- Delegated or blocked
                        'someday',      -- Not committed to now
                        'done',         -- Completed
                        'dropped'       -- Intentionally abandoned
                    )),
    priority        INTEGER DEFAULT 3
                    CHECK (priority BETWEEN 1 AND 5),
    energy_level    VARCHAR(10) DEFAULT 'medium'
                    CHECK (energy_level IN ('low', 'medium', 'high')),
    context         TEXT[] DEFAULT '{}',
    estimated_minutes INTEGER,
    due_date        DATE,
    waiting_on      TEXT,
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    completed_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_coach_tasks_status
    ON coach_tasks(status);
CREATE INDEX IF NOT EXISTS idx_coach_tasks_project
    ON coach_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_coach_tasks_due
    ON coach_tasks(due_date) WHERE due_date IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- CAPTURES — Raw voice dumps and text inputs before processing
-- "Your mind is for having ideas, not holding them." — David Allen
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS coach_captures (
    id              SERIAL PRIMARY KEY,
    raw_text        TEXT NOT NULL,
    source          VARCHAR(20) DEFAULT 'voice'
                    CHECK (source IN ('voice', 'text', 'ambient')),
    processed       BOOLEAN DEFAULT FALSE,
    extracted_tasks INTEGER[] DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coach_captures_unprocessed
    ON coach_captures(processed) WHERE processed = FALSE;

-- ═══════════════════════════════════════════════════════════════════════
-- DAILY PLANS — What Coach selected as today's focus
-- Tracks planned vs actual without creating shame infrastructure
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS coach_daily_plans (
    id              SERIAL PRIMARY KEY,
    plan_date       DATE NOT NULL UNIQUE,
    top_tasks       INTEGER[] DEFAULT '{}',
    review_notes    TEXT DEFAULT '',
    energy_start    VARCHAR(10),
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coach_daily_plans_date
    ON coach_daily_plans(plan_date);

-- ═══════════════════════════════════════════════════════════════════════
-- WEEKLY REVIEWS — The "keep the system alive" ritual (GTD)
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS coach_weekly_reviews (
    id              SERIAL PRIMARY KEY,
    review_date     DATE NOT NULL,
    inbox_cleared   BOOLEAN DEFAULT FALSE,
    projects_reviewed BOOLEAN DEFAULT FALSE,
    tasks_completed INTEGER DEFAULT 0,
    tasks_added     INTEGER DEFAULT 0,
    tasks_dropped   INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════
-- KNOWLEDGE — Seed knowledge + future RAG expansion
-- GTD methodology, Atomic Habits, Barkley EF/ADHD frameworks
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS coach_knowledge (
    id              SERIAL PRIMARY KEY,
    source_name     TEXT NOT NULL,
    source_type     VARCHAR(30) NOT NULL
                    CHECK (source_type IN (
                        'framework', 'research', 'strategy',
                        'government', 'practitioner', 'personal'
                    )),
    topic           TEXT NOT NULL,
    content         TEXT NOT NULL,
    tier            INTEGER DEFAULT 1 CHECK (tier BETWEEN 1 AND 4),
    adhd_relevant   BOOLEAN DEFAULT TRUE,
    actionability   VARCHAR(20) DEFAULT 'reference'
                    CHECK (actionability IN (
                        'framework', 'strategy', 'tactic',
                        'reference', 'principle'
                    )),
    embedding       vector(1536),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════
-- CONFIG — Config-over-code key/value store
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS coach_config (
    key             VARCHAR(100) PRIMARY KEY,
    value           JSONB NOT NULL,
    description     TEXT DEFAULT '',
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- Seed defaults
INSERT INTO coach_config (key, value, description) VALUES
    ('daily_top_n', '3', 'Number of top tasks in daily plan'),
    ('weekly_review_day', '"sunday"', 'Preferred day for weekly review'),
    ('drift_threshold_hours', '4', 'Hours on one task before flagging hyperfocus drift'),
    ('two_minute_threshold', '2', 'Minutes — tasks under this get "do it now" treatment'),
    ('energy_check_interval_hours', '3', 'How often to suggest an energy/break check'),
    ('capture_processing_model', '"haiku"', 'LLM tier for processing captures into tasks'),
    ('evening_reset_time', '"21:00"', 'When to suggest workspace reset'),
    ('morning_briefing_tasks', '3', 'Number of tasks to include in morning briefing')
ON CONFLICT (key) DO NOTHING;
"""
