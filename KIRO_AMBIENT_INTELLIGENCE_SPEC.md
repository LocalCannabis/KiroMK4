# KIRO AMBIENT INTELLIGENCE LAYER

## Build Spec & Implementation Prompt

> This document is a build specification for Claude Code (Opus) working inside the Kiro project workspace. It defines the **Ambient Intelligence Layer** — Kiro's passive learning, background cognition, and briefing system that runs continuously while Tim is living his life. Read this entire document before writing any code.

---

## 1. WHAT THIS IS

Kiro today is reactive — Tim talks, a persona responds. This layer makes Kiro **proactive**. While Tim is out, working, sleeping, or just not at the keyboard, Kiro is:

- Ingesting data from every connected stream
- Detecting patterns, anomalies, and trends
- Building and expanding the knowledge base
- Connecting dots across personas
- Preparing synthesized, prioritized briefings

The goal: when Tim next talks to Kiro, she's already ahead of him. She's not starting from zero — she's been thinking about his life in the background.

---

## 2. DESIGN PHILOSOPHY

### 2.1 Kiro Is the Orchestrator, Personas Are Specialists

Each persona (Jack, Finley, Coach, Chef, Doc, Sage, Ops) sees a narrow slice of Tim's world. Kiro sees all of it. The ambient layer is Kiro's **subconscious** — always processing, always integrating, surfacing to conscious attention only when something crosses a relevance threshold.

Personas don't compete for attention. Kiro decides what's worth surfacing, when, and through which persona's voice.

### 2.2 Three Types of Learning

| Type | What It Does | Example |
|------|-------------|---------|
| **Pattern Recognition** | Notices recurring themes, trends, and anomalies across data streams | "WhatsApp group mentioned Brand X supply issues 3 times this week" |
| **Knowledge Accumulation** | Actively builds and enriches the knowledge base with new information | Jack's knowledge base gets new strain grow reports overnight |
| **Context Bridging** | Connects dots across personas that no single persona would see alone | Finley's budget data + Calendar's day off + Coach's activity gap → synthesized recommendation |

### 2.3 Signal, Not Noise

The ambient layer's job is **compression and prioritization**, not information overload. If Kiro surfaces 15 things every morning, Tim will stop listening. The bar for surfacing an insight should be: "Would Tim want to know this, and would he be unlikely to notice it himself?"

### 2.4 Never Repeat, Never Stale

Once an insight has been surfaced to Tim, it's marked as delivered. Kiro doesn't bring up the same thing twice unless the situation has evolved. Briefings reference what's new since the last briefing, not a running dump of everything Kiro knows.

---

## 3. DATA STREAMS

These are the sources Kiro passively monitors. Each stream has an ingestion method, a polling frequency, and a processing strategy.

### 3.1 WhatsApp Group Chats
- **Existing infrastructure:** whatsapp-web.js Node.js listener → SQLite message store
- **Migration:** Move message store to PostgreSQL to unify with Kiro's data layer (or read from existing SQLite as a bridge)
- **Ingestion frequency:** Real-time (listener is always on)
- **Raw storage:** Every message → `kiro_events` with source='whatsapp', metadata includes group name, sender, timestamp
- **Processing:** Periodic summarization (already built), PLUS tagging for: product/strain mentions, staff dynamics signals, customer patterns, supply chain chatter, action items, sentiment shifts
- **Relevant personas:** Sage (product/strain mentions), Ops (staff/operations), Kiro (general awareness)

### 3.2 Google Calendar
- **API:** Google Calendar API (already planned for Kiro integration)
- **Ingestion frequency:** Poll every 15 minutes, or webhook push if configured
- **Raw storage:** Events → `kiro_events` with source='gcal', metadata includes event title, time, attendees, location
- **Processing:** Schedule awareness, prep triggers ("supplier meeting in 2 hours — Sage should have inventory data ready"), time-block analysis (is Tim overbooked? underbooked?), correlation with other streams
- **Relevant personas:** All (schedule affects everyone), Ops (work scheduling), Coach (free time / workout windows)

