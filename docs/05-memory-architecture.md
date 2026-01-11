# Kiro: Memory Architecture

**Version**: 1.0 | **Date**: January 2026 | **Status**: Canonical Specification

---

## 1. Memory Philosophy

### 1.1 Why Memory Matters

Kiro's value depends on **remembering what matters** and **forgetting what doesn't**.

Unlike chat history (linear, complete), Kiro's memory must be:
- **Structured** — Not just "what was said" but "what it means"
- **Queryable** — Retrieve by relevance, not just chronology
- **Compressed** — Old memories summarized, not stored verbatim
- **Decay-aware** — Not everything is worth keeping forever

### 1.2 Core Principles

| Principle | Meaning |
|-----------|---------|
| **Capture liberally** | When uncertain, store it. Pruning is easier than reconstruction |
| **Index thoughtfully** | Memory is only valuable if it can be found |
| **Summarize aggressively** | Detail matters for recent events, gist matters for old ones |
| **Separate fact from event** | "User's mom is named Carol" ≠ "User called mom on Tuesday" |
| **Never lose commitments** | EFE owns tasks, but memory provides the narrative context |

### 1.3 What Memory Is NOT

Memory is **not**:
- A complete transcript of all conversations
- A search engine for the internet
- A replacement for the EFE's task storage
- A backup system for files

Memory is a **contextual retrieval system** that helps Kiro understand the user's world.

---

## 2. Memory Types

Kiro maintains three distinct types of memory, modeled loosely on human cognition:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MEMORY TYPES                                      │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      WORKING MEMORY                                 │   │
│  │  • Current conversation context                                     │   │
│  │  • Recently mentioned entities                                      │   │
│  │  • Active project/task focus                                        │   │
│  │  • Lifespan: Session (minutes to hours)                             │   │
│  │  • Storage: In-memory only                                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      EPISODIC MEMORY                                │   │
│  │  • Events that happened                                             │   │
│  │  • Conversations and their outcomes                                 │   │
│  │  • Decisions made and why                                           │   │
│  │  • Lifespan: Days to months (with summarization)                    │   │
│  │  • Storage: SQLite, compressed over time                            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      SEMANTIC MEMORY                                │   │
│  │  • Facts about the user and their world                             │   │
│  │  • Preferences, relationships, recurring patterns                   │   │
│  │  • Learned information (not tied to specific events)                │   │
│  │  • Lifespan: Persistent (until explicitly changed)                  │   │
│  │  • Storage: SQLite, structured                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Working Memory

**Purpose**: Hold the current conversational and task context

| Aspect | Description |
|--------|-------------|
| **Contents** | Current thread, recent utterances, active entities, focus stack |
| **Lifespan** | Current session; cleared on restart (but can be reconstructed) |
| **Size limit** | ~50 items / ~10,000 tokens equivalent |
| **Storage** | In-memory (Python objects) |
| **Persistence** | Snapshot to disk every 5 minutes for crash recovery |

**Working Memory Contents**:
```
WorkingMemory {
  current_thread_id: UUID
  recent_utterances: [              # Last 20 exchanges
    { speaker, text, timestamp }
  ]
  active_entities: {                # Currently relevant
    projects: [Project],
    tasks: [Task],
    people: [Person],
    topics: [string]
  }
  focus_stack: [                    # What we're "zoomed in" on
    { type: "project", id: UUID },
    { type: "task", id: UUID }
  ]
  mentioned_facts: [                # Facts surfaced this session
    { fact_id, when_mentioned }
  ]
}
```

### 2.2 Episodic Memory

**Purpose**: Record what happened, when, and what resulted

| Aspect | Description |
|--------|-------------|
| **Contents** | Events, conversations, decisions, outcomes |
| **Lifespan** | Full detail for 7 days → summarized for 90 days → archived/pruned |
| **Queryable by** | Time, topic, people involved, associated entities |
| **Storage** | SQLite with JSON payloads |

