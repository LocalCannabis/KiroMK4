# Kiro: Phased Development Plan

**Version**: 1.0 | **Date**: January 2026 | **Status**: Canonical Specification

---

## Overview

This document provides a step-by-step implementation plan for Kiro v1. Each phase builds on the previous, delivering incremental value while maintaining architectural integrity.

**Target**: Single developer (you) working incrementally.

**Philosophy**: 
- Each phase produces something **usable**, not just buildable
- Phases are scoped to ~1-3 weeks of focused work
- "Done" is defined precisely — no ambiguity
- Non-goals prevent scope creep

---

## Phase Summary

| Phase | Name | Focus | Depends On |
|-------|------|-------|------------|
| 0 | Foundation | Project structure, tooling, config | — |
| 1 | Audio Loop | Wake word → transcription | Phase 0 |
| 2 | Voice Pipeline | Intent → LLM → response → speech | Phase 1 |
| 3 | EFE v1 | Task capture, storage, reminders | Phase 2 |
| 4 | Memory v1 | Episodic + semantic storage | Phase 3 |
| 5 | Personas | Multi-persona with switching | Phase 4 |
| 6 | Proactivity | Morning briefing, stall detection | Phase 3, 4 |
| 7 | Polish | Error handling, stability, logging | All |
| 8 | Abstraction | Hardware/deployment portability | Phase 7 |

---

## Phase 0: Foundation

### Goals

Establish project structure, development environment, and core infrastructure that all subsequent phases depend on.

### Deliverables

```
kiro/
├── pyproject.toml              # Project metadata, dependencies
├── README.md                   # Quick start guide
├── .env.example                # Environment variable template
├── .gitignore
├── config/
│   └── default.yaml            # Default configuration
├── src/
│   └── kiro/
│       ├── __init__.py
│       ├── main.py             # Entry point (kirod)
│       ├── config.py           # Configuration loading
│       ├── events.py           # Event bus implementation
│       ├── models/
│       │   └── __init__.py     # SQLAlchemy base, common models
│       └── utils/
│           ├── __init__.py
│           └── logging.py      # Structured logging setup
├── tests/
│   └── __init__.py
├── scripts/
│   ├── install.sh              # Development setup
│   └── run.sh                  # Launch daemon
└── docs/                       # (Already exists - specifications)
```

**Specific outputs**:

1. **Configuration system**
   - YAML-based config loading
   - Environment variable override support
   - Validation on startup

2. **Event bus**
   - Simple pub/sub using `asyncio`
   - `emit(event_name, payload)`
   - `subscribe(event_name, handler)`

3. **Database foundation**
   - SQLAlchemy setup with SQLite
   - Alembic for migrations
   - Base models defined

4. **Logging**
   - Structured JSON logging
   - Log levels configurable
   - File + stdout output

5. **Daemon skeleton**
   - `kirod` entry point
   - Graceful shutdown handling
   - systemd service file (optional)

### Done Criteria

- [ ] `pip install -e .` works
- [ ] `kirod` starts and logs "Kiro starting..."
- [ ] Config loads from `~/.kiro/config/` or falls back to defaults
- [ ] Event bus: can emit and receive test event
- [ ] Database: can create tables, insert row, query it
- [ ] Ctrl+C shuts down gracefully with "Kiro shutting down..."
- [ ] All tests pass (`pytest`)

### Non-Goals

- ❌ Any audio functionality
- ❌ Any LLM integration
- ❌ Any user-facing features
- ❌ PostgreSQL support (SQLite only for now)
- ❌ systemd integration (manual start is fine)

### Estimated Effort

2-4 days

---

## Phase 1: Always-Listening Audio Loop

### Goals

Establish the foundational audio pipeline: continuous listening, wake word detection, and speech-to-text transcription.

### Deliverables

```
src/kiro/
├── audio/
│   ├── __init__.py
│   ├── capture.py              # Microphone capture (sounddevice)
│   ├── wake_word.py            # Wake word detection (OpenWakeWord)
│   ├── vad.py                  # Voice activity detection
│   └── stt.py                  # Speech-to-text (Whisper API)
```

**Specific outputs**:

1. **Audio capture**
   - Continuous microphone monitoring using `sounddevice`
   - Configurable sample rate (16kHz default)
   - Audio level monitoring (for debugging)

2. **Wake word detection**
   - OpenWakeWord integration
   - Custom wake word: "Hey Kiro" (or configurable)
   - Low CPU usage when idle