### 3.3 Gmail
- **API:** Gmail API (already planned for Kiro integration)
- **Ingestion frequency:** Poll every 5-10 minutes, or Gmail push notifications
- **Raw storage:** Email metadata + body → `kiro_events` with source='gmail', metadata includes sender, subject, labels
- **Processing:** BCLDB order confirmations (trigger Sage/Ops workflow), supplier communications, bill/payment notifications (trigger Finley), deadline detection, action item extraction
- **Relevant personas:** Ops (orders, suppliers), Finley (financial emails), Sage (product-related comms)
- **Privacy rule:** Kiro stores email metadata and extracted insights, NOT full email bodies long-term. Full body is used for processing then discarded from events table.

### 3.4 YNAB (You Need A Budget)
- **API:** YNAB Personal Access Token API, delta sync via `server_knowledge` parameter
- **Ingestion frequency:** Poll every 30 minutes (YNAB rate limits: 200 req/hr)
- **Raw storage:** Transactions and budget state → `kiro_events` with source='ynab', metadata includes category, amount (milliunits), payee, account
- **Processing:** Spending pace vs. budget by category, unusual transactions, upcoming scheduled transactions, month-over-month trends, "days of runway" calculations
- **Relevant personas:** Finley (primary), Kiro (overall financial health for briefings)

### 3.5 Grow Tent (Jack's Domain)
- **Source:** `grow_log_entries` table (populated during checkins)
- **Ingestion frequency:** After every checkin (event-driven, not polled)
- **Processing:** Multi-day trend analysis on all environmental readings (humidity, temp, VPD, DLI), comparison against stage-appropriate targets from knowledge base, early warning detection (3+ day trends in wrong direction), growth stage transition reminders ("she's at day 55 of flower — harvest window opens in about a week")
- **Relevant personas:** Jack (primary), Kiro (surfacing in briefings)