**Episode Record Structure**:
```
Episode {
  id: UUID
  timestamp: datetime
  type: enum [
    "conversation",      # Multi-turn exchange
    "decision",          # User decided something
    "commitment_made",   # Promise to someone
    "task_completed",    # Work finished
    "information_shared",# User told Kiro something
    "question_answered", # Kiro provided information
    "project_milestone"  # Significant project event
  ]
  summary: string           # 1-2 sentence description
  detail: string?           # Full content (pruned over time)
  participants: [string]    # People involved
  topics: [string]          # Subject tags
  entities: {               # Linked entities
    projects: [UUID],
    tasks: [UUID],
    people: [string]
  }
  outcome: string?          # What resulted from this
  emotional_valence: enum?  # positive, negative, neutral (future)
  importance: float         # 0.0-1.0, affects retention
}
```

### 2.3 Semantic Memory

**Purpose**: Store persistent facts about the user's world

| Aspect | Description |
|--------|-------------|
| **Contents** | Facts, preferences, relationships, patterns |
| **Lifespan** | Permanent until contradicted or explicitly removed |
| **Queryable by** | Subject, predicate, confidence |
| **Storage** | SQLite, structured triples |

**Fact Record Structure**:
```
Fact {
  id: UUID
  subject: string           # "user", "user.mom", "garage_shelf_project"
  predicate: string         # "name_is", "prefers", "lives_in", "is_allergic_to"
  object: string            # The value
  confidence: float         # 0.0-1.0
  source_episode: UUID?     # Where did we learn this?
  learned_at: datetime
  last_confirmed: datetime  # When was this last validated?
  contradicted_by: UUID?    # If superseded, what replaced it?
}
```

**Example Facts**:
```
{ subject: "user.mom", predicate: "name_is", object: "Carol" }
{ subject: "user", predicate: "prefers", object: "morning_briefings_short" }
{ subject: "user", predicate: "works_on", object: "software" }
{ subject: "user.partner", predicate: "name_is", object: "Sam" }
{ subject: "user", predicate: "vehicle_is", object: "2019 Honda Civic" }
```

---

## 3. The Three-Layer Model (L1/L2/L3)

Memory is organized into three **storage layers** based on age and access patterns:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        THREE-LAYER STORAGE MODEL                            │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  L1: HOT (In-Memory)                                                │   │
│  │  • Working memory + last 30 minutes of episodes                     │   │
│  │  • Instant access (<1ms)                                            │   │
│  │  • ~1,000 items max                                                 │   │
│  │  • Lost on crash (reconstructable from L2)                          │   │
│  └──────────────────────────────┬──────────────────────────────────────┘   │
│                                 │ age out after 30 min                      │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  L2: WARM (SQLite, Full Detail)                                     │   │
│  │  • Episodes from last 7 days, full detail                           │   │
│  │  • All semantic facts (permanent)                                   │   │
│  │  • Fast access (<50ms)                                              │   │
│  │  • ~100,000 items capacity                                          │   │
│  │  • Primary query target                                             │   │
│  └──────────────────────────────┬──────────────────────────────────────┘   │
│                                 │ summarize after 7 days                    │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  L3: COLD (SQLite, Summarized)                                      │   │
│  │  • Episodes from 7-90 days, summary only                            │   │
│  │  • Lower-confidence facts                                           │   │
│  │  • Slower access (<200ms)                                           │   │
│  │  • Archival, queryable but not primary                              │   │
│  │  • Pruned after 90 days (configurable)                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Layer Transitions

**L1 → L2** (continuous):
- Episodes older than 30 minutes flush to SQLite
- Working memory snapshots every 5 minutes

**L2 → L3** (daily batch job):
- Episodes older than 7 days:
  - Summarize (LLM-generated summary if detail exists)
  - Discard full `detail` field, keep `summary`
  - Retain all metadata and links

**L3 → Archive/Prune** (weekly batch job):
- Episodes older than 90 days:
  - High importance (>0.7): Retain summary indefinitely
  - Medium importance (0.3-0.7): Keep for 1 year
  - Low importance (<0.3): Prune (delete)

### 3.2 Query Routing

When a query arrives, the Memory System checks layers in order:

```
Query: "What did we decide about the shelf dimensions?"
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. CHECK L1 (Working Memory)                                               │
│    Is "shelf" or "dimensions" in active_entities or recent_utterances?     │
│    → If found, return immediately                                          │
└────────────────────────────────────────────────────────────────────────────┘
         │ not found
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. QUERY L2 (Warm Storage)                                                 │
│    Search episodes where:                                                  │
│      topics CONTAINS "shelf" OR "dimensions"                               │
│      OR entities.projects CONTAINS shelf_project_id                        │
│      OR type = "decision"                                                  │
│    Order by: relevance_score DESC, timestamp DESC                          │
│    → Return top 5 matches                                                  │
└────────────────────────────────────────────────────────────────────────────┘
         │ insufficient results
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 3. QUERY L3 (Cold Storage)                                                 │
│    Same query, but on archived/summarized episodes                         │
│    → Return top 3 matches, note they are older                             │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 4. ASSEMBLE RESPONSE                                                       │
│    Combine results from all layers                                         │
│    Prioritize: L1 > L2 > L3                                                │
│    Return with source attribution                                          │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Memory Operations

### 4.1 Recording (Write Path)

When Kiro learns something new:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         MEMORY WRITE PATH                                  │
└────────────────────────────────────────────────────────────────────────────┘

Input: User says "My mom's coming to visit next weekend"
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. WORKING MEMORY UPDATE                                                   │
│    Add to recent_utterances                                                │
│    Add "user.mom" to active_entities.people                                │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. EPISODE CREATION                                                        │
│    Episode {                                                               │
│      type: "information_shared",                                           │
│      summary: "User mentioned mom visiting next weekend",                  │
│      participants: ["user.mom"],                                           │
│      topics: ["family", "visit", "planning"]                               │
│    }                                                                       │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 3. FACT EXTRACTION (if applicable)                                         │
│    Does this contain a durable fact?                                       │
│    → Yes: "Mom is visiting next weekend" is event, not fact                │
│    → But: If user said "My mom Carol is visiting" we'd extract:            │
│           Fact { subject: "user.mom", predicate: "name_is", object: "Carol" }│
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 4. EFE NOTIFICATION (if actionable)                                        │
│    This implies a future event → notify EFE for potential task/reminder    │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Retrieval (Read Path)

When Kiro needs context:

**Retrieval Triggers**:
| Trigger | What's Retrieved |
|---------|------------------|
| New user utterance | Relevant facts, recent episodes on topic |
| "Where was I?" | Active context, recent breadcrumbs |
| Morning briefing | Today's commitments, stalled items, relevant reminders |
| Name/entity mentioned | All known facts about that entity |
| Project context | Project history, measurements, decisions |

**Relevance Scoring** (v1 implementation):
```
relevance_score = 
    (topic_match * 0.4) +           # Does topic match query?
    (entity_match * 0.3) +          # Does it involve same entities?
    (recency_score * 0.2) +         # How recent? (decay function)
    (importance * 0.1)              # How important was this marked?
```

**Future**: Replace with vector similarity (embedding-based retrieval)

### 4.3 Summarization

**When**: Daily batch job for episodes aging out of L2

**Process**:
```
┌────────────────────────────────────────────────────────────────────────────┐
│                      SUMMARIZATION PIPELINE                                │
└────────────────────────────────────────────────────────────────────────────┘

Episodes from 7 days ago (with full detail)
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. GROUP BY THREAD/TOPIC                                                   │
│    Cluster episodes from same conversation or topic                        │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. GENERATE SUMMARY (LLM)                                                  │
│    Prompt: "Summarize these events in 2-3 sentences. Preserve:             │
│             - Key decisions made                                           │
│             - Commitments to others                                        │
│             - Important facts learned                                      │
│             - Outcomes/results"                                            │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 3. EXTRACT DURABLE FACTS                                                   │
│    Any facts in these episodes that should be semantic memory?             │
│    → Promote to Fact table if not already present                          │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 4. REPLACE DETAIL WITH SUMMARY                                             │
│    episode.detail = NULL                                                   │
│    episode.summary = generated_summary                                     │
│    Move to L3                                                              │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.4 Pruning

**When**: Weekly batch job

**Pruning Rules**:
| Episode Age | Importance | Action |
|-------------|------------|--------|
| < 7 days | Any | Keep full detail |
| 7-90 days | Any | Keep summary |
| > 90 days | High (>0.7) | Keep summary |
| > 90 days | Medium (0.3-0.7) | Keep for 1 year total |
| > 90 days | Low (<0.3) | Delete |
| > 1 year | Medium | Delete |

**Never prune**:
- Episodes linked to active projects
- Episodes containing commitments (until fulfilled)
- Episodes explicitly marked "important" by user
- Semantic facts (only supersede, never delete)

---

## 5. Semantic Memory Details

