# KIRO IMPLEMENTATION ROADMAP
### From Current State to Gospel Spec — Phased Delivery Plan
**Version:** 1.0
**Date:** March 21, 2026
**Reference:** `GOSPEL_KIRO_PERSONA_SYSTEM_SPEC.md`

---

## WHERE WE ARE TODAY

**~26,100 lines of custom code. ~60–65% of the Gospel vision is operational.**

### What's Built and Working

| System | Status | Notes |
|---|---|---|
| Voice pipeline (VAD → STT → LLM → TTS) | ✅ Production | Kokoro-82M, RTF 0.08x, 10-persona voice map |
| Persona routing (keyword + sticky sessions) | ✅ Production | 10 personas, reset words, per-persona tools |
| Finley (financial advisor) | ✅ Production | 5,343 lines. YNAB sync, 17 tools, profiling, CFPB wellness, engagement engine, knowledge base |
| Jack (master grower) | ✅ Production | 4,506 lines. Multi-grow, VPD/DLI, 11+ tools, pgvector knowledge, trend analysis |
| Ambient intelligence layer | ✅ Production | 6 ingestors, 5 processors, briefing composer, 13 systemd services |
| Tools system | ✅ Production | 50+ tools. Google Workspace (24), Finley (17), Jack (11+), core utilities |
| Briefing system | ✅ Production | 5 types (morning/evening/commute/on_demand/alert), feedback loop, auto-config |
| WhatsApp ingestion | ✅ Production | Node.js listener → PostgreSQL direct, backfill, idempotent |
| Pi thin client | ✅ Production | State machine, Tailscale, WAV streaming to Beast |
| Memory (legacy) | ✅ Active | SQLite facts + ChromaDB vectors + recent turns |
| PostgreSQL + pgvector | ✅ Production | 30+ tables across 4 domains |
| Orpheus TTS infrastructure | ⏸️ Disabled | Code fixed (double-prefix bug), models on disk, services disabled. Kokoro active. |
| FHRR Glass memory | 🔨 Built, not wired | 2,943 lines. 4-tier architecture complete, not integrated into voice loop |

### What's Prompt-Only (No Domain Code)

| Persona | Gospel Spec Says | What Exists Today |
|---|---|---|
| **Kiro** (Chief of Staff) | Orchestrator, synthesizer, cross-persona arbitrator, guardian of global rules | Good general prompt. Briefings exist but don't synthesize FROM personas — they synthesize ambient insights. No cross-persona conflict resolution. |
| **Coach** (Executive Function) | Task system, project tracking, hyperfocus drift detection, impostor spiral intervention, daily cadence, voice dumps → structured tasks | Fitness-themed prompt. No task DB, no project tracking, no ADHD-specific executive function support. **Biggest gap vs spec.** |
| **Doc** (Health — Dual Office) | Medical reminders, mood pulse, CBT mode, pattern recognition, mental health monitoring, substance awareness | Wellness prompt. No mood tracking, no CBT framework, no pattern recognition, no medical tracking. |
| **Chef** (Culinary) | Fridge/pantry tracking, expiry-driven recipes, repertoire tracking, shopping lists, nutrition coordination, dual kitchen modes | Generic cooking prompt. No inventory system, no recipe DB, no meal planning, no grocery integration. |
| **Sage** (Cannabis Specialist) | Product encyclopedia, strain profiles, terpene breakdowns, LP database, brand mapping | Philosophy/debate prompt (?!). **Not even the right domain.** Needs complete rewrite to cannabis knowledge base. |

### What Doesn't Exist At All

| Feature | Gospel Spec Section |
|---|---|
| Cross-persona CBT mode | Cross-Persona Modes |
| Purchase Review Pipeline (WATCHTOWER) | Cross-Persona Modes |
| Shared persona prompt registry | Implied by architecture |
| Cross-persona awareness/conflict resolution | Global Design Constraints §4 |
| Shame-avoidance enforcement layer | Global Design Constraints §1 |
| Wearable health data integration | Doc — future |
| Flyer/deal API for grocery planning | Chef + Finley |
| Barcode scanner fridge input | Chef — hardware TBD |

---

## GLOBAL DESIGN CONSTRAINTS — COMPLIANCE AUDIT

The spec defines four inviolable rules. Current compliance:

