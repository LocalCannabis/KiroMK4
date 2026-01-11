# Kiro: System Architecture Overview

**Version**: 1.0 | **Date**: January 2026 | **Status**: Canonical Specification

---

## 1. Architectural Principles (Non-Negotiable)

These principles constrain all design decisions:

| Principle | Meaning | Implication |
|-----------|---------|-------------|
| **Daemon-First** | Kiro runs as a long-lived background process | No cold-start latency; state lives in memory; graceful degradation on failure |
| **Service-Oriented** | Subsystems are independent services with defined interfaces | Components can be upgraded, replaced, or scaled independently |
| **Voice-Primary** | Voice is the default I/O path | All features must be accessible without screen/keyboard |
| **Stateful by Default** | Every interaction may persist | No "throwaway" conversations; all context is potentially valuable |
| **Cloud-Assisted, Not Cloud-Dependent** | Cloud LLMs are primary, but local fallback must be possible | Abstract LLM interface; no hard dependency on any single provider |
| **Hardware-Agnostic Core** | Business logic must not assume specific hardware | Separate hardware abstraction layer; capability negotiation at runtime |
| **Fail-Open for Capture** | If uncertain, capture and store | Better to log too much than lose user intent |

---

## 2. Major Subsystems

Kiro consists of **eight primary subsystems** organized into three layers:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              INTERFACE LAYER                                │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │   Audio I/O     │  │  Text Interface │  │  Future: API    │              │
│  │   (Primary)     │  │  (Debug/Fallback)│  │  (Integrations) │              │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘              │
└───────────┼─────────────────────┼─────────────────────┼──────────────────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CORE LAYER                                     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │  Intent Router  │◄─┤  Conversation   │◄─┤    Persona      │              │
│  │                 │  │    Manager      │  │    System       │              │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘              │
│           │                    │                    │                       │
│           ▼                    ▼                    ▼                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │   Executive     │◄─┤     Memory      │◄─┤  LLM Gateway    │              │
│  │ Function Engine │  │     System      │  │                 │              │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘              │
└───────────┼─────────────────────┼─────────────────────┼──────────────────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            INFRASTRUCTURE LAYER                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │ Action Executor │  │  Persistence    │  │    Hardware     │              │
│  │                 │  │  (SQLite/Files) │  │   Abstraction   │              │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Subsystem Responsibilities

### 3.1 Audio I/O Subsystem

**Responsibility**: Capture audio, detect wake word, transcribe speech, synthesize responses

| Component | Function |
|-----------|----------|
| **Audio Capture** | Continuous low-power microphone monitoring |
| **Wake Word Detector** | Local hotword detection ("Hey Kiro" or configurable) |
| **Voice Activity Detection (VAD)** | Determine when user has finished speaking |
| **Speech-to-Text (STT)** | Convert audio → text (cloud or local) |
| **Text-to-Speech (TTS)** | Convert response → audio output |
| **Barge-In Handler** | Allow user to interrupt Kiro mid-response |

**Boundary**: Audio I/O outputs **text transcripts** and receives **text responses**. It does not interpret meaning.

**Local vs Cloud**: Wake word detection and VAD run locally. STT/TTS may be cloud-assisted (Whisper API, cloud TTS) or local (whisper.cpp, Piper).

---

### 3.2 Intent Router

**Responsibility**: Classify user input and route to appropriate handler

| Function | Description |
|----------|-------------|
| **Intent Classification** | Determine input type: command, question, capture, conversation continuation |
| **Urgency Detection** | Flag time-sensitive inputs for priority handling |
| **Routing Decision** | Direct to: EFE, Persona, Memory query, or direct action |
| **Ambiguity Handling** | When intent unclear, ask for clarification OR capture with flag |

**Boundary**: Intent Router receives **text** and outputs **structured intent objects** with routing metadata.

**Key Design Decision**: Intent Router does NOT use the LLM for every classification. It uses a lightweight local classifier for common patterns, escalating to LLM only for ambiguous cases. This reduces latency and cost.

---

### 3.3 Conversation Manager

**Responsibility**: Maintain conversation state, threading, and continuity