### 5.1 Fact Categories

| Category | Examples |
|----------|----------|
| **Identity** | User's name, birthday, location |
| **Relationships** | Family members, friends, colleagues |
| **Preferences** | Likes/dislikes, communication style |
| **Possessions** | Vehicle, home, tools |
| **Work** | Job, skills, current projects |
| **Health** | Allergies, conditions (if shared) |
| **Patterns** | "Usually wakes at 7am", "Works from home Fridays" |

### 5.2 Fact Confidence

Facts have confidence scores:

| Confidence | Source | Example |
|------------|--------|---------|
| 1.0 | Explicit statement | "My mom's name is Carol" |
| 0.8 | Strong inference | User said "calling Carol" after "need to call mom" |
| 0.6 | Reasonable inference | Topic patterns suggest interest |
| 0.4 | Weak inference | Single mention, might be temporary |

**Confidence decay**: Facts not reconfirmed in 6 months decrease by 0.1

### 5.3 Fact Conflicts

When new information contradicts existing fact:

```
Existing: { subject: "user", predicate: "lives_in", object: "Austin" }
New info: "We just moved to Portland"
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. DETECT CONFLICT                                                         │
│    Same subject + predicate, different object                              │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. CREATE NEW FACT                                                         │
│    { subject: "user", predicate: "lives_in", object: "Portland",           │
│      confidence: 1.0 }                                                     │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 3. MARK OLD FACT SUPERSEDED                                                │
│    old_fact.contradicted_by = new_fact.id                                  │
│    old_fact is retained but not returned in queries                        │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Memory API

### 6.1 Public Interface

| Method | Description |
|--------|-------------|
| `record_episode(type, summary, detail?, ...)` | Store new episode |
| `record_fact(subject, predicate, object, confidence)` | Store/update fact |
| `query_episodes(filters, limit) → [Episode]` | Search episodes |
| `query_facts(subject?, predicate?) → [Fact]` | Search facts |
| `get_context_for_query(query_text) → MemoryContext` | Retrieve relevant context |
| `get_entity_facts(entity_id) → [Fact]` | All facts about an entity |
| `get_working_memory() → WorkingMemory` | Current session state |
| `update_working_memory(updates)` | Modify active context |
| `summarize_period(start, end) → string` | Generate summary for timeframe |

### 6.2 Memory Context Object

When other subsystems request memory, they receive:

```
MemoryContext {
  working: {
    current_thread: ThreadSummary,
    recent_exchanges: [Exchange],
    active_entities: [Entity]
  },
  episodic: [
    { episode: Episode, relevance: float }
  ],
  semantic: [
    { fact: Fact, relevance: float }
  ],
  suggested_topics: [string]      # For prompt enrichment
}
```

---

## 7. Integration with Other Subsystems

### 7.1 Memory ↔ EFE

| Direction | Data Flow |
|-----------|-----------|
| EFE → Memory | Task/project events become episodes |
| EFE → Memory | "User completed X" stored as episode |
| Memory → EFE | Context for "where was I?" |
| Memory → EFE | Facts about people for commitment context |

**Boundary**: EFE owns task/project **state**. Memory owns **narrative history**.

### 7.2 Memory ↔ Conversation Manager

| Direction | Data Flow |
|-----------|-----------|
| Conv → Memory | Each exchange recorded as episode |
| Memory → Conv | Relevant context for LLM prompts |
| Memory → Conv | "We discussed this before" detection |

### 7.3 Memory ↔ Persona System

| Direction | Data Flow |
|-----------|-----------|
| Memory → Persona | Same facts, persona filters interpretation |
| Persona → Memory | Episodes tagged with active persona |

**Key Point**: All personas share the same memory. Personas differ in how they **interpret and present** memories, not what they know.

---

## 8. Future Expansion: Vector Search

### 8.1 Current Limitation (v1)

Retrieval is keyword/tag based:
- Fast but brittle
- Misses semantic similarity
- Requires good tagging

### 8.2 Future Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    FUTURE: VECTOR-ENHANCED MEMORY                          │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  EMBEDDING GENERATION                                               │   │
│  │  • On episode creation, generate embedding of summary               │   │
│  │  • On fact creation, generate embedding of fact triple              │   │
│  │  • Use local embedding model (e.g., all-MiniLM-L6-v2)               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  VECTOR STORAGE                                                     │   │
│  │  • SQLite with sqlite-vss extension                                 │   │
│  │  • OR: Separate ChromaDB/Qdrant instance                            │   │
│  │  • ~1536 dimensions per embedding                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  HYBRID RETRIEVAL                                                   │   │
│  │  • Keyword search (existing) + vector similarity                    │   │
│  │  • Combine scores: 0.5 * keyword + 0.5 * vector                     │   │
│  │  • Rerank with cross-encoder (optional)                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.3 Migration Path

1. **Phase 1 (v1)**: Keyword/tag retrieval only
2. **Phase 2**: Add embeddings to new episodes, keyword + vector hybrid
3. **Phase 3**: Backfill embeddings for existing episodes
4. **Phase 4**: Vector-primary with keyword fallback

---

## 9. Future Expansion: Episodic Reconstruction

### 9.1 Concept

For important past events, reconstruct a richer narrative from summarized episodes:

```
User: "Tell me about when we planned the shelf project"
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. FIND RELEVANT EPISODES                                                  │
│    Episodes tagged with shelf_project from start date onward               │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. RECONSTRUCT NARRATIVE                                                   │
│    LLM assembles summaries into coherent story:                            │
│    "About three weeks ago, you decided to build a shelf for the garage.    │
│     You measured the space at 4 feet wide. We discussed materials and      │
│     you chose pine plywood. Then you bought the wood but hit a snag        │
│     with the saw blade..."                                                 │
└────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Status