| Constraint | Status | Gap |
|---|---|---|
| **1. No Shame-Based Accountability** | ⚠️ Not enforced | No system-level guardrail. Individual prompts don't explicitly encode this. Finley's engagement engine has anti-nagging, but it's isolated. |
| **2. ADHD/Autism-Informed Communication** | ⚠️ Partial | Voice output is direct and short (good). But no structured options over open-ended prompts. No hyperfocus drift detection. No sensory awareness controls. |
| **3. Technical Credibility Required** | ✅ Good | Finley and Jack give specific, data-driven responses. Other personas are too generic to evaluate. |
| **4. Cross-Persona Awareness** | ❌ Not implemented | Personas operate in silos. No shared context. Briefings pull from ambient insights, not from persona state. |

---

## PHASE V0 — VOICE OVERLAY (Thin client to kiro_server)
**Status: ✅ COMPLETE (March 2026)**
**Effort: 1 session**
**Dependencies: Overlay functional (complete), kiro_server running on port 5400**

The overlay is a **thin client** to the existing `kiro_server.py` `/process` endpoint — the exact same architecture the Pi uses. No new voice engine, no duplicate STT/VAD/Whisper. The Beast does all processing.

### Architecture

```
Browser mic → getUserMedia → ScriptProcessor (16kHz mono PCM)
    → WAV encode in JS → POST /api/voice (Flask proxy)
    → Forward to kiro_server:5400/process
    → kiro_server: WAV → STT → persona routing → LLM (tools) → TTS → WAV
    → Response: transcript + response text + WAV audio
    → Flask proxy → JSON (transcript, response_text, audio base64)
    → JS: show transcript in chat + play WAV via <audio>
```

### Components (all delivered)

#### V0.1 — HUD mic button (`hud.html`)
- Mic SVG button in input area, next to send button
- Three visual states: idle (blue), recording (red pulse), processing (amber blink)
- Click toggles recording on/off
- `Ctrl+M` keyboard shortcut

#### V0.2 — JS voice capture (`Voice` object in `hud.html`)
- `navigator.mediaDevices.getUserMedia` for mic access
- `AudioContext` at 16kHz + `ScriptProcessor` for raw PCM capture
- Float32 → Int16 conversion + WAV header encoding in JS
- Minimum 100ms audio gate (ignores clicks < 1600 samples)
- POST to `/api/voice` with `Content-Type: audio/wav` + session/persona headers

#### V0.3 — Flask proxy route (`/api/voice` in `app.py`)
- Receives WAV from browser, forwards to `kiro_server:5400/process`
- Parses response headers: X-Transcript, X-Response-Text, X-Persona, X-Timing
- Base64-encodes WAV response for JSON transport
- Saves user transcript + assistant response to chat DB
- Error handling: 502 (server error), 503 (server unreachable)
- Health check: `GET /api/voice/health` — pings kiro_server

#### V0.4 — Response playback
- Base64 WAV → Blob → `new Audio(objectURL)` → browser playback
- Transcript shown as user message with 🎤 prefix
- Response shown as assistant message in chat
- Session auto-title from first voice transcript

### What This Reuses (Zero Duplication)
- **kiro_server /process**: STT (faster-whisper), LLM (OpenAI), TTS (Kokoro), persona routing — all existing
- **Pi thin-client pattern**: same architecture, just browser + Flask proxy instead of sounddevice + requests
- **Chat DB and sessions**: voice messages saved alongside text messages
- **`/api/chat` pipeline**: not duplicated — voice goes through kiro_server's own LLM+tools pipeline

### Future Enhancements (not in V0)
- Wake word (OpenWakeWord) for hands-free activation
- Push-to-hold recording mode
- Real-time waveform visualizer
- Streaming partial transcripts
- Voice mode selector in settings (wake/ptt/click/off)

---

## PHASED IMPLEMENTATION PLAN

### Guiding Principles

1. **Highest daily-impact items first.** Coach is the biggest gap — executive function support is the system's reason for existing.
2. **Foundation before features.** Shared persona registry and cross-persona awareness enable everything downstream.
3. **Each phase delivers a working increment.** No phase is wasted work if we stop.
4. **Respect the spec's own priorities.** The spec calls this an "executive function exoskeleton" — Coach is load-bearing.

---

### PHASE 0 — FOUNDATION (Infrastructure for Everything Else)
**Estimated effort: 2–3 sessions**
**Dependencies: None**