### 3.6 News & Industry Feeds
- **Sources:** Cannabis industry RSS feeds, BC government regulatory feeds, BCLDB catalog/product updates, relevant subreddits (r/canadients, r/BCcannabis), industry newsletters
- **Ingestion frequency:** Poll every 1-2 hours
- **Raw storage:** Articles/posts → `kiro_events` with source='feed', metadata includes feed name, title, URL, author
- **Processing:** Relevance filtering (most content is noise — Kiro should discard anything not directly relevant to Tim's store, blog, grows, or market), topic extraction, cross-reference with existing knowledge base, strain/brand mention detection
- **Relevant personas:** Sage (product/industry), Jack (cultivation science), Kiro (blog content opportunities)

### 3.7 Social Media Analytics (Future — When "Keepin' It Local" Launches)
- **Sources:** Instagram Graph API, YouTube Data API, TikTok API
- **Ingestion frequency:** Poll every 1-2 hours during active posting periods
- **Processing:** Engagement metrics, audience growth, content performance comparison, comment sentiment, trending topics for content ideas
- **Relevant personas:** Ops (social media performance), Kiro (content strategy)

---

## 4. DATABASE SCHEMA

### 4.1 Core Event Store

```sql
-- Raw events from all data streams
CREATE TABLE kiro_events (
    id              SERIAL PRIMARY KEY,
    source          VARCHAR(30) NOT NULL,       -- 'whatsapp', 'gcal', 'gmail', 'ynab', 'grow_log', 'feed', 'social'
    source_id       VARCHAR(200),               -- unique ID from source system (message ID, event ID, transaction ID, etc.)
    event_type      VARCHAR(50),                -- source-specific type: 'message', 'calendar_event', 'email', 'transaction', 'article', etc.
    occurred_at     TIMESTAMP NOT NULL,         -- when the event happened in the real world
    ingested_at     TIMESTAMP DEFAULT NOW(),    -- when Kiro captured it
    metadata        JSONB NOT NULL DEFAULT '{}', -- source-specific structured data
    raw_content     TEXT,                        -- full content for processing (may be purged after processing for privacy)
    content_purged  BOOLEAN DEFAULT FALSE,       -- TRUE if raw_content was cleared after processing
    processed       BOOLEAN DEFAULT FALSE,
    processed_at    TIMESTAMP,
    tags            TEXT[],                      -- extracted tags from processing
    UNIQUE(source, source_id)                    -- prevent duplicate ingestion
);

CREATE INDEX idx_kiro_events_source ON kiro_events(source, occurred_at DESC);
CREATE INDEX idx_kiro_events_unprocessed ON kiro_events(processed) WHERE processed = FALSE;
CREATE INDEX idx_kiro_events_tags ON kiro_events USING GIN(tags);
CREATE INDEX idx_kiro_events_metadata ON kiro_events USING GIN(metadata);
```

### 4.2 Insights Store

```sql
-- Processed observations, patterns, and connections
CREATE TABLE kiro_insights (
    id              SERIAL PRIMARY KEY,
    insight_type    VARCHAR(30) NOT NULL,        -- 'pattern', 'anomaly', 'trend', 'reminder', 'preparation', 'bridge', 'knowledge'
    persona         VARCHAR(20),                 -- which persona owns this insight (NULL = Kiro-level cross-persona)
    summary         TEXT NOT NULL,               -- human-readable insight text
    detail          TEXT,                        -- longer explanation if needed
    confidence      VARCHAR(20) NOT NULL DEFAULT 'medium',  -- 'high', 'medium', 'low'
    priority        INTEGER NOT NULL DEFAULT 5,  -- 1 (critical) to 10 (trivial)
    source_event_ids INTEGER[],                  -- references to kiro_events.id that generated this insight
    related_insight_ids INTEGER[],               -- links to other insights (for context bridging)
    tags            TEXT[],
    metadata        JSONB DEFAULT '{}',

    -- Lifecycle
    created_at      TIMESTAMP DEFAULT NOW(),
    expires_at      TIMESTAMP,                   -- some insights are time-sensitive and should auto-expire
    surfaced        BOOLEAN DEFAULT FALSE,       -- has this been presented to Tim?
    surfaced_at     TIMESTAMP,
    surfaced_in     INTEGER,                     -- references kiro_briefings.id
    dismissed       BOOLEAN DEFAULT FALSE,       -- Tim explicitly dismissed this
    acted_on        BOOLEAN DEFAULT FALSE,       -- Tim took action based on this

    -- Evolution tracking
    superseded_by   INTEGER REFERENCES kiro_insights(id),  -- if a newer insight replaces this one
    evolved_from    INTEGER REFERENCES kiro_insights(id)   -- if this evolved from an earlier insight
);

CREATE INDEX idx_kiro_insights_unsurfaced ON kiro_insights(surfaced, priority) WHERE surfaced = FALSE AND dismissed = FALSE;
CREATE INDEX idx_kiro_insights_persona ON kiro_insights(persona, created_at DESC);
CREATE INDEX idx_kiro_insights_type ON kiro_insights(insight_type, created_at DESC);
CREATE INDEX idx_kiro_insights_tags ON kiro_insights USING GIN(tags);
```

### 4.3 Briefings Store

```sql
-- Assembled briefings delivered to Tim
CREATE TABLE kiro_briefings (
    id              SERIAL PRIMARY KEY,
    briefing_type   VARCHAR(20) NOT NULL,       -- 'morning', 'evening', 'commute', 'on_demand', 'alert'
    insight_ids     INTEGER[] NOT NULL,          -- which insights were included
    briefing_text   TEXT NOT NULL,               -- the assembled briefing as delivered
    persona_segments JSONB,                      -- breakdown of which persona contributed what
    delivered_at    TIMESTAMP DEFAULT NOW(),
    delivery_method VARCHAR(20) DEFAULT 'voice', -- 'voice', 'text', 'notification'
    feedback        VARCHAR(20),                 -- 'helpful', 'too_long', 'missed_something', 'irrelevant'
    notes           TEXT                         -- Tim's feedback notes
);

CREATE INDEX idx_kiro_briefings_type ON kiro_briefings(briefing_type, delivered_at DESC);
```

### 4.4 Learning Configuration

```sql
-- Configurable thresholds and preferences for the ambient layer
CREATE TABLE kiro_ambient_config (
    id              SERIAL PRIMARY KEY,
    config_key      VARCHAR(100) UNIQUE NOT NULL,
    config_value    JSONB NOT NULL,
    description     TEXT,
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- Seed with defaults
INSERT INTO kiro_ambient_config (config_key, config_value, description) VALUES
('briefing_schedule', '{"morning": "07:00", "commute": "trigger:location", "evening": "21:00"}',
    'When briefings are assembled and delivered'),
('max_insights_per_briefing', '7',
    'Maximum number of insights in a single briefing. Less is more.'),
('priority_threshold', '6',
    'Only surface insights with priority <= this value. 1=critical, 10=trivial.'),
('pattern_detection_window_days', '7',
    'How many days of events to analyze for pattern recognition'),
('trend_alert_threshold_days', '3',
    'Number of consecutive days a metric must trend before flagging'),
('knowledge_research_enabled', 'true',
    'Whether Kiro actively searches for new knowledge base content'),
('knowledge_research_interval_hours', '12',
    'How often Kiro searches for new knowledge'),
('content_purge_after_hours', '72',
    'Hours after processing before raw_content is purged from sensitive sources (gmail)'),
('stream_polling', '{"gcal": 900, "gmail": 600, "ynab": 1800, "feeds": 3600}',
    'Polling intervals in seconds per data stream');
```

---

## 5. PROCESSING PIPELINE

### 5.1 Architecture Overview

The processing pipeline is a set of **background workers** that run continuously on the Beast. Each worker handles a specific type of processing. Workers are Python scripts managed by systemd (or a simple process manager).

```
Data Streams → Ingestion Workers → kiro_events
                                        ↓
                              Processing Workers
                                        ↓
                                  kiro_insights
                                        ↓
                              Briefing Composer
                                        ↓
                                kiro_briefings → Voice Pipeline
```

### 5.2 Ingestion Workers

One worker per data stream. Each worker:
1. Polls or listens to its source
2. Checks for duplicates (via `source` + `source_id` unique constraint)
3. Writes raw events to `kiro_events`
4. Handles rate limits and errors gracefully (back off, retry, log)

```
Worker: ingest_whatsapp.py    — reads from existing whatsapp-web.js SQLite (bridge) or direct listener
Worker: ingest_gcal.py        — polls Google Calendar API
Worker: ingest_gmail.py       — polls Gmail API
Worker: ingest_ynab.py        — delta sync via YNAB API
Worker: ingest_feeds.py       — polls RSS/news feeds
Worker: ingest_grow.py        — watches grow_log_entries for new rows (triggered, not polled)
```

### 5.3 Processing Workers

These workers pick up unprocessed events and generate insights. They run on a loop with configurable intervals.

#### 5.3.1 Event Tagger
**Runs:** Every 5 minutes
**Input:** Unprocessed events from `kiro_events`
**Action:** For each event, uses LLM (via OpenRouter, lightweight model) to:
- Extract tags (topics, people, products, strains, actions)
- Classify relevance (1-10 scale)
- Assign to relevant persona(s)
- Flag if it needs deeper analysis
**Output:** Updates `kiro_events.tags`, `kiro_events.processed = TRUE`

**Cost control:** Use a cheap, fast model for tagging (e.g., Haiku or equivalent). Reserve Opus-class for deeper analysis. Most events need only tagging, not deep reasoning.

#### 5.3.2 Pattern Detector
**Runs:** Every 30 minutes
**Input:** Processed events from the last N days (configurable, default 7)
**Action:** Looks for:
- **Frequency patterns:** Same topic/tag appearing multiple times across sources
- **Trend patterns:** Numeric values (YNAB spending, grow tent readings, engagement metrics) moving consistently in one direction
- **Absence patterns:** Expected events that didn't happen (no grow checkin in 48hrs, no workout logged in 5 days, a recurring meeting that got cancelled)
- **Correlation patterns:** Events across different sources that are related in time or topic
**Output:** New rows in `kiro_insights` with type='pattern' or type='trend' or type='anomaly'

**Deduplication:** Before creating a new insight, check if a substantially similar insight already exists and is unsurfaced. If so, update the existing insight (add new source events, adjust confidence) rather than creating a duplicate.

#### 5.3.3 Context Bridger
**Runs:** Every hour
**Input:** Recent unsurfaced insights across all personas
**Action:** Looks for connections between persona-specific insights that create cross-domain value:
- Financial insight + calendar event = preparation opportunity
- Health/activity gap + free time on calendar = coaching moment
- Product industry news + inventory data = business action
- Grow tent trend + knowledge base match = proactive advice
**Output:** New rows in `kiro_insights` with type='bridge', linking to the source insights via `related_insight_ids`

**This is the highest-value processing step.** It's what makes Kiro more than the sum of her personas. Use a capable model (Sonnet or Opus class) for this step.

#### 5.3.4 Knowledge Builder
**Runs:** Every 12 hours (configurable)
**Input:** Current active grows, recent blog topics, inventory gaps in knowledge base
**Action:**
- Searches for new grow reports matching Tim's active strains
- Checks for new cannabis research papers or industry publications
- Looks for updated regulatory information (BC cannabis regs)
- Evaluates finds against existing knowledge base
- Chunks, embeds, and stores verified new knowledge with source attribution and confidence
**Output:** New rows in `knowledge_chunks` (Jack's and Sage's knowledge bases), plus `kiro_insights` with type='knowledge' flagging notable new information

#### 5.3.5 Content Purger
**Runs:** Every 6 hours
**Input:** Processed events older than `content_purge_after_hours` from sensitive sources
**Action:** Clears `raw_content` from Gmail events after insights have been extracted. Sets `content_purged = TRUE`. This respects privacy — Kiro keeps the insight, not the email.
**Output:** Updated `kiro_events` rows

---

## 6. BRIEFING SYSTEM

### 6.1 Briefing Types

| Type | Trigger | Tone | Max Insights |
|------|---------|------|-------------|
| **Morning** | Scheduled (configurable, default 7:00 AM) | Calm overview of the day ahead. What happened overnight, what's coming today. | 5-7 |
| **Commute** | Triggered by Tim leaving (manual trigger or future: location) | Concise, audio-optimized. Designed for the walk/transit to work. | 3-5 |
| **Evening** | Scheduled (configurable, default 9:00 PM) | Reflective. How the day went, what to think about, anything to prep for tomorrow. | 3-5 |
| **On-Demand** | Tim says "Kiro, what did I miss?" or "catch me up" | Comprehensive. Everything unsurfaced, prioritized. | Up to config max |
| **Alert** | Insight with priority 1-2 generated (critical) | Immediate, interrupting. Something Tim needs to know now. | 1 |

### 6.2 Briefing Composition

The briefing composer is NOT a simple concatenation of insights. It's an LLM-driven synthesis step that:

1. Pulls all unsurfaced insights at or above the priority threshold
2. Groups them by persona
3. Identifies the narrative thread — what's the overall shape of Tim's day/situation?
4. Composes a unified briefing in Kiro's voice, with persona-specific segments voiced naturally
5. Prioritizes: urgent first, then actionable, then informational
6. Respects the max insight count — if there are 12 things but the cap is 7, the composer must triage

**Kiro's briefing voice:** Warm, direct, efficient. Not robotic, not chatty. Like a trusted chief of staff who respects Tim's time and knows what matters.

### 6.3 Briefing Composition Prompt Structure

```
You are Kiro, Tim's AI personal assistant. You are composing a {briefing_type} briefing.

Current time: {timestamp}
Tim's schedule today: {calendar_summary}

The following insights have been generated by your personas since the last briefing:

{insights_grouped_by_persona}

Compose a briefing that:
- Opens with the most important or time-sensitive item
- Groups related insights naturally (don't rigidly go persona-by-persona)
- Uses each persona's voice when delivering their specific insight
- Bridges cross-persona connections explicitly ("Finley noticed X, and since your calendar shows Y, you might want to Z")
- Closes with anything Tim should keep in mind or prep for
- Stays under {max_duration_seconds} seconds when spoken aloud (estimate ~150 words per minute)
- Does NOT include anything Tim has already been told (check surfaced status)
- Does NOT pad with filler or pleasantries beyond a brief greeting
```

### 6.4 Feedback Loop

After delivering a briefing, Kiro asks (or Tim can volunteer):
- "Was that helpful?"
- "Too long? Too short?"
- "Did I miss anything important?"

Feedback is stored on the `kiro_briefings` row. Over time, Kiro can analyze feedback patterns to calibrate:
- Priority threshold (raise if Tim says "too much noise")
- Insight count per briefing
- Which insight types Tim finds most/least valuable
- Preferred briefing length

---

## 7. PRIORITY SCORING

Insights are scored 1-10 (1 = critical, 10 = trivial). The score is computed by the processing workers based on these factors:

### 7.1 Scoring Factors

| Factor | Effect on Priority (lower = more important) |
|--------|---------------------------------------------|
| **Time sensitivity** | Event expires soon or requires action today → -3 |
| **Financial impact** | Involves money above a threshold → -2 |
| **Health/safety** | Grow tent emergency, health flag → -4 |
| **Cross-persona bridge** | Connects multiple domains → -1 |
| **Recurring pattern** | Seen 3+ times → -1 |
| **First occurrence** | Novel information → -1 |
| **Low confidence** | Insight confidence is 'low' → +2 |
| **Informational only** | No action required → +2 |
| **Already partially known** | Related insight was recently surfaced → +1 |

Base priority starts at 5. Factors adjust up or down. Clamped to 1-10 range.

### 7.2 Alert Thresholds

| Priority | Action |
|----------|--------|
| 1-2 | **Immediate alert.** Interrupt Tim if possible. (e.g., grow tent critical reading, urgent financial issue, time-critical deadline) |
| 3-4 | **Next briefing, featured.** Lead the briefing with this. |
| 5-6 | **Next briefing, included.** Standard inclusion. |
| 7-8 | **Next briefing, if space.** Include only if under max insight count. |
| 9-10 | **Log only.** Don't surface unless Tim asks for everything. |

---

## 8. EXAMPLE: A DAY IN KIRO'S AMBIENT LIFE

This illustrates the full loop for a single day.

**6:00 AM — Overnight processing**
- Knowledge Builder ran at 2 AM. Found a new Reddit grow report for Tim's current strain. Cross-referenced with Bugbee's data — mostly consistent but the grower reports higher cal-mag needs than expected. Stored as knowledge chunk (confidence: medium). Generated insight: "New grow report for [strain] suggests higher calcium demand in flower than typical. Aligns partially with existing data — worth watching." Priority: 6.
- YNAB delta sync picked up a recurring charge. Finley processed it — normal, no insight generated.
- WhatsApp group had 40 messages overnight. Event tagger processed all. 37 were noise. 3 were tagged: staff member mentioned calling in sick Wednesday, two messages about a new product drop from a popular brand. Insights generated: "Staff coverage gap possible Wednesday" (priority: 4, persona: Ops). "New [brand] drop generating buzz in the group" (priority: 6, persona: Sage).

**7:00 AM — Morning briefing composes**
- Pulls 4 unsurfaced insights above priority threshold.
- Composes:

> "Morning, Tim. Few things for you. Ops flagged that [staff member] mentioned in the group chat they might not make Wednesday — worth having a backup plan since that's your BCLDB cutoff day. Sage picked up chatter about a new [brand] drop — sounds like it's getting attention, might be worth pulling the terpene profile for the blog. Jack has a note from overnight research: a grower running your strain reported higher calcium needs in flower than we'd expect. Your girls aren't there yet, but it's on Jack's radar for when they flip. Otherwise, clean day — you're off until Wednesday."

- Briefing stored. All 4 insights marked as surfaced.

**12:00 PM — Grow checkin**
- Tim walks up to the tent. "Hey Jack, let's do a checkin."
- Jack runs the checkin protocol. Tim reports readings. Jack logs them.
- Jack notices humidity has trended up for 3 consecutive afternoons. Generates insight: "Tent humidity trending up — 3 day pattern, afternoons specifically. May correlate with ambient temp rise. Worth investigating airflow." Priority: 5.
- This insight is stored but NOT surfaced during the checkin itself — Jack mentions it naturally as part of his assessment: "Humidity's been creeping up every afternoon this week. She's fine right now but let's keep an eye on it. Might want to check if the fan intake is restricted."

**3:00 PM — Context Bridger runs**
- Connects: Ops insight about Wednesday coverage gap + Gmail ingestion of a BCLDB order email that arrived at 1 PM + Calendar showing Tim works Wednesday.
- Bridge insight: "BCLDB order confirmation came in. You're working Wednesday with possible short staff. Might want to prep the order review tonight so you're not scrambling." Priority: 3.

**9:00 PM — Evening briefing composes**
- Pulls 2 unsurfaced insights (the humidity trend was surfaced by Jack during checkin, so it's excluded).
- Composes:

> "Hey Tim, quick evening note. That BCLDB order confirmation came through this afternoon. With [staff member] potentially out Wednesday, you might want to review the order tonight so it's not a crunch day. The humidity pattern in the tent — Jack mentioned it at your checkin. Nothing else new."

---

## 9. INTEGRATION WITH KIRO

### 9.1 Process Management

All workers run as systemd services on the Beast. Each worker is a standalone Python script with:
- A main loop with configurable sleep interval
- Graceful shutdown handling (SIGTERM)
- Error logging to a unified log (or PostgreSQL log table)
- Health check endpoint (simple HTTP or file-based heartbeat)

```
kiro-ingest-whatsapp.service
kiro-ingest-gcal.service
kiro-ingest-gmail.service
kiro-ingest-ynab.service
kiro-ingest-feeds.service
kiro-ingest-grow.service
kiro-process-tagger.service
kiro-process-patterns.service
kiro-process-bridger.service
kiro-process-knowledge.service
kiro-process-purger.service
kiro-briefing-composer.service
```

### 9.2 OpenRouter Model Routing

Different processing tasks need different model tiers. Cost control matters — the ambient layer runs 24/7.

| Task | Model Tier | Rationale |
|------|-----------|-----------|
| Event tagging | Haiku / cheapest fast model | High volume, simple classification |
| Pattern detection | Sonnet | Needs reasoning but not creative synthesis |
| Context bridging | Sonnet / Opus | Highest value step — cross-domain reasoning |
| Knowledge evaluation | Sonnet | Needs to assess quality and relevance |
| Briefing composition | Sonnet | Needs voice, personality, and prioritization |
| Alert generation | Sonnet | Needs judgment on urgency |

### 9.3 Voice Pipeline Integration

Briefings are delivered through the existing Kiro voice pipeline:
- Briefing text → Kokoro-82M or Chatterbox TTS → Speaker (or Pi 5 over Tailscale)
- Kiro's voice for the briefing wrapper; persona voices for persona-specific segments (future: per-persona TTS voice)
- On-demand briefings triggered by wake word: "Kiro, catch me up" / "Kiro, what did I miss?"

### 9.4 Persona System Prompt Enhancement

Each persona's system prompt should now include a line like:

```
You have access to Kiro's ambient intelligence layer. Recent insights relevant to your domain
will be included in your context. You may reference these proactively in conversation — you
don't need to wait for Tim to ask. If an insight was already surfaced in a briefing, you can
reference it briefly ("as I mentioned this morning") but don't repeat the full detail.
```

---

## 10. BUILD ORDER

Implement in this sequence:

1. **Database schema** — Create `kiro_events`, `kiro_insights`, `kiro_briefings`, and `kiro_ambient_config` tables. Seed config with defaults.

2. **Event ingestion framework** — Build the base ingestion worker class (polling loop, error handling, duplicate prevention, logging). Then implement the first two ingestion workers:
   - `ingest_ynab.py` (cleanest API, well-documented, Finley already designed)
   - `ingest_grow.py` (reads from Jack's `grow_log_entries`, simplest — event-driven trigger)

3. **Event tagger** — The first processing worker. Takes raw events, uses LLM to tag and classify. This validates the full pipeline: ingest → store → process.

4. **Pattern detector** — Second processing worker. Analyzes tagged events over a rolling window. Generates first real insights.

5. **Briefing composer** — Build the morning briefing first. Pulls unsurfaced insights, composes via LLM, stores briefing, marks insights as surfaced. Wire into voice pipeline for delivery.

6. **Context bridger** — The cross-persona connection engine. Depends on having multiple data streams active to be useful, so build after at least 3 ingestion workers are running.

7. **Remaining ingestion workers** — `ingest_gcal.py`, `ingest_gmail.py`, `ingest_whatsapp.py` (bridge from existing SQLite), `ingest_feeds.py`.

8. **Knowledge builder** — Background research worker. Depends on Jack's knowledge base tables (from JACK_PERSONA_SPEC.md) being in place.

9. **Systemd service files** — Package all workers as managed services with logging, restart policies, and health checks.

10. **Feedback loop** — Add briefing feedback capture (voice-driven: "Kiro, that was too long" / "Kiro, you missed something about...") and wire feedback into config adjustments.

---

## 11. HARD RULES

These rules apply to all ambient layer development. Do not deviate.

- **No React, no Vue.** Python workers, Flask endpoints if any UI is needed, Tailwind for any web UI.
- **PostgreSQL only.** All state in Postgres. The WhatsApp SQLite bridge is a temporary read-only source, not a pattern to follow.
- **Config over code.** Polling intervals, priority thresholds, model selection, briefing schedules — all in `kiro_ambient_config`, not hardcoded.
- **Cost-conscious model routing.** Use the cheapest model that can do the job. Haiku for tagging, Sonnet for reasoning, Opus only when cross-domain synthesis demands it.
- **Never surface the same insight twice.** Once surfaced, it's done unless the situation materially evolves (in which case, create a new insight linked via `evolved_from`).
- **Privacy by design.** Purge raw email content after processing. Store insights, not surveillance.
- **Graceful degradation.** If a data stream goes down, Kiro keeps running with the others. No single stream failure should break the system.
- **Log everything.** Every processing decision, every insight generated, every briefing composed. Debugging ambient intelligence requires a full audit trail.
- **Signal over noise.** When in doubt, don't surface it. Tim's attention is the scarcest resource in the system.

---

## 12. FUTURE EXTENSIONS (Do not build yet)

- **Location awareness:** Pi 5 GPS or phone location triggers commute briefing automatically, adjusts persona availability based on whether Tim is at home, at the store, or in transit.
- **Feedback-driven calibration:** ML model trained on Tim's briefing feedback to auto-adjust priority thresholds and insight type preferences over time.
- **Inter-persona dialogue:** Before composing a briefing, personas "discuss" insights in a simulated internal dialogue to resolve conflicts or enrich context. (e.g., Finley and Coach debate whether Tim should spend money on a gym membership vs. home equipment.)
- **Proactive scheduling:** Kiro notices patterns in Tim's behavior (always does grow checkins around noon, always reviews BCLDB orders Tuesday night) and builds a shadow schedule of expected activities, flagging when patterns break.
- **Dream journaling / morning capture:** Tim speaks freely in the morning about what's on his mind. Kiro transcribes, extracts themes, and routes relevant fragments to appropriate personas.
- **Seasonal intelligence:** Knowledge builder accounts for seasonal patterns — outdoor growing season, holiday retail spikes, BC regulatory review cycles — and pre-loads relevant knowledge before Tim needs it.
