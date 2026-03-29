# Finley Financial Profiling — Progress Tracker

## Architecture Decision: SQLite → PostgreSQL

**Decision: Migrate Finley to PostgreSQL.** The SQLite database (`~/.kiro/finley.db`) was built before the broader architecture was decided. Every other layer (Jack, Ambient, Memory, TTS voices) lives in the shared `kiro` PostgreSQL database. Keeping Finley isolated in SQLite means:
- No JSONB for flexible profile data
- No pgvector for knowledge embeddings  
- No cross-persona queries (e.g., ambient briefing pulling financial data)
- Separate backup strategy for one module
- Different connection patterns from every other module

Finley's tables will follow the **`finley_`** prefix convention (matching `kiro_` for Ambient, bare names for Jack).

---

## Build Phases

### Phase 1: Database Migration (SQLite → PostgreSQL)
- [x] Audit existing PG patterns (Jack/Ambient connection pooling, migration framework)
- [ ] Create `finley/migrations/` directory with migration runner
- [ ] Migration 001: Core YNAB cache tables (accounts, categories, transactions, scheduled_transactions, sync_log, insights_queue)
- [ ] Migration 002: Financial profiling tables (financial_profile, wellbeing_assessments, finley_knowledge, finley_engagements)
- [ ] Rewrite `finley/db.py` → psycopg2 + ThreadedConnectionPool (matching Jack/Ambient pattern)
- [ ] Update `finley/config.py` → remove SQLite path, add PG config, load from jack_config.yaml (shared DB)
- [ ] One-time data migration script (SQLite → PG) for the 30 existing transactions
- [ ] Verify sync.py, analyzer.py, intent_router.py all work against PG

### Phase 2: Financial Profile Engine
- [ ] `finley/profiler.py` — Vital signs calculation, behavioral pattern detection, stage assessment
- [ ] `finley/cfpb.py` — CFPB Financial Well-Being Scale with IRT lookup tables
- [ ] Wire profiler into post-sync pipeline (sync.py callback)

### Phase 3: Proactive Engagement
- [ ] `finley/engagement.py` — Trigger evaluation, anti-nagging rules, delivery
- [ ] Time-based triggers (payday ritual, weekly pulse, month-end)
- [ ] Event-based triggers (large expense, payday spike, subscription creep, ADHD tax)
- [ ] Insight-based triggers (category drift, income instability, progress stall)

### Phase 4: Knowledge System
- [ ] `finley/knowledge.py` — CRUD, retrieval by topic/relevance, seeding utilities
- [ ] Tier 1 seed: CFPB framework, YNAB Four Rules, Canada tax basics, ADHD strategies
- [ ] Vector embedding integration for similarity search

### Phase 5: Integration & Polish
- [ ] Update `finley/prompts.py` with profile data injection, knowledge retrieval, engagement context
- [ ] Update `finley/intent_router.py` with new profiling tools
- [ ] Update `config.yaml` with engagement timing, trigger thresholds, anti-nagging cooldowns
- [ ] Fix bugs: missing `ynab_recent_transactions` in allowlist, delta sync under-fetching
- [ ] End-to-end test: sync → classify → profile → engage

---

## Table Inventory (all in `kiro` PostgreSQL database)

### Migrated from SQLite (prefixed `finley_`)
| Table | Purpose |
|---|---|
| `finley_accounts` | YNAB account cache |
| `finley_categories` | YNAB category/budget cache |
| `finley_transactions` | YNAB transaction cache |
| `finley_scheduled_transactions` | YNAB recurring transactions |
| `finley_sync_log` | Sync audit trail |
| `finley_insights_queue` | Proactive insight buffer |

### New Profiling Tables
| Table | Purpose |
|---|---|
| `finley_profile` | Financial vital signs + behavioral patterns + stage (JSONB) |
| `finley_wellbeing` | CFPB Financial Well-Being Scale scores over time |
| `finley_knowledge` | RAG knowledge base (frameworks, strategies, Canadian finance) |
| `finley_engagements` | Proactive engagement log with anti-nagging tracking |
| `_finley_migrations` | Migration tracking (matches Ambient pattern) |

---

## Current Status

**Phase 1 in progress** — building the PostgreSQL migration and rewriting db.py.