The persona system has a structural problem: prompts are duplicated 3× (kiro.py, kiro_server.py, kiro_cli.py) and persona definitions live in code, not config. Every subsequent phase touches persona prompts — fix the foundation first.

#### 0.1 — Shared Persona Registry Module

Create `personas/registry.py`:
- Single source of truth for all persona definitions
- Loads from `personas/` YAML files (one per persona) or a single `personas.yaml`
- Each persona definition includes: name, voice, system_prompt template, tool ownership, routing keywords, reset words
- `get_system_prompt(persona, context_dict)` — template rendering with dynamic context injection
- `get_persona_tools(persona)` — replaces the scattered tool ownership dicts
- `get_routing_config()` — replaces hardcoded keyword maps

#### 0.2 — Gospel-Aligned Persona Prompts

Rewrite all 10 persona prompts to match the Gospel spec exactly:
- **Kiro:** Chief of Staff voice — snarky, long-suffering, affectionate. Not generic assistant.
- **Finley:** Already good. Minor tone alignment to Sam Axe (already mostly there from finley/prompts.py).
- **Coach:** Complete rewrite. Executive function support, not fitness. Peer-level, technically literate.
- **Doc:** Complete rewrite. Dual-office (medical + mental health). Frank medical, calm therapeutic.
- **Chef:** Complete rewrite. Sub-Ramsay energy. Technique-first. Dual kitchen context.
- **Sage:** Complete rewrite. Cannabis product encyclopedia, NOT philosophy/debate.
- **Jack:** Already aligned from jack/prompts.py. Pull into registry.
- **Ops, Ruth, Lisa:** Tone-check against spec (these are less defined in the Gospel).

#### 0.3 — Global Constraint Injection

Add a `GLOBAL_CONSTRAINTS` block that gets prepended to EVERY persona prompt:
- No shame-based accountability rules
- ADHD/autism communication guidelines
- Direct, concrete, structured-options language requirements
- Cross-persona awareness preamble (what other personas have flagged, if anything)

#### 0.4 — Wire Registry Into All Entry Points

Replace `_PERSONA_PROMPTS` dicts in kiro.py, kiro_server.py, kiro_cli.py with:
```python
from personas.registry import get_system_prompt
```

**Deliverable:** Single source of truth for personas. Gospel-aligned prompts. Global constraints enforced system-wide.

---

### PHASE 1 — COACH: Executive Function Exoskeleton
**Estimated effort: 4–6 sessions**
**Dependencies: Phase 0**
**Priority: CRITICAL — This is the system's core value proposition per the Gospel spec.**

The spec is explicit: *"Kiro is not a productivity system. It is an executive function exoskeleton."* Coach is the load-bearing persona for this. Today it's an empty shell.

#### 1.1 — Task/Project Database

PostgreSQL tables:
- `coach_projects` — name, status, priority, notes, created_at, updated_at
- `coach_tasks` — project_id (nullable), title, status (inbox/next/waiting/someday/done), priority, due_date, context tags, energy_level (low/medium/high), estimated_minutes, notes
- `coach_daily_plans` — date, top_3 task_ids, review notes, planned vs actual
- `coach_captures` — raw voice dumps and text inputs before Coach processes them into tasks

Design philosophy: GTD-influenced but ADHD-adapted. No guilt infrastructure. No streaks. Status is information, not judgment.

#### 1.2 — Voice Dump → Structured Task Pipeline

The spec says: *"Tim talks, Coach captures and organizes."*
- Capture raw voice transcripts tagged as task dumps
- LLM extraction: parse intent, project association, priority signals, deadlines
- Coach organizes into project/task structure behind the scenes
- Tim gets a quick confirmation: "Got it. I've added that to the LocalBot project — API pagination fix, high priority."

#### 1.3 — Coach Intent Router + Tools

Following Finley/Jack pattern:
- `coach/intent_router.py` — Tool schemas for: `coach_add_task`, `coach_list_tasks`, `coach_get_project_status`, `coach_plan_day`, `coach_capture_dump`, `coach_whats_next`, `coach_mark_done`, `coach_review_week`
- `coach/db.py` — PostgreSQL CRUD
- `coach/analyzer.py` — Priority scoring, energy matching, deadline proximity
- Register in `tools/registry.py`

#### 1.4 — Daily Cadence Integration