3. **Voice activity detection (VAD)**
   - Silero VAD or WebRTC VAD
   - Detect end of utterance (user stopped speaking)
   - Configurable silence threshold

4. **Speech-to-text**
   - Whisper API integration (OpenAI)
   - Audio buffering from wake word to end-of-speech
   - Return transcript with confidence score

5. **Events emitted**
   - `audio.wake_word_detected`
   - `audio.utterance_started`
   - `audio.utterance_complete` (with transcript)

### Done Criteria

- [ ] Daemon starts and begins listening (log: "Listening for wake word...")
- [ ] Say "Hey Kiro" → log shows "Wake word detected"
- [ ] Speak a sentence → log shows transcript (e.g., "Transcript: remind me to buy milk")
- [ ] Silence after speaking → utterance ends within 1-2 seconds
- [ ] CPU usage < 10% when idle (just wake word listening)
- [ ] Works with USB microphone
- [ ] Configurable: wake word sensitivity, VAD threshold, STT provider

### Non-Goals

- ❌ Text-to-speech (no responses yet)
- ❌ Intent parsing (just raw transcript)
- ❌ Any action on transcripts
- ❌ Local STT (Whisper API only for now)
- ❌ Barge-in / interruption handling
- ❌ Multiple wake words

### Dependencies

- OpenAI API key (for Whisper)
- USB microphone connected
- Working audio on Linux (PulseAudio/PipeWire)

### Estimated Effort

3-5 days

### Technical Notes

**Recommended libraries**:
- `sounddevice` for audio capture
- `openwakeword` for wake word
- `webrtcvad` or `silero-vad` for VAD
- `openai` SDK for Whisper API

**Audio pipeline flow**:
```
Microphone → Buffer → Wake Word Detector
                            │
                            ▼ (wake word detected)
                      Start Recording
                            │
                            ▼
                     VAD Monitoring
                            │
                            ▼ (silence detected)
                      Stop Recording
                            │
                            ▼
                      Send to STT
                            │
                            ▼
                 Emit utterance_complete
```

---

## Phase 2: Voice → Response Pipeline

### Goals

Complete the conversation loop: receive transcript, determine intent, generate response via LLM, speak response back.

### Deliverables

```
src/kiro/
├── intent/
│   ├── __init__.py
│   └── router.py               # Intent classification and routing
├── llm/
│   ├── __init__.py
│   ├── gateway.py              # LLM provider abstraction
│   └── providers/
│       ├── __init__.py
│       ├── claude.py           # Anthropic Claude
│       └── openai.py           # OpenAI GPT
├── audio/
│   └── tts.py                  # Text-to-speech (new)
├── conversation/
│   ├── __init__.py
│   └── manager.py              # Basic conversation state
```

**Specific outputs**:

1. **Intent router (basic)**
   - Classify transcripts into categories:
     - `conversation` — General chat, questions
     - `command` — Direct actions (set timer, etc.)
     - `capture` — Commitment/task language detected
   - For Phase 2: Route everything to LLM (smart routing later)

2. **LLM Gateway**
   - Provider abstraction (interface)
   - Claude provider implementation
   - OpenAI provider implementation
   - Request/response logging
   - Basic error handling + retry

3. **Text-to-speech**
   - Piper TTS (local) — primary
   - Fallback: cloud TTS if Piper fails
   - Non-blocking audio playback
   - Interruptible (stop on new wake word)

4. **Conversation manager (basic)**
   - Maintain current conversation context (last N turns)
   - Build LLM prompt with context
   - No persistence yet (memory comes in Phase 4)

5. **Events**
   - `intent.classified`
   - `llm.request_sent`
   - `llm.response_received`
   - `tts.started`
   - `tts.completed`

### Done Criteria

- [ ] Say "Hey Kiro, what's the weather like?" → Kiro responds audibly
- [ ] Responses are contextual (LLM-generated, not canned)
- [ ] Can have 2-3 turn conversation (Kiro remembers previous turns)
- [ ] TTS is intelligible and reasonably natural
- [ ] End-to-end latency < 5 seconds (wake word to start of speech)
- [ ] If LLM fails, Kiro says "I'm having trouble thinking right now"
- [ ] Can switch between Claude and OpenAI via config

### Non-Goals

- ❌ Smart intent classification (LLM for everything is fine)
- ❌ Task/reminder creation (EFE comes in Phase 3)
- ❌ Persistent memory (Phase 4)
- ❌ Personas (Phase 5)
- ❌ Streaming responses (batch is fine for now)
- ❌ Cost optimization (tier routing comes later)