| Function | Description |
|----------|-------------|
| **Thread Management** | Group related exchanges into named threads (by topic/project) |
| **Context Window Assembly** | Build relevant context for LLM calls |
| **Turn Tracking** | Maintain who said what, when |
| **Resumption Support** | Answer "Where were we?" with appropriate context |
| **Cross-Session Continuity** | Conversations persist across daemon restarts |

**Boundary**: Conversation Manager owns **conversation state**. It requests memory from Memory System but does not own long-term storage.

**Key Design Decision**: Conversations are not stored as flat logs. They are structured with:
- Thread ID
- Topic/project association
- Key decisions extracted
- Open questions flagged

---

### 3.4 Persona System

**Responsibility**: Provide distinct response personalities and domain expertise filters

| Function | Description |
|----------|-------------|
| **Persona Registry** | Store persona definitions (tone, expertise, constraints) |
| **Persona Selection** | Choose active persona based on context or explicit request |
| **Prompt Injection** | Modify LLM system prompts per persona |
| **Arbitration** | When multiple personas could respond, choose or blend |
| **Memory Filtering** | Same memory, different interpretation per persona |

**Boundary**: Persona System modifies **how** Kiro responds, not **what** it knows. It does not own memory.

**Initial Personas** (hardcoded for v1):
1. **Default/Balanced** — General-purpose, friendly, practical
2. **Taskmaster** — Direct, action-oriented, minimal small talk
3. **Technical** — Systems thinking, precise, assumes competence
4. **Advisor** — Reflective, asks questions, surfaces tradeoffs

---

### 3.5 Executive Function Engine (EFE)

**Responsibility**: Core ADHD support — capture commitments, track tasks, provide scaffolding

| Function | Description |
|----------|-------------|
| **Commitment Capture** | Extract actionable items from natural speech |
| **Task Storage** | Maintain structured task/reminder database |
| **Priority Modeling** | Assign urgency, importance, deadlines |
| **Proactive Prompting** | Decide when to surface reminders (time, context, routine) |
| **Project State Tracking** | Track multi-step projects with current/next step |
| **Daily Structure** | Morning briefing, midday check-in (optional), stall detection |
| **Anti-Dropoff** | Detect stalled tasks, propose smallest next action |

**Boundary**: EFE owns **task/commitment state**. It reads from Memory but maintains its own structured storage for actionable items.

**Key Design Decision**: EFE is a **separate subsystem**, not embedded in conversation. It can be queried, can inject into conversations, and runs background processes (e.g., daily briefing scheduler).

---

### 3.6 Memory System

**Responsibility**: Store, organize, retrieve, and prune long-term information

| Function | Description |
|----------|-------------|
| **Episodic Memory** | What happened, when, with whom |
| **Semantic Memory** | Facts, preferences, learned information |
| **Working Memory** | Current session context, recently active items |
| **Memory Indexing** | Make memories searchable by relevance, not just time |
| **Summarization** | Compress old memories to save space/tokens |
| **Pruning** | Remove low-value memories over time |
| **Memory Queries** | Retrieve relevant context for current conversation |

**Boundary**: Memory System is a **service** that other subsystems query. It does not interpret memories — it stores and retrieves.

**Key Design Decision**: Memory is **layered**:
- **L1 (Working)**: In-memory, current session, ~last 30 minutes
- **L2 (Recent)**: SQLite, last 7 days, full detail
- **L3 (Archive)**: Summarized, older than 7 days, queryable

---

### 3.7 LLM Gateway

**Responsibility**: Abstract all LLM interactions behind a unified interface

| Function | Description |
|----------|-------------|
| **Provider Abstraction** | Support Claude, OpenAI, local models via same interface |
| **Request Routing** | Choose provider based on task (fast/cheap vs capable) |
| **Context Packing** | Assemble system prompt + context + user input |
| **Response Parsing** | Extract structured data from LLM responses |
| **Rate Limiting** | Respect API limits, queue requests |
| **Fallback Handling** | If primary provider fails, try secondary |
| **Cost Tracking** | Log token usage for awareness |

**Boundary**: LLM Gateway is the **only** component that talks to LLMs. All other subsystems request LLM services through it.