Wire into briefing system:
- **Morning:** Top 3 priorities for today, energy-appropriate first task, any approaching deadlines. Gentle eating reminder (from spec: "Have you eaten, or are we doing the thing where you forget until 3pm?").
- **Midday:** Lightweight check-in via ambient worker. What's on track, should we pivot.
- **Evening:** What got done (specific, not generic). Workspace reset suggestion. Tomorrow's preview.

#### 1.5 — ADHD-Specific Interventions

- **Hyperfocus drift detection:** Time-based analysis of conversation history per persona. If Tim's been deep on something for 4+ hours and the daily priority was different, flag it. "You've been on the SVG pipeline for 5 hours. Loyalty system UI was today's priority. Your call."
- **Sequencer mode:** When Tim says he doesn't know where to start → tiny, concrete, no-decision first steps. "Pick up the three things closest to your keyboard. Now open the cabinet_pages module. First task: rename the import."
- **Impostor spiral detection:** Keyword/sentiment analysis on conversation. When detected → offer CBT reframe (Phase 3) while maintaining Coach's practical peer-level voice.

**Deliverable:** Fully functional executive function support. Task management, voice capture, daily cadence, ADHD-specific interventions.

---

### PHASE 2 — DOC: Health Advisor (Dual Office)
**Estimated effort: 3–5 sessions**
**Dependencies: Phase 0, partial Phase 1 (for cross-persona pattern awareness)**

#### 2.1 — Mood Pulse System

PostgreSQL tables:
- `doc_mood_entries` — date, score (1-5), optional note, context (time of day, what was happening)
- `doc_patterns` — detected pattern type, evidence, severity, first_seen, last_seen, status

Low-friction daily input: "Hey Tim, quick pulse check — 1 to 5, how are you doing?" Optional note. Stored, feeds into pattern recognition.

#### 2.2 — Medical Office

- `doc_medical_reminders` — appointment type, last_done, next_due, nag_interval, notes
- Seed with spec priorities: esophageal motility screening (hereditary), annual physical
- Nagging voice: frank, practical, not moralistic. "When was your last physical? That's what I thought."
- Medication tracking: empty now, structure exists for future

#### 2.3 — Mental Health Office — Pattern Recognition

Cross-system monitoring (reads from, never writes to, other persona data):
- Withdrawal detection: silence across systems, conversation frequency drop
- Project engagement changes: Coach data shows sustained low productivity
- Mood pulse trends: declining scores, missing entries (silence IS data)
- Financial stress correlation: Finley stage regression
- Substance use indicators: time-of-day conversation patterns, topic avoidance

When patterns are detected: calm check-in through voice pipeline. "Hey Tim, I've noticed things have been quiet the last few days. No pressure — just checking in. Door's open."

#### 2.4 — Doc Intent Router + Tools

- `doc/intent_router.py` — `doc_log_mood`, `doc_get_mood_history`, `doc_check_reminders`, `doc_get_patterns`, `doc_start_cbt` (hooks into Phase 3)
- `doc/db.py` — PostgreSQL CRUD
- `doc/prompts.py` — Dual-office voice (frank medical / warm therapeutic)

#### 2.5 — Cross-Persona Health Context

Feed Doc's awareness into other personas:
- Coach knows when Doc sees a rough patch → adjusts expectations
- Finley knows when Doc flags elevated stress → softer delivery
- Kiro's briefing incorporates Doc's gentle nudges naturally

**Critical Design Rule:** When Tim is in a rough patch, Doc does NOT add weight. Open door. Always the same door. No tracking of "days since last engagement." No implied disappointment.

**Deliverable:** Mood tracking, medical reminders, mental health pattern recognition, cross-persona health awareness.

---

### PHASE 3 — CBT MODE (Cross-Persona)
**Estimated effort: 1–2 sessions**
**Dependencies: Phase 0, Phase 2 (Doc foundation)**

#### 3.1 — CBT Reframe Engine

`modes/cbt.py`:
- Guided 4-step reframe: Identify thought → Evidence for → Evidence against → Balanced reframe
- Activation: manual ("Kiro, I need to talk through something") or proactive (Coach detects impostor spiral, Doc detects rough patch, Finley detects financial avoidance)
- Voice inherits from invoking persona (Coach stays practical, Doc stays therapeutic, Finley stays wry)
- State machine: tracks position in the reframe sequence, handles disengagement gracefully ("No 'we should finish this'")

#### 3.2 — CBT Logging + Pattern Analysis