🔮 **FUTURE PHASE** — Not required for v1. Architecture supports it.

---

## 10. Storage Schema

**Note**: Schema is defined via SQLAlchemy ORM for portability between SQLite (local) and PostgreSQL (cloud). Raw SQL shown for clarity.

### 10.1 Tables

```sql
-- Episodic memory
CREATE TABLE episodes (
    id TEXT PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    type TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT,                    -- NULL after summarization
    participants TEXT,              -- JSON array
    topics TEXT,                    -- JSON array
    entities TEXT,                  -- JSON object
    outcome TEXT,
    importance REAL DEFAULT 0.5,
    layer TEXT DEFAULT 'L2',        -- L2 or L3
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_episodes_timestamp ON episodes(timestamp);
CREATE INDEX idx_episodes_type ON episodes(type);
CREATE INDEX idx_episodes_layer ON episodes(layer);

-- Semantic memory
CREATE TABLE facts (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    source_episode_id TEXT,
    learned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_confirmed DATETIME DEFAULT CURRENT_TIMESTAMP,
    contradicted_by TEXT,           -- ID of superseding fact
    FOREIGN KEY (source_episode_id) REFERENCES episodes(id),
    FOREIGN KEY (contradicted_by) REFERENCES facts(id)
);

CREATE INDEX idx_facts_subject ON facts(subject);
CREATE INDEX idx_facts_predicate ON facts(predicate);
CREATE INDEX idx_facts_active ON facts(contradicted_by) WHERE contradicted_by IS NULL;

-- Conversation threads (for working memory persistence)
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    name TEXT,
    started_at DATETIME NOT NULL,
    last_active DATETIME NOT NULL,
    topic_tags TEXT,                -- JSON array
    project_id TEXT,                -- Link to EFE project if applicable
    status TEXT DEFAULT 'active'    -- active, closed
);

-- Future: embeddings table
-- CREATE TABLE embeddings (
--     id TEXT PRIMARY KEY,
--     source_type TEXT,             -- 'episode' or 'fact'
--     source_id TEXT,
--     embedding BLOB,               -- Vector as binary
--     model_version TEXT
-- );
```

---

## 11. Summary

Kiro's Memory System provides:

| Capability | Implementation |
|------------|----------------|
| **Working memory** | In-memory, current session context |
| **Episodic memory** | Events with automatic summarization over time |
| **Semantic memory** | Persistent facts with confidence and conflict resolution |
| **Layered storage** | L1 (hot) → L2 (warm) → L3 (cold) with automatic transitions |
| **Relevance retrieval** | Topic/entity matching (v1), vector search (future) |
| **Pruning** | Importance-based retention with clear rules |

Memory is the foundation that makes Kiro feel like it **knows** the user, not just responds to them.

---

*Next: [06-hardware-roadmap.md](06-hardware-roadmap.md)*
