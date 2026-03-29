"""
001_finley_core_schema.py — Core Finley tables in PostgreSQL.

Replaces the old SQLite schema. All YNAB cache tables plus sync/insights
infrastructure, now with proper types, GIN indexes on JSONB, and the
finley_ prefix convention.
"""

SQL = """
-- =========================================================================
-- Finley Core Schema — YNAB cache + sync infrastructure
-- =========================================================================

-- Accounts cache
CREATE TABLE IF NOT EXISTS finley_accounts (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    type                TEXT,
    on_budget           BOOLEAN DEFAULT FALSE,
    closed              BOOLEAN DEFAULT FALSE,
    balance             INTEGER DEFAULT 0,
    cleared_balance     INTEGER DEFAULT 0,
    uncleared_balance   INTEGER DEFAULT 0,
    last_updated        TIMESTAMP DEFAULT NOW()
);

-- Category/budget cache
CREATE TABLE IF NOT EXISTS finley_categories (
    id                  TEXT PRIMARY KEY,
    group_id            TEXT,
    group_name          TEXT,
    name                TEXT NOT NULL,
    budgeted            INTEGER DEFAULT 0,
    activity            INTEGER DEFAULT 0,
    balance             INTEGER DEFAULT 0,
    goal_type           TEXT,
    goal_target         INTEGER,
    goal_target_date    TEXT,
    last_updated        TIMESTAMP DEFAULT NOW()
);

-- Transaction cache (the core data)
CREATE TABLE IF NOT EXISTS finley_transactions (
    id                      TEXT PRIMARY KEY,
    date                    DATE NOT NULL,
    amount                  INTEGER NOT NULL,
    memo                    TEXT,
    payee_id                TEXT,
    payee_name              TEXT,
    category_id             TEXT,
    category_name           TEXT,
    account_id              TEXT,
    account_name            TEXT,
    approved                BOOLEAN DEFAULT FALSE,
    cleared                 TEXT DEFAULT 'uncleared',
    flag_color              TEXT,
    transfer_account_id     TEXT,
    subtransactions         JSONB,
    -- Finley classifier metadata (populated by classifier.py)
    classified_category     TEXT,
    classified_subcategory  TEXT,
    classified_merchant_type TEXT
);

CREATE INDEX IF NOT EXISTS idx_ft_date      ON finley_transactions(date);
CREATE INDEX IF NOT EXISTS idx_ft_category  ON finley_transactions(category_name);
CREATE INDEX IF NOT EXISTS idx_ft_payee     ON finley_transactions(payee_name);
CREATE INDEX IF NOT EXISTS idx_ft_amount    ON finley_transactions(amount);
CREATE INDEX IF NOT EXISTS idx_ft_account   ON finley_transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_ft_cls_cat   ON finley_transactions(classified_category);

-- Scheduled/recurring transactions
CREATE TABLE IF NOT EXISTS finley_scheduled_transactions (
    id              TEXT PRIMARY KEY,
    date_first      DATE,
    date_next       DATE,
    frequency       TEXT,
    amount          INTEGER DEFAULT 0,
    payee_name      TEXT,
    category_name   TEXT,
    memo            TEXT
);

-- Sync audit log
CREATE TABLE IF NOT EXISTS finley_sync_log (
    id                  SERIAL PRIMARY KEY,
    endpoint            TEXT NOT NULL,
    server_knowledge    INTEGER,
    synced_at           TIMESTAMP DEFAULT NOW(),
    records_updated     INTEGER DEFAULT 0
);

-- Proactive insights queue
CREATE TABLE IF NOT EXISTS finley_insights_queue (
    id              SERIAL PRIMARY KEY,
    message         TEXT NOT NULL,
    severity        TEXT DEFAULT 'info',
    created_at      TIMESTAMP DEFAULT NOW(),
    delivered       BOOLEAN DEFAULT FALSE
);
"""