PostgreSQL tables:
- `cbt_sessions` — invoking_persona, trigger, distorted_thought, evidence_for, evidence_against, reframe, outcome
- Feed into Doc's pattern recognition: "The last three times you hit an API problem, the same thought fired. Here's what actually happened each time."

**Deliverable:** Working CBT reframe tool accessible from Coach, Doc, and Finley.

---

### PHASE 4 — CHEF: Culinary Advisor
**Estimated effort: 3–4 sessions**
**Dependencies: Phase 0**

#### 4.1 — Fridge/Pantry Inventory

PostgreSQL tables:
- `chef_inventory` — item, category, quantity, unit, location (fridge/freezer/pantry/work_fridge), added_date, expiry_date, notes
- Input method: voice for now ("Chef, I just bought chicken thighs, bok choy, and rice"). Barcode scanning is pinned for future hardware.

#### 4.2 — Recipe + Repertoire System

- `chef_recipes` — name, cuisine, difficulty, prep_time, cook_time, ingredients JSONB, instructions, source, rating, times_made, last_made, notes
- `chef_meal_log` — date, meal_type (breakfast/lunch/dinner/snack), recipe_id or description, assessment
- Repertoire tracking: detect ruts ("You've made chicken stir fry four times this month"), suggest same-effort alternatives
- Expiry-driven suggestions: "Chicken thighs need to go by tomorrow. You've got bok choy and rice. Here's what I'd do."

#### 4.3 — Dual Kitchen Context

Spec defines two cooking contexts:
- **Home kitchen:** Full capabilities, no oven. Meal prep, batch cooking, stocks/butters/components.
- **Work kitchen:** Toaster oven, Instant Pot, panini press, rice cooker, fridge. Both reheating AND raw cooking.

Chef must track which kitchen Tim is asking about and adjust suggestions accordingly.

#### 4.4 — Shopping Lists + Budget Coordination

- `chef_shopping_lists` — items with store preferences (Kingsway/East Van stores, Freshmart on 1st for savings)
- Finley coordination: consult food budget before suggesting expensive ingredients
- Multi-store optimization: when savings merit extra stops

#### 4.5 — Nutrition Coordination with Doc

- Age-appropriate focus (Tim is 45): sodium, protein, fibre, heart health, digestive health
- Feed into Doc's awareness: eating patterns, nutrition gaps
- Quick healthy options for deep-work sessions: fast, nutritious, zero decision-making required

#### 4.6 — Chef Intent Router + Tools + Knowledge Base

Following established pattern:
- `chef/intent_router.py` — Tool schemas for inventory, recipes, meal logging, shopping lists
- `chef/db.py` — PostgreSQL CRUD
- `chef/prompts.py` — Sub-Ramsay voice. Ramsay Masterclass energy, not Hell's Kitchen.
- `chef/knowledge.py` — Seed: technique library, from-scratch methodologies, Instant Pot recipes, Vancouver seasonal produce, freezer strategy

**Deliverable:** Full culinary advisor with inventory, recipes, repertoire tracking, dual-kitchen, shopping lists, nutrition coordination.

---

### PHASE 5 — SAGE: Cannabis Product Encyclopedia
**Estimated effort: 2–3 sessions**
**Dependencies: Phase 0**

**Note:** Sage's current prompt is for philosophy/debate — completely wrong per the Gospel spec. This is a ground-up rebuild.

#### 5.1 — Cannabis Knowledge Base

PostgreSQL + pgvector (following Jack/Finley pattern):
- `sage_strains` — name, genetics (indica/sativa/hybrid %), lineage, terpene_profile JSONB, effects JSONB, THC/CBD ranges, grower/LP, notes
- `sage_producers` — LP name, brand portfolio, parent company, province, license type
- `sage_brands` — name, producer_id, umbrella company, product types
- `sage_terpenes` — name, aroma, effects, interaction_notes, common_strains

#### 5.2 — Seed Data: Canadian LP Database

Seed with:
- Major Canadian LPs and their brand portfolios
- Brand ownership/umbrella company mapping
- Common strains available in BC market
- Terpene profile reference database

#### 5.3 — Sage Intent Router + Tools

- `sage/intent_router.py` — `sage_strain_lookup`, `sage_terpene_info`, `sage_producer_info`, `sage_brand_lookup`, `sage_compare_strains`
- `sage/db.py` — PostgreSQL CRUD + vector search for strain similarity
- `sage/prompts.py` — Lightweight, knowledge-base-forward. Fast, accurate. Tool, not coach.