**Key Design Decision**: The gateway supports **tiered routing**:
- **Tier 1 (Fast)**: Simple classification, yes/no questions → smaller/cheaper model
- **Tier 2 (Standard)**: General conversation → Claude Sonnet or GPT-4o
- **Tier 3 (Complex)**: Deep reasoning, planning → Claude Opus or equivalent

---

### 3.8 Action Executor

**Responsibility**: Perform side effects — system commands, notifications, integrations

| Function | Description |
|----------|-------------|
| **Action Registry** | Catalog of available actions with required parameters |
| **Permission Model** | What actions require confirmation vs auto-execute |
| **Execution Engine** | Actually perform the action |
| **Result Reporting** | Return success/failure to calling subsystem |
| **Integration Hooks** | Future: calendar, smart home, file system |

**Boundary**: Action Executor **only executes**. It does not decide what to do — that comes from EFE or Intent Router.

**Initial Actions** (v1):
- Set timer/alarm
- Add to shopping list
- Create reminder
- Speak response
- Log to file

---

## 4. End-to-End Data Flow

### Primary Path: Voice Input → Response

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. AUDIO CAPTURE                                                             │
│    Microphone → continuous audio stream (low-power)                          │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ audio frames
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2. WAKE WORD DETECTION (Local)                                               │
│    "Hey Kiro" detected → activate full pipeline                              │
│    No wake word → continue low-power monitoring                              │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ audio (post-wake)
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3. SPEECH-TO-TEXT                                                            │
│    Audio → text transcript                                                   │
│    VAD determines end-of-utterance                                           │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ text: "remind me to call mom tomorrow"
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 4. INTENT ROUTER                                                             │
│    Classify: COMMITMENT_CAPTURE                                              │
│    Route to: Executive Function Engine                                       │
│    Urgency: normal                                                           │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ intent object
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 5. EXECUTIVE FUNCTION ENGINE                                                 │
│    Parse: action="call mom", deadline="tomorrow"                             │
│    Store: new reminder in task database                                      │
│    Prepare: confirmation response                                            │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ response needed
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 6. CONVERSATION MANAGER                                                      │
│    Log exchange to current thread                                            │
│    No persona override → use default                                         │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ response text
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 7. MEMORY SYSTEM                                                             │
│    Store: episodic (user made commitment about mom)                          │
│    Update: working memory with recent exchange                               │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 8. TEXT-TO-SPEECH                                                            │
│    "Got it. I'll remind you to call mom tomorrow."                           │
│    → Audio output to speakers                                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Alternate Path: Proactive Prompt (No User Input)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. EFE SCHEDULER (Background)                                                │
│    Time trigger: 9:00 AM → morning briefing                                  │
│    OR: Context trigger: near hardware store + pending "buy screws" task      │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ proactive prompt request
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2. CONVERSATION MANAGER                                                      │
│    Create new thread or append to existing                                   │
│    Select persona (Taskmaster for briefing)                                  │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ context assembled
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3. LLM GATEWAY                                                               │
│    Generate natural-language briefing from structured data                   │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │ response text
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 4. TEXT-TO-SPEECH                                                            │
│    "Good morning. You have three things today..."                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Local vs Cloud Distribution

| Component | Default Location | Cloud Option | Local Fallback |
|-----------|-----------------|--------------|----------------|
| Audio Capture | **Local** | — | Required |
| Wake Word Detection | **Local** | — | Required |
| VAD | **Local** | — | Required |
| STT | Cloud (Whisper API) | Yes | whisper.cpp |
| TTS | Cloud or Local | Yes | Piper TTS |
| Intent Router (fast path) | **Local** | — | Required |
| Intent Router (ambiguous) | Cloud (LLM) | Yes | Degrade to capture-all |
| LLM Gateway | Cloud | Required (initially) | Future: llama.cpp |
| Memory System | **Local** | — | Required |
| EFE | **Local** | — | Required |
| Persona System | **Local** | — | Required |
| Action Executor | **Local** | — | Required |

**Key Principle**: All **state** lives locally. Cloud is used only for **inference** (STT, TTS, LLM).