### Dependencies

- Phase 1 complete
- Anthropic API key (for Claude)
- OpenAI API key (for GPT, backup)
- Piper TTS installed (or cloud TTS key)

### Estimated Effort

5-7 days

### Technical Notes

**Basic system prompt (Phase 2)**:
```
You are Kiro, a helpful personal AI assistant. You are voice-first, 
so keep responses concise and conversational. You're talking out loud, 
not writing an essay.
```

**Piper TTS setup**:
- Download voice model (en_US-lessac-medium recommended)
- Test: `echo "Hello world" | piper --model ... --output_file test.wav`

---

## Phase 3: Executive Function Engine v1

### Goals

Implement the core ADHD support system: capture tasks/reminders from voice, store them, and respond to queries.

### Deliverables

```
src/kiro/
├── efe/
│   ├── __init__.py
│   ├── engine.py               # Main EFE coordinator
│   ├── capture.py              # Commitment capture pipeline
│   ├── models.py               # Task, Reminder, Project, etc.
│   ├── store.py                # Database operations
│   └── queries.py              # Query handlers
├── intent/
│   └── router.py               # (Updated) Route to EFE
```

**Specific outputs**:

1. **Data models** (SQLAlchemy)
   - `Task` — title, status, priority, due_date, project_id, context_tags
   - `Reminder` — message, trigger_time, recurrence, acknowledged
   - `Project` — name, status, current_phase, next_step
   - `Capture` — raw_text, timestamp, processed, converted_to

2. **Capture pipeline**
   - Detect task/reminder intent in transcript
   - Parse entities (action, deadline, project reference)
   - Create appropriate record
   - Confirm back to user

3. **Intent routing update**
   - Detect capture-intent phrases:
     - "remind me to...", "I need to...", "don't forget..."
     - "add ... to my list", "remember that..."
   - Route to EFE instead of general LLM

4. **Query handling**
   - "What's on my list?" → List pending tasks
   - "What do I need to do today?" → Today's tasks/reminders
   - "What's the status of [project]?" → Project summary

5. **Reminder triggering**
   - Background scheduler checks due reminders
   - Fires reminder via TTS at trigger time
   - Tracks acknowledgment

### Done Criteria

- [ ] "Remind me to call mom tomorrow at 3pm" → Reminder created, confirmed
- [ ] "I need to buy milk" → Task created, confirmed
- [ ] "What's on my list?" → Kiro reads back pending tasks
- [ ] "What do I need to do today?" → Today's items listed
- [ ] Reminder fires at scheduled time (TTS speaks it)
- [ ] "Mark buy milk as done" → Task status updated
- [ ] Data persists across daemon restarts
- [ ] Can see tasks in database (sqlite CLI or debug endpoint)

### Non-Goals

- ❌ Project state tracking (next_step, blockers)
- ❌ Stall detection
- ❌ Morning briefing
- ❌ Priority/urgency modeling (all tasks equal for now)
- ❌ Context tags filtering (@home, @errands)
- ❌ "Where was I?" resumption
- ❌ Natural language date parsing beyond basics

### Dependencies

- Phase 2 complete
- Alembic migrations for new models

### Estimated Effort

7-10 days

### Technical Notes

**Capture intent detection** (simple approach for v1):
```python
CAPTURE_PHRASES = [
    r"remind me to",
    r"i need to",
    r"don't (let me )?forget",
    r"add .+ to my list",
    r"remember (that|to)",
]

def is_capture_intent(transcript: str) -> bool:
    return any(re.search(p, transcript.lower()) for p in CAPTURE_PHRASES)
```

**Date parsing**: Use `dateparser` library for "tomorrow", "next week", "at 3pm"

---

## Phase 4: Memory v1

### Goals

Implement the layered memory system: working memory, episodic memory, and semantic memory (facts).

### Deliverables

```
src/kiro/
├── memory/
│   ├── __init__.py
│   ├── system.py               # Main memory coordinator
│   ├── working.py              # In-memory session context
│   ├── episodic.py             # Event/conversation storage
│   ├── semantic.py             # Fact storage
│   ├── models.py               # Episode, Fact models
│   └── retrieval.py            # Query and relevance scoring
├── conversation/
│   └── manager.py              # (Updated) Integrate memory
```

**Specific outputs**:

1. **Working memory**
   - Current conversation thread
   - Recent utterances (last 20)
   - Active entities (mentioned people, projects)
   - Persisted to disk every 5 min (crash recovery)

2. **Episodic memory**
   - Store conversations as episodes
   - Episode types: conversation, decision, task_completed, etc.
   - Topic tagging (auto-extracted)
   - Query by time, topic, entity

3. **Semantic memory**
   - Fact storage (subject, predicate, object)
   - Extract facts from conversation ("My mom is Carol")
   - Query facts ("What's my mom's name?")
   - Conflict resolution (new fact supersedes old)

4. **Memory-enhanced LLM calls**
   - Retrieve relevant memories for context
   - Inject into system prompt
   - "We discussed this before..." awareness

5. **Events**
   - `memory.episode_created`
   - `memory.fact_learned`
   - `memory.context_retrieved`

### Done Criteria

- [ ] "My mom's name is Carol" → Fact stored
- [ ] Later: "What's my mom's name?" → "Your mom's name is Carol"
- [ ] Previous conversations influence current responses
- [ ] "What did we talk about yesterday?" → Summary of recent topics
- [ ] Facts persist across restarts
- [ ] Episodes persist across restarts
- [ ] Working memory recovers after crash (from disk snapshot)
- [ ] Memory queries return relevant results (not just recent)

### Non-Goals

- ❌ Summarization pipeline (keep full detail for now)
- ❌ Memory pruning
- ❌ Vector search / embeddings
- ❌ L3 (cold storage) layer
- ❌ Memory layer transitions (all in L2 for now)
- ❌ Episodic reconstruction narratives

### Dependencies

- Phase 3 complete (EFE records events to memory)

### Estimated Effort

7-10 days

### Technical Notes

**Fact extraction** (simple approach for v1):
Use LLM to extract facts from conversation:
```
Given this exchange, extract any durable facts about the user in 
JSON format: {"subject": "...", "predicate": "...", "object": "..."}

Only extract facts that would remain true over time (not events).
Return [] if no facts found.
```

**Relevance scoring** (v1):
```python
def score_relevance(memory_item, query_topics, query_entities):
    score = 0.0
    score += 0.4 * topic_overlap(memory_item.topics, query_topics)
    score += 0.3 * entity_overlap(memory_item.entities, query_entities)
    score += 0.2 * recency_score(memory_item.timestamp)
    score += 0.1 * memory_item.importance
    return score
```

---

## Phase 5: Persona System

### Goals

Implement multiple named personas with distinct tones and behaviors, including voice-triggered switching.

### Deliverables

```
src/kiro/
├── persona/
│   ├── __init__.py
│   ├── system.py               # Persona coordinator
│   ├── registry.py             # Load persona definitions
│   ├── selector.py             # Choose active persona
│   └── prompt.py               # System prompt composition
├── config/
│   └── personas/
│       ├── default.yaml
│       ├── taskmaster.yaml
│       ├── technical.yaml
│       └── advisor.yaml
```

**Specific outputs**:

1. **Persona definitions** (YAML)
   - Four built-in personas: Default, Taskmaster, Technical, Advisor
   - Tone parameters, behavior flags, domain focus
   - System prompt additions

2. **Persona selection**
   - Explicit: "Switch to Taskmaster" / "Taskmaster mode"
   - Context-based: Project association
   - Default fallback

3. **Prompt composition**
   - Base Kiro identity + persona overlay
   - Tone modifiers injected into prompt
   - Domain focus affects memory relevance

4. **Switching acknowledgment**
   - "Switching to Taskmaster mode"
   - "Going back to normal"

5. **Memory interaction**
   - Same memory, different presentation
   - Persona logged with episodes

### Done Criteria