**Deliverable:** Fast-reference cannabis product encyclopedia for work use.

---

### PHASE 6 — CROSS-PERSONA AWARENESS + KIRO CHIEF OF STAFF
**Estimated effort: 2–3 sessions**
**Dependencies: Phases 0–5 (most personas operational)**

This is where the system goes from "collection of specialists" to "unified intelligence."

#### 6.1 — Cross-Persona Context Bus

`personas/context_bus.py`:
- Each persona can publish context summaries to a shared store
- `get_cross_persona_context(requesting_persona)` — returns relevant context from other personas
- Examples:
  - Doc knows Coach is seeing a stressful sprint
  - Finley knows Chef needs grocery budget
  - Coach knows Finley flagged financial stress
  - Kiro sees everything

#### 6.2 — Conflict Resolution

When persona priorities conflict:
- Chef says fridge is empty → Finley says food budget is tight → Coach says today's priority is a sprint
- Kiro arbitrates: "Chef needs groceries but Finley says we're tight. Here's what Coach thinks about timing."
- Resolution logic in `personas/arbitrator.py`

#### 6.3 — Unified Briefing Synthesis

Upgrade briefing system to synthesize FROM persona state, not just ambient insights:
- Coach: Today's priorities, project status, approaching deadlines
- Doc: Gentle health nudge, pattern awareness
- Chef: What's in the fridge, anything expiring, meal suggestion
- Finley: Financial pulse, upcoming payments
- Jack: Grow tent status
- Ambient: Calendar, messages, news