---

## 6. Inter-Service Communication

### Communication Pattern: Event Bus + Direct Calls

```
┌─────────────────────────────────────────────────────────────────┐
│                         EVENT BUS                               │
│   (async notifications: "user spoke", "task created", etc.)     │
└─────────────────────────────────────────────────────────────────┘
        ▲           ▲           ▲           ▲           ▲
        │           │           │           │           │
   ┌────┴────┐ ┌────┴────┐ ┌────┴────┐ ┌────┴────┐ ┌────┴────┐
   │ Audio   │ │ Intent  │ │  EFE    │ │ Memory  │ │ Persona │
   │  I/O    │ │ Router  │ │         │ │ System  │ │ System  │
   └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘
        │           │           │           │           │
        └───────────┴───────────┴───────────┴───────────┘
                    Direct calls (sync requests)
```

**Event Bus** (async):
- `user.speech.detected` — wake word triggered
- `user.utterance.complete` — transcript ready
- `task.created` — EFE captured new commitment
- `reminder.due` — time-based trigger fired
- `memory.updated` — new information stored

**Direct Calls** (sync):
- Intent Router → Memory: "Get relevant context for this input"
- EFE → LLM Gateway: "Parse this into structured task"
- Conversation Manager → Persona: "Get system prompt for current persona"

**Implementation**: Python `asyncio` with simple pub/sub for events, direct method calls for synchronous queries.

---

## 7. Process Model

Kiro runs as a **single daemon process** with multiple async services:

```
┌─────────────────────────────────────────────────────────────────┐
│                     kirod (main daemon)                         │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Audio Thread │  │  Event Loop  │  │  Scheduler   │          │
│  │ (dedicated)  │  │  (asyncio)   │  │  (cron-like) │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                 │
│  Services (async tasks within event loop):                      │
│  - IntentRouter                                                 │
│  - ConversationManager                                          │
│  - ExecutiveFunctionEngine                                      │
│  - MemorySystem                                                 │
│  - PersonaSystem                                                │
│  - LLMGateway                                                   │
│  - ActionExecutor                                               │
└─────────────────────────────────────────────────────────────────┘
```

**Why single process?**
- Simpler deployment
- Shared memory for fast inter-service communication
- Easier debugging
- Sufficient for Phase 1 (desktop daemon)

**Future scaling**: Services can be extracted to separate processes if needed for hardware distribution.

---

## 8. Persistence Strategy

### 8.1 Database Abstraction

Kiro uses **SQLAlchemy ORM** to abstract the database layer, allowing deployment-specific database selection:

| Deployment | Recommended DB | Rationale |
|------------|---------------|-----------|
| Phase 1 (Desktop) | SQLite | Zero config, single file, perfect for daemon |
| Phase 2 (Cloud/GCP) | PostgreSQL | Cloud SQL native, multi-client capable |
| Phase 3 (Raspberry Pi) | SQLite | Lightweight, low resource usage |
| Phase 4 (Mobile) | SQLite | Standard for mobile/embedded |

**Configuration-driven selection** (no code changes required):
```yaml
# ~/.kiro/config/database.yaml
database:
  driver: sqlite                          # or: postgresql
  sqlite:
    path: ~/.kiro/data/kiro.db
  postgresql:
    host: localhost                       # or: Cloud SQL instance
    port: 5432
    database: kiro
    user: kiro
    password_env: KIRO_DB_PASSWORD        # Read from environment
```

### 8.2 Portability Rules

To maintain database portability:
1. **All queries via SQLAlchemy ORM** — no raw SQL
2. **No SQLite-specific features** — no `ROWID`, no `sqlite3` module
3. **No PostgreSQL-specific features** — no `ARRAY` types, no `JSONB` operators in queries
4. **JSON stored as TEXT** — parsed in Python, works on both
5. **Timestamps as ISO strings or Unix epochs** — avoid DB-specific datetime handling
6. **Migrations via Alembic** — tested on both backends

### 8.3 Data Storage Summary