- [ ] "Taskmaster mode" → Kiro confirms switch, tone changes
- [ ] Taskmaster is noticeably more direct/terse
- [ ] "Back to normal" → Returns to Default
- [ ] Technical questions get more precise answers in Technical mode
- [ ] Persona persists within conversation (doesn't reset every turn)
- [ ] Persona resets on new day (configurable)
- [ ] Custom persona can be added via YAML file

### Non-Goals

- ❌ Persona blending
- ❌ Auto-switching based on detected topic
- ❌ Companion/emotional support persona
- ❌ Persona-specific voice (same TTS voice for all)
- ❌ Memory filtering by persona (same facts, different emphasis is enough)

### Dependencies

- Phase 4 complete (personas use memory)

### Estimated Effort

4-6 days

### Technical Notes

**Persona switching detection**:
```python
SWITCH_PATTERNS = [
    (r"switch to (\w+)", 1),
    (r"(\w+) mode", 1),
    (r"be more (direct|technical|gentle)", "map_adjective"),
    (r"back to normal", "default"),
]
```

---

## Phase 6: Proactivity

### Goals

Implement proactive behaviors: morning briefing, stall detection, scheduled prompts — without overwhelming the user.

### Deliverables

```
src/kiro/
├── efe/
│   ├── scheduler.py            # Scheduled events
│   ├── stall_detector.py       # Find stalled tasks/projects
│   ├── briefing.py             # Generate morning briefing
│   └── context_tracker.py      # "Where was I?" support
├── proactive/
│   ├── __init__.py
│   ├── manager.py              # Coordinate proactive behaviors
│   └── config.py               # Scaffolding intensity settings
```

**Specific outputs**:

1. **Scheduler**
   - Background task for time-based triggers
   - Support daily schedules (morning briefing time)
   - Reminder scheduling (from Phase 3)

2. **Morning briefing**
   - Configurable trigger time
   - Content: today's deadlines, commitments, stalled items (max 2)
   - Natural language generation via LLM
   - Max 90 seconds spoken

3. **Stall detection**
   - Daily scan for tasks/projects with no activity
   - Configurable threshold (default: 7 days)
   - Queue alerts for morning briefing

4. **"Where was I?" support**
   - Track active context (current task/project)
   - Detect interruptions
   - On "where was I?" → restore context with next action

5. **Scaffolding intensity**
   - Configuration for light/moderate/heavy
   - Controls: max prompts/day, stall threshold, snooze duration
   - User can change via voice ("be more hands-off")

### Done Criteria

- [ ] Morning briefing fires at configured time
- [ ] Briefing includes today's tasks and stalled items
- [ ] Stalled task appears in briefing after threshold days
- [ ] "Where was I?" returns context after interruption
- [ ] "Be more hands-off" reduces prompt frequency
- [ ] Max daily prompts is respected
- [ ] Prompts can be snoozed ("remind me later")
- [ ] No prompts during configured quiet hours

### Non-Goals

- ❌ Adaptive learning from feedback
- ❌ End-of-day reflection
- ❌ Location-based triggers
- ❌ Midday check-in (can be added later)
- ❌ Anti-dropoff with external integrations

### Dependencies

- Phase 3 complete (tasks exist)
- Phase 4 complete (context tracking uses memory)

### Estimated Effort

5-7 days

### Technical Notes

**Briefing generation prompt**:
```
Generate a brief morning briefing (max 90 seconds spoken) for the user.

Today's date: {date}
Hard deadlines today: {deadlines}
Commitments to others: {commitments}
Stalled items: {stalled}
Suggested focus: {suggested}

Be concise and actionable. End with "That's it" or similar closure.
Use Taskmaster tone: direct, no fluff.
```

---

## Phase 7: Polish & Stability

### Goals

Harden the system for daily use: error handling, logging, monitoring, and edge cases.

### Deliverables

```
src/kiro/
├── utils/
│   ├── errors.py               # Custom exceptions
│   ├── monitoring.py           # Health checks, metrics
│   └── recovery.py             # Crash recovery utilities
├── cli/
│   ├── __init__.py
│   └── commands.py             # Debug/admin CLI
```

**Specific outputs**:

1. **Error handling**
   - Graceful degradation for all external services
   - User-friendly error messages via TTS
   - No silent failures

2. **Logging improvements**
   - Structured logs with correlation IDs
   - Request/response logging for LLM calls
   - Audio event logging

3. **Health monitoring**
   - Periodic self-check (DB connection, audio devices)
   - Log warnings if degraded
   - Optional: simple HTTP health endpoint

4. **Crash recovery**
   - Working memory recovery from snapshot
   - Database integrity check on startup
   - Auto-restart via systemd

5. **Debug CLI**
   - `kiro status` — Show system health
   - `kiro tasks` — List tasks
   - `kiro memory search <query>` — Search memory
   - `kiro config show` — Show active config

6. **Edge case handling**
   - Very long utterances (truncate/warn)
   - Rapid repeated wake words
   - Network outage during LLM call
   - Full disk handling

### Done Criteria

- [ ] Run for 24 hours without crash
- [ ] Network disconnect → graceful degradation, recovery when back
- [ ] Kill daemon, restart → no data loss
- [ ] All errors produce user-friendly spoken message
- [ ] `kiro status` shows meaningful output
- [ ] Logs are clean (no unhandled exceptions)
- [ ] systemd service auto-restarts on crash

### Non-Goals

- ❌ Web dashboard
- ❌ Remote monitoring
- ❌ Performance optimization (unless blocking)
- ❌ Multi-user support

### Dependencies

- Phases 1-6 complete

### Estimated Effort

4-6 days

---

## Phase 8: Hardware Abstraction

### Goals

Prepare codebase for Phase 2 (cloud) and Phase 3 (Pi) deployment without rewrites.

### Deliverables

```
src/kiro/
├── providers/
│   ├── __init__.py
│   ├── audio.py                # AudioProvider interface
│   ├── stt.py                  # STTProvider interface
│   ├── tts.py                  # TTSProvider interface
│   ├── llm.py                  # LLMProvider interface (refactor)
│   └── database.py             # Database provider abstraction
├── platform/
│   ├── __init__.py
│   ├── capabilities.py         # Detect available capabilities
│   └── config.py               # Platform-specific config
```

**Specific outputs**:

1. **Provider interfaces**
   - Abstract base classes for all I/O
   - Existing implementations moved behind interfaces
   - Configuration-driven provider selection

2. **Capability detection**
   - Detect at startup: GPU, audio devices, network
   - Log available capabilities
   - Select providers based on available capabilities

3. **PostgreSQL support**
   - Test SQLAlchemy with PostgreSQL
   - Database URL in config
   - Migration compatibility verified

4. **Configuration profiles**
   - `desktop.yaml` — Full local capability
   - `cloud.yaml` — No local audio
   - `portable.yaml` — Limited local, cloud-dependent

5. **Documentation**
   - Deployment guide for each platform
   - Configuration reference
   - Troubleshooting guide

### Done Criteria

- [ ] Switch SQLite → PostgreSQL via config change (tested)
- [ ] Provider interfaces defined for audio, STT, TTS, LLM
- [ ] Capability detection logs available resources at startup
- [ ] Can run in "headless" mode (no audio, text-only) for testing
- [ ] Configuration profiles documented
- [ ] Same codebase works on desktop and cloud (tested manually)

### Non-Goals

- ❌ Actual cloud deployment (just preparation)
- ❌ Raspberry Pi testing (just preparation)
- ❌ Client application
- ❌ API server for clients

### Dependencies

- Phase 7 complete

### Estimated Effort

4-6 days

---

## Timeline Summary

| Phase | Duration | Cumulative |
|-------|----------|------------|
| 0: Foundation | 2-4 days | 2-4 days |
| 1: Audio Loop | 3-5 days | 5-9 days |
| 2: Voice Pipeline | 5-7 days | 10-16 days |
| 3: EFE v1 | 7-10 days | 17-26 days |
| 4: Memory v1 | 7-10 days | 24-36 days |
| 5: Personas | 4-6 days | 28-42 days |
| 6: Proactivity | 5-7 days | 33-49 days |
| 7: Polish | 4-6 days | 37-55 days |
| 8: Abstraction | 4-6 days | 41-61 days |

**Realistic estimate**: 8-12 weeks for full v1.

**Usable milestone**: After Phase 3 (~4-5 weeks), you have a working voice assistant that captures tasks and reminders. That's a real daily-driver.

---

## Risk Checkpoints

After each phase, evaluate:

| Question | If No, Then... |
|----------|----------------|
| Is it working reliably? | Fix before proceeding |
| Is latency acceptable? | Profile and optimize |
| Is it actually useful? | Revisit requirements |
| Am I still motivated? | Take a break, adjust scope |

---

## Success Metrics (v1 Complete)

Kiro v1 is **done** when you can:

1. ✅ Wake Kiro by voice anywhere in the room
2. ✅ Have a multi-turn conversation
3. ✅ Create tasks and reminders by voice
4. ✅ Ask "what's on my list?" and get accurate answer
5. ✅ Receive morning briefing automatically
6. ✅ Ask "where was I?" and get useful context
7. ✅ Switch personas by voice
8. ✅ Have Kiro remember facts you told it
9. ✅ Run for a week without manual intervention
10. ✅ Trust it enough to stop using other reminder systems

That last one is the real test.

---

*Next: [08-future-expansion.md](08-future-expansion.md)*