One voice (Kiro's), woven together. Not seven reports.

**Deliverable:** True Chief of Staff orchestration. Cross-persona awareness. Unified intelligence.

---

### PHASE 7 — ADVANCED MEMORY (FHRR Glass Integration)
**Estimated effort: 2–3 sessions**
**Dependencies: Phase 0**

The FHRR Glass memory system (2,943 lines) is built but not wired.

#### 7.1 — Migration Path

- Migrate legacy SQLite facts → PostgreSQL `memory_facts` table
- Migrate ChromaDB vectors → pgvector shelf embeddings
- Validate fidelity scores post-migration

#### 7.2 — Wire Into Voice Loop

Replace legacy `MemoryManager` with FHRR in:
- `kiro.py` — `_system_prompt()` context injection
- `kiro_server.py` — `build_system_prompt()` context injection
- `kiro_cli.py` — same pattern

#### 7.3 — Per-Persona Memory

FHRR already supports per-persona glasses. Enable:
- Finley-specific financial memory
- Jack-specific grow knowledge
- Coach-specific task/project context
- Doc-specific health patterns

#### 7.4 — Glass Lifecycle Management

Enable automated:
- Fidelity monitoring and mitosis (glass splitting when saturated)
- Cold retirement (90 days inactive)
- Promotion pipeline (frequently-retrieved facts → Tier 0 instant recall)

**Deliverable:** Production FHRR memory replacing legacy system. Per-persona recall. Self-maintaining glass lifecycle.

---

### PHASE 8 — WATCHTOWER (Purchase Review Pipeline)
**Estimated effort: 4–6 sessions**
**Dependencies: Phase 0, Phase 4 (Chef), Phase 5 (Sage), Phase 6 (cross-persona)**

**Full spec:** `WATCHTOWER_EXTENSION_SPEC.md`

This is a Chrome extension (Manifest V3) that intercepts purchase intent and routes to personas for review.

#### 8.1 — Extension Core

- Content script on retail product pages (Amazon initially)
- Intercept "Add to Cart" / "Buy Now" — pause, not block
- Extract product details (name, price, ASIN/SKU, category)
- Send to Flask backend via Tailscale

#### 8.2 — Backend Purchase Router

- Finley: ALWAYS consulted. Budget impact, spending category, $50 cooling-off threshold.
- Chef: Kitchen equipment, cookware, ingredients.
- Jack: Grow supplies, soil amendments, cultivation gear.
- Coach: Hobby rabbit hole detection.
- Sage: Cannabis-adjacent gear.
- Doc: Health-related purchases.

#### 8.3 — Response Assembly

Kiro consolidates persona inputs into single recommendation. Overlay/notification in browser.

#### 8.4 — Ambient Awareness (Drift Detection)

Extension also provides:
- Browser awareness: Coach detects rabbit holes, doom scrolling
- Tab pattern analysis: "You're three tabs deep into woodworking clamps"
- Purchase history feeding into Finley spending analysis

**Deliverable:** Chrome extension for purchase review + browser ambient awareness.

---

## IMPLEMENTATION PRIORITY MATRIX

```
IMPACT
  ▲
  │  ★ PHASE 1        ★ PHASE 6
  │    (Coach)           (Cross-Persona)
  │
  │  ★ PHASE 0        ★ PHASE 2
  │    (Foundation)      (Doc)
  │
  │  ★ PHASE 3        ★ PHASE 7
  │    (CBT Mode)        (FHRR Memory)
  │
  │  ★ PHASE 4        ★ PHASE 8
  │    (Chef)            (Watchtower)
  │
  │  ★ PHASE 5
  │    (Sage)
  │
  └──────────────────────────────► EFFORT
```

---

## ESTIMATED TIMELINE

| Phase | Sessions | Cumulative | Core Deliverable |
|---|---|---|---|
| **Phase 0** — Foundation | 2–3 | 2–3 | Shared persona registry, Gospel prompts, global constraints |
| **Phase 1** — Coach | 4–6 | 6–9 | Executive function exoskeleton (THE core feature) |
| **Phase 2** — Doc | 3–5 | 9–14 | Health monitoring, mood pulse, pattern recognition |
| **Phase 3** — CBT Mode | 1–2 | 10–16 | Cross-persona cognitive reframe tool |
| **Phase 4** — Chef | 3–4 | 13–20 | Culinary advisor with inventory + recipes |
| **Phase 5** — Sage | 2–3 | 15–23 | Cannabis product encyclopedia |
| **Phase 6** — Cross-Persona | 2–3 | 17–26 | Unified Chief of Staff intelligence |
| **Phase 7** — FHRR Memory | 2–3 | 19–29 | Advanced memory system integration |
| **Phase 8** — Watchtower | 4–6 | 23–35 | Chrome purchase review extension |

**Total: ~23–35 sessions to full Gospel spec implementation.**

---

## WHAT WE'RE NOT TOUCHING (Future / Hardware-Dependent)

| Item | Status | Reason |
|---|---|---|
| Barcode scanner fridge input | Pinned | Hardware dependency (scanner TBD) |
| Wearable health data (Fitbit etc.) | Future | No wearable yet |
| Flyer/deal API for grocery planning | Research needed | API availability TBD |
| Medication tracking | Structure exists | No medications currently |
| Orpheus TTS re-enablement | On hold | Kokoro performing well. Orpheus code is fixed and ready. |
| v1.2 dual-engine TTS (Orpheus + XTTS-v2) | Not planned | Never implemented, no current need |

---

## TECHNICAL PATTERNS (Established by Finley + Jack)

Every new persona follows the same proven pattern:

```
persona_name/
├── __init__.py
├── db.py              # PostgreSQL CRUD with connection pooling
├── migrate.py         # Schema migrations (numbered SQL files)
├── config.py          # YAML config loading
├── prompts.py         # Dynamic system prompt with context injection
├── intent_router.py   # OpenAI function-calling tool schemas + dispatch
├── analyzer.py        # Domain-specific analysis functions
├── knowledge.py       # pgvector knowledge base + seed data
└── migrations/
    ├── __init__.py
    └── 001_schema.py  # Initial table definitions
```

Wire into system:
1. Lazy import in kiro.py + kiro_server.py
2. Register tools in `tools/registry.py`
3. Add tool schemas to intent router
4. Dynamic prompt injection via `_system_prompt()`
5. Briefing integration via ambient insight personas

---

## DECISION LOG

| Decision | Rationale |
|---|---|
| Coach before Doc | Spec says "executive function exoskeleton" is the core purpose. Coach enables this. Doc enriches it. |
| Foundation (Phase 0) first | Prompt duplication across 3 files is a maintenance landmine. Fix once, then build. |
| Sage after Chef | Chef has higher daily impact. Sage is a work reference tool — useful but not life-changing. |
| FHRR Memory in Phase 7 | Legacy memory works. FHRR is better, but the system isn't blocked by it. |
| Watchtower last | Requires most infrastructure (Chef, Sage, Coach, cross-persona). Highest effort, highest dependency count. |

---

*This roadmap is a living document. Update it as phases complete and priorities shift.*
*Reference: GOSPEL_KIRO_PERSONA_SYSTEM_SPEC.md is canonical. This roadmap serves it.*