| Data Type | Storage | Format | Backup Strategy |
|-----------|---------|--------|-----------------|
| Tasks/Reminders | SQLite/PostgreSQL | Structured tables | DB backup |
| Conversation Threads | SQLite/PostgreSQL | JSON in TEXT columns | DB backup |
| Memory (L2/L3) | SQLite/PostgreSQL | Structured + JSON | DB backup |
| Persona Definitions | YAML files | Human-readable | Git |
| Configuration | YAML files | Human-readable | Git |
| Audio Logs | Optional WAV | Binary | Not backed up |

**Local paths** (SQLite mode):
- Database: `~/.kiro/data/kiro.db`
- Config: `~/.kiro/config/`

**Cloud mode** (PostgreSQL):
- Database: Cloud SQL or self-managed PostgreSQL
- Config: Same YAML files, deployed with application

---

## 9. Key Architectural Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Process model | Single daemon, async services | Simplicity for v1; can scale later |
| Inter-service comm | Event bus + direct calls | Loose coupling for events, low latency for queries |
| LLM abstraction | Gateway with tiered routing | Cost control, provider flexibility |
| Memory storage | Layered (L1/L2/L3) | Balance between availability and storage |
| Intent classification | Local-first with LLM fallback | Latency reduction |
| Persona implementation | Prompt injection, not separate models | Practical given cloud LLM reliance |
| Conversation structure | Threaded, not flat | Enables "where were we?" across time |
| EFE independence | Separate subsystem, own storage | Core mission; must evolve independently |

---

## 10. Performance Architecture

Voice assistants live or die by response latency. Users expect near-instant feedback. This section defines Kiro's performance targets and the architecture to achieve them.

### 10.1 Latency Budget

**Target**: Wake word to first audio output ≤ 2 seconds

| Stage | Target | Current | Optimization Path |
|-------|--------|---------|-------------------|
| Wake word → Recording end | 0.3-0.5s | 0.5s | VAD tuning |
| STT (transcription) | 0.3s | 2.0s | Local faster-whisper |
| Intent classification | 0.05s | 0.05s | Already local |
| LLM generation | 0.8s | 1.5s | Streaming + model choice |
| TTS synthesis | 0.2s | 0.5s | Local Piper |
| **Total** | **≤2s** | **~4.5s** | |

### 10.2 Streaming Architecture (Critical)

The key insight: **Don't wait for the full LLM response before speaking.**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STREAMING RESPONSE PIPELINE                              │
│                                                                             │
│  LLM Stream:  "Sure, │ I'll remind │ you to call │ mom tomorrow │ ..."     │
│                  │          │            │              │                   │
│                  ▼          ▼            ▼              ▼                   │
│  TTS Buffer:  [Sure,]  [I'll remind] [you to call] [mom tomorrow]          │
│                  │          │            │              │                   │
│                  ▼          ▼            ▼              ▼                   │
│  Audio Out:   🔊 -----> 🔊 -------> 🔊 ---------> 🔊 ---------->           │
│                                                                             │
│  User hears first word while LLM is still generating!                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Implementation approach**:
1. LLM Gateway streams tokens via `generate_stream()`
2. Sentence buffer accumulates until punctuation (. , ! ?)
3. Each complete phrase → TTS synthesis (can pipeline)
4. Audio chunks queued for playback
5. User hears response ~500ms after first token

### 10.3 Local vs Cloud Tradeoffs by Hardware

| Component | The Beast (GPU) | Raspberry Pi | Cloud-Only |
|-----------|-----------------|--------------|------------|
| **STT** | faster-whisper (local) | Whisper API | Whisper API |
| **LLM** | Local fallback available | Cloud required | Cloud required |
| **TTS** | Piper (local) | Piper (local) | OpenAI TTS |

### 10.4 STT Strategy

**Default (Cloud)**: OpenAI Whisper API
- Pros: High accuracy, no GPU memory
- Cons: Network latency (~1.5s), API cost

**Local Option (GPU)**: faster-whisper with CTranslate2
- Pros: ~0.3s latency, no API cost, offline capable
- Cons: Requires ~2GB VRAM, initial model load
- Model: `large-v3` for accuracy, `small` for speed
- Trigger: Auto-select based on GPU availability at startup

```python
# Capability detection at startup
if gpu_available and vram >= 2048:
    stt_engine = "faster-whisper"
    stt_model = "large-v3"  # or config override
else:
    stt_engine = "whisper-api"
```

### 10.5 TTS Strategy

**Primary (Local)**: Piper TTS
- Pros: ~50ms synthesis, no network, natural voices
- Cons: Requires installation, ~100MB per voice
- Model: `en_US-amy-medium` (default), configurable

**Fallback (Cloud)**: OpenAI TTS
- Pros: High quality, no setup
- Cons: Network latency (~0.5s), API cost
- Voice: `nova` (default), configurable

**Selection logic**:
```python
if piper_available:
    tts_engine = "piper"
elif openai_api_key:
    tts_engine = "openai"
else:
    tts_engine = "disabled"  # Text-only mode
```

### 10.6 LLM Strategy

**Tiered model selection**:

| Tier | Use Case | Model | Latency |
|------|----------|-------|---------|
| Fast | Yes/no, simple routing | gpt-4o-mini | ~0.3s |
| Standard | General conversation | claude-sonnet or gpt-4o-mini | ~0.8s |
| Complex | Planning, analysis | claude-opus or gpt-4o | ~2s |

**Streaming is mandatory** for Standard and Complex tiers.

**Local fallback** (Phase 1 stretch goal):
- Model: Llama 3.1 8B via llama.cpp
- Use case: Network outage, privacy mode
- Quality: Acceptable for simple tasks, not primary

### 10.7 VAD Tuning for Responsiveness

Voice Activity Detection directly impacts perceived latency.

| Parameter | Responsive | Balanced | Patient |
|-----------|------------|----------|---------|
| `min_speech_duration` | 0.15s | 0.25s | 0.4s |
| `max_silence_duration` | 0.4s | 0.6s | 1.0s |
| Best for | Quick commands | General use | Thinking aloud |

**Adaptive VAD** (future): Detect user's speaking pattern and adjust dynamically.

### 10.8 Performance Monitoring

Built-in metrics (logged per interaction):

```json
{
  "event": "interaction_complete",
  "timings": {
    "wake_to_vad_end": 0.82,
    "stt_latency": 0.31,
    "intent_latency": 0.02,
    "llm_first_token": 0.45,
    "llm_complete": 1.23,
    "tts_synthesis": 0.08,
    "total_latency": 1.58
  },
  "engines": {
    "stt": "faster-whisper",
    "llm": "claude-sonnet",
    "tts": "piper"
  }
}
```

### 10.9 Hardware Capability Negotiation

At startup, Kiro detects available hardware and configures engines:

```
┌─────────────────────────────────────────────────────────────────┐
│                    CAPABILITY DETECTION                         │
│                                                                 │
│  1. Check GPU availability (nvidia-smi / torch.cuda)           │
│  2. Check VRAM (need 2GB+ for local STT)                       │
│  3. Check Piper installation (which piper)                     │
│  4. Check API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY)        │
│  5. Log capability profile                                      │
│  6. Select optimal engine configuration                         │
└─────────────────────────────────────────────────────────────────┘
```

**Example capability log**:
```
[info] hardware_profile gpu=rtx3060 vram=12288MB cpu_cores=16
[info] engine_selection stt=faster-whisper tts=piper llm=claude-sonnet
[info] estimated_latency target=1.5s
```

---

## 11. Boundary Contracts (Summary)

| From | To | Contract |
|------|----|----------|
| Audio I/O | Intent Router | `Utterance(text, timestamp, confidence)` |
| Intent Router | EFE | `Intent(type, entities, urgency, raw_text)` |
| Intent Router | Conversation Manager | `Intent(...)` with routing directive |
| EFE | Memory System | `MemoryWrite(type, content, associations)` |
| Any Service | LLM Gateway | `LLMRequest(prompt, tier, response_format)` |
| EFE | Action Executor | `Action(type, parameters, confirm_required)` |
| Memory System | Any Service | `MemoryResult(items, relevance_scores)` |

---

*Next: [03-executive-function-engine.md](03-executive-function-engine.md)*
