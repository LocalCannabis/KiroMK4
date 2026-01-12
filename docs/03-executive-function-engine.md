# Kiro: Executive Function Engine (EFE)

**Version**: 1.0 | **Date**: January 2026 | **Status**: Canonical Specification

---

## 1. EFE Core Mission

The Executive Function Engine is **the reason Kiro exists**.

It is not a todo app with voice input. It is a **cognitive prosthetic** that compensates for specific executive function deficits:

| Deficit | How EFE Compensates |
|---------|---------------------|
| Working memory limits | Captures fleeting thoughts before they vanish |
| Prospective memory failure | Surfaces reminders at the right moment, not just the right time |
| Task drop-off after interruption | Maintains "where was I?" state, ready for instant resumption |
| Initiation difficulty | Proposes smallest viable next action to reduce activation energy |
| Project stall-out | Detects dormant work and gently re-surfaces it |
| Commitment fog | Records promises/commitments with enough context to honor them |

**Design Philosophy**: The EFE must be **effective without being oppressive**. An ADHD brain is already overwhelmed; Kiro must reduce cognitive load, not add to it.

---

## 2. What the EFE Tracks

The EFE manages five distinct entity types, each with different lifecycles and behaviors:

### 2.1 Entity Type Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EFE ENTITY HIERARCHY                              │
│                                                                             │
│  ┌─────────────┐                                                            │
│  │   PROJECT   │ ◄── Multi-step, long-running, has state machine           │
│  └──────┬──────┘                                                            │
│         │ contains                                                          │
│         ▼                                                                   │
│  ┌─────────────┐                                                            │
│  │    TASK     │ ◄── Actionable item, belongs to project or standalone      │
│  └──────┬──────┘                                                            │
│         │ may generate                                                      │
│         ▼                                                                   │
│  ┌─────────────┐                                                            │
│  │  REMINDER   │ ◄── Time-triggered prompt, may link to task               │
│  └─────────────┘                                                            │
│                                                                             │
│  ┌─────────────┐                                                            │
│  │ COMMITMENT  │ ◄── Promise to another person (special tracking)          │
│  └─────────────┘                                                            │
│                                                                             │
│  ┌─────────────┐                                                            │
│  │   CAPTURE   │ ◄── Unprocessed thought (inbox, needs triage)             │
│  └─────────────┘                                                            │
│                                                                             │
│  ┌─────────────┐                                                            │
│  │ MEASUREMENT │ ◄── Data point for active project (dimension, quantity)   │
│  └─────────────┘                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Entity Definitions

#### CAPTURE (Inbox Item)

**Purpose**: Zero-friction thought capture before it's lost

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `raw_text` | string | Exact transcription of what user said |
| `timestamp` | datetime | When captured |
| `context_hint` | string? | What was happening (active project, time of day) |
| `processed` | boolean | Has this been triaged? |
| `converted_to` | UUID? | If promoted, link to resulting entity |

**Lifecycle**: Created → Triaged (converted to Task/Reminder/etc. OR dismissed) → Archived

**Key Behavior**: Captures are **never silently dropped**. If the user says something that might be important, it goes in the capture inbox even if intent parsing fails.

---

#### TASK

**Purpose**: A single actionable item

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `title` | string | Short, action-oriented description |
| `description` | string? | Additional context |
| `project_id` | UUID? | Parent project, if any |
| `status` | enum | `pending`, `in_progress`, `blocked`, `done`, `dropped` |
| `priority` | enum | `critical`, `high`, `normal`, `low`, `someday` |
| `due_date` | date? | Hard deadline, if any |
| `soft_deadline` | date? | "Would be nice by" date |
| `estimated_minutes` | int? | Rough time estimate |
| `energy_level` | enum? | `high`, `medium`, `low` — what state needed to do this |
| `context_tags` | string[] | Where/when appropriate: `@home`, `@computer`, `@errands` |
| `created_at` | datetime | When created |
| `last_touched` | datetime | Last interaction (view, edit, progress) |
| `stall_days` | int | Computed: days since last_touched while pending |
| `next_action` | string? | If blocked or complex, what's the literal next step |

**Lifecycle**: 
```
Created → Pending → In Progress → Done
                 ↘ Blocked → (unblocked) → In Progress
                 ↘ Dropped (explicitly abandoned)
```

---

#### PROJECT

**Purpose**: Multi-step work that spans time and has internal state

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `name` | string | Project name |
| `description` | string? | What is this project? Why does it exist? |
| `status` | enum | `active`, `paused`, `completed`, `abandoned` |
| `current_phase` | string? | Human-readable current state |
| `next_step` | string? | The literal next action |
| `blockers` | string[] | What's preventing progress |
| `tasks` | UUID[] | Child tasks |
| `measurements` | UUID[] | Associated measurements |
| `created_at` | datetime | When started |
| `last_touched` | datetime | Last activity |
| `target_date` | date? | When should this be done |
| `stall_days` | int | Computed: days since last_touched while active |

**Key Behavior**: Projects have a **state narrative** — Kiro can explain "where we are" in plain language.

---

#### REMINDER

**Purpose**: Time-triggered prompt

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `message` | string | What to say/show |
| `trigger_time` | datetime | When to fire |
| `recurrence` | object? | Repeat pattern (daily, weekly, etc.) |
| `task_id` | UUID? | Associated task, if any |
| `snoozed_until` | datetime? | If snoozed, when to retry |
| `fired` | boolean | Has this triggered? |
| `acknowledged` | boolean | Did user respond? |

**Key Behavior**: Reminders are **not just alarms**. They carry context and can be conversationally dismissed or rescheduled.

---

#### COMMITMENT

**Purpose**: Promise made to another person (elevated importance)

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `what` | string | What was promised |
| `to_whom` | string | Person's name or relationship |
| `when_made` | datetime | When the promise was made |
| `due_by` | date? | When it's expected |
| `context` | string? | Circumstances of the promise |
| `status` | enum | `pending`, `fulfilled`, `broken`, `renegotiated` |
| `linked_task_id` | UUID? | Task created to fulfill this |

**Key Behavior**: Commitments get **elevated priority** in prompting. Breaking a commitment to another person has social cost; Kiro helps prevent that.

---

#### MEASUREMENT

**Purpose**: Data point for an active project

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `project_id` | UUID | Parent project |
| `label` | string | What is being measured ("shelf width", "screw count") |
| `value` | string | The value (stored as string for flexibility) |
| `unit` | string? | Unit of measurement |
| `timestamp` | datetime | When recorded |
| `supersedes` | UUID? | If this updates a previous measurement |

**Key Behavior**: Measurements are **queryable by voice** ("What's the width of the shelf again?") and **versioned** (can see history of changes).

---

## 3. Capture: Zero-Friction Thought Recording

### 3.1 Capture Philosophy

> **If it takes more than 3 seconds and zero context-switching, capture has failed.**

The capture system must work when the user is:
- Mid-task with hands dirty
- Half-asleep with a sudden thought
- Walking and can't look at a screen
- In the middle of a conversation

### 3.2 Capture Triggers

| Trigger | Example | Handling |
|---------|---------|----------|
| Explicit capture phrase | "Remind me to..." / "I need to..." / "Don't let me forget..." | Direct to EFE |
| Implicit commitment | "I'll call you tomorrow" (to someone) | Detect and confirm |
| Project context | "The measurement is 42 inches" (while project active) | Associate with active project |
| Ambient capture | "Oh, I should get milk" (mid-conversation) | Capture with low confidence, confirm later |

### 3.3 Capture Pipeline

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         CAPTURE PIPELINE                                   │
└────────────────────────────────────────────────────────────────────────────┘

User speaks: "Oh, I need to pick up screws for that shelf project"
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. TRANSCRIPTION                                                           │
│    Raw text captured                                                       │
└────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. INTENT CLASSIFICATION                                                   │
│    Detected: CAPTURE_INTENT (high confidence)                              │
│    Entities: action="pick up screws", project_ref="shelf project"          │
└────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 3. ENTITY RESOLUTION                                                       │
│    "shelf project" → matches Project(id=xyz, name="Garage Shelf Build")    │
└────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 4. CAPTURE RECORD CREATED                                                  │
│    {                                                                       │
│      raw_text: "Oh, I need to pick up screws for that shelf project",      │
│      context_hint: "garage_shelf_build",                                   │
│      suggested_type: "TASK",                                               │
│      suggested_action: "Pick up screws",                                   │
│      suggested_project: "xyz"                                              │
│    }                                                                       │
└────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 5. CONFIRMATION (if needed)                                                │
│    Kiro: "Got it — pick up screws for the garage shelf. Want me to add     │
│           that to your errands list?"                                      │
│    User: "Yeah"                                                            │
└────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 6. ENTITY CREATED                                                          │
│    Task(title="Pick up screws", project_id=xyz, context_tags=["@errands"]) │
│    Capture marked processed=true, converted_to=task_id                     │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Capture Confidence Levels

| Confidence | Behavior |
|------------|----------|
| **High** (>0.9) | Create entity immediately, brief confirmation ("Got it") |
| **Medium** (0.7-0.9) | Create entity, ask confirming question ("Pick up screws for the shelf project?") |
| **Low** (<0.7) | Create Capture record, flag for later triage, acknowledge ("I think I heard something about screws — I'll ask you about it later") |
| **Uncertain** | Create Capture with raw text only, no parsing. Better to have the text than nothing |

### 3.5 Triage Queue

Low-confidence captures accumulate in a **triage queue**. This queue is surfaced:
- During low-activity periods
- As part of end-of-day review (if enabled)
- When user explicitly asks ("What did I tell you earlier?")

**Critical Rule**: The triage queue never grows unboundedly. After 7 days, unprocessed captures are archived with a flag, not deleted.

---

## 4. Task Resumption: "Where Was I?"

### 4.1 The Resumption Problem

After an interruption, an ADHD brain loses:
1. What they were doing
2. Why they were doing it
3. What the next step was
4. The activation energy to restart

Kiro must restore **all four**.

### 4.2 Active Context Tracking

The EFE maintains an **Active Context** object:

```
ActiveContext {
  current_task: Task?              # What user is working on
  current_project: Project?        # Broader project context
  session_start: datetime          # When this work session began
  last_interaction: datetime       # Last user activity
  breadcrumbs: [                   # Recent actions/statements
    { timestamp, action, note }
  ]
  interruption_flag: boolean       # Did user get pulled away?
  pre_interruption_state: {        # Snapshot before interruption
    task, project, breadcrumbs, user_statement
  }
}
```

### 4.3 Interruption Detection

Kiro detects interruptions via:

| Signal | Interpretation |
|--------|----------------|
| User says "hold on" / "one sec" / "be right back" | Explicit interruption |
| Long silence (>5 min) after active work | Implicit interruption |
| User starts talking about unrelated topic | Context switch |
| External trigger (doorbell, phone) if detectable | Environmental interruption |

When interruption is detected:
1. Snapshot current ActiveContext to `pre_interruption_state`
2. Set `interruption_flag = true`
3. Wait for resumption signal

### 4.4 Resumption Flow

**Trigger**: User says "Where was I?" / "What was I doing?" / returns after detected interruption

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         RESUMPTION FLOW                                    │
└────────────────────────────────────────────────────────────────────────────┘

User: "Where was I?"
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. CHECK INTERRUPTION STATE                                                │
│    Is there a pre_interruption_state? → Yes                                │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. RECONSTRUCT CONTEXT                                                     │
│    - What task was active                                                  │
│    - What project it belongs to                                            │
│    - Last 2-3 breadcrumbs                                                  │
│    - What user said they were about to do                                  │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 3. GENERATE RESUMPTION BRIEFING                                            │
│    LLM constructs natural-language summary:                                │
│                                                                            │
│    "You were working on the garage shelf project. You had just measured    │
│     the back panel at 36 inches and were about to cut the plywood.         │
│     You mentioned needing to double-check the blade depth."                │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 4. PROPOSE NEXT ACTION                                                     │
│    "Ready to do that cut, or do you need to check the blade first?"        │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 5. RESTORE ACTIVE CONTEXT                                                  │
│    ActiveContext = pre_interruption_state (updated with current time)      │
│    interruption_flag = false                                               │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.5 Breadcrumb Recording

Breadcrumbs are micro-records of user activity:

| Event | Breadcrumb Example |
|-------|-------------------|
| User states measurement | `{ action: "recorded_measurement", note: "back panel = 36 inches" }` |
| User marks task done | `{ action: "completed_task", note: "Cut side panels" }` |
| User asks question | `{ action: "asked", note: "How many screws do I need?" }` |
| User states intent | `{ action: "stated_intent", note: "About to cut the plywood" }` |

Breadcrumbs are:
- Kept for the current session + 24 hours
- Limited to last 20 entries (rolling)
- Used for resumption, not long-term memory

---

## 5. Stall Detection & Re-Engagement

### 5.1 What "Stalled" Means

A task or project is **stalled** when:
- It was previously active (user engaged with it)
- No activity has occurred for N days
- It is not explicitly paused or completed

**Stall thresholds** (configurable):

| Entity Type | Default Stall Threshold |
|-------------|------------------------|
| Task (standalone) | 3 days |
| Task (in active project) | 5 days |
| Project | 7 days |
| Commitment | 2 days (elevated urgency) |

### 5.2 Stall Detection Process

Runs **daily** as a background job:

```python
# Pseudocode
for task in tasks.where(status="pending" or status="in_progress"):
    days_idle = (now - task.last_touched).days
    if days_idle >= stall_threshold(task):
        create_stall_alert(task)

for project in projects.where(status="active"):
    days_idle = (now - project.last_touched).days
    if days_idle >= stall_threshold(project):
        create_stall_alert(project)
```

### 5.3 Stall Alert Handling

When a stall is detected, Kiro does NOT immediately nag. Instead:

1. **Queue the alert** with a proposed re-engagement
2. **Wait for appropriate moment** (morning briefing, low-activity period)
3. **Surface gently** with smallest viable next action

**Stall Prompt Template**:
```
"Hey, it's been [N days] since you worked on [project/task]. 
The next step was [next_action]. 
Want to [smallest possible action] or should I put this on hold?"
```

**Example**:
```
"It's been a week since you worked on the garage shelf. 
You were about to cut the plywood for the back panel. 
Want to just pull the plywood out today, or should we pause this project?"
```

### 5.4 Smallest Viable Next Action

For stalled items, Kiro proposes the **smallest possible re-entry point**:

| Stalled State | Bad Prompt | Good Prompt |
|---------------|------------|-------------|
| Project with unclear next step | "Work on the shelf project" | "Can you tell me what's blocking the shelf project?" |
| Task that feels big | "Do your taxes" | "Want to just find last year's return? That's step one." |
| Forgotten commitment | "You promised to call Sarah" | "Want me to set a 15-minute reminder to call Sarah today?" |

**Principle**: Reduce activation energy. Don't demand completion; demand one tiny step.

---

## 6. Priority & Scheduling Model

### 6.1 Priority Dimensions

Tasks have two independent priority axes:

**Urgency** (time-sensitivity):
| Level | Meaning |
|-------|---------|
| `immediate` | Must happen today |
| `soon` | Within 2-3 days |
| `normal` | This week |
| `whenever` | No time pressure |

**Importance** (impact):
| Level | Meaning |
|-------|---------|
| `critical` | Serious consequences if dropped |
| `high` | Significant value |
| `normal` | Standard task |
| `low` | Nice to do |

### 6.2 Derived Priority

Combined priority for surfacing decisions:

```
if urgency == immediate OR importance == critical:
    → Surface prominently
elif urgency == soon AND importance >= high:
    → Surface in daily briefing
elif has_due_date AND days_until_due <= 2:
    → Surface as upcoming deadline
else:
    → Available on request, not proactively surfaced
```

### 6.3 Context Tags

Tasks can have context tags that affect when they're surfaced:

| Tag | When to Surface |
|-----|-----------------|
| `@errands` | When user mentions going out, near stores (future: location-based) |
| `@computer` | When at desktop |
| `@phone` | When mobile (future) |
| `@home` | When at home |
| `@low-energy` | When user seems tired or mentions it |
| `@high-focus` | Only when user has dedicated time |

### 6.4 Merge & Surfacing Logic (Natural Reminder Aggregation)

When a context activates (e.g., user says "I'm going to Superstore"), Kiro should surface relevant pending commitments naturally — not as a robotic list dump, but woven into the response.

**Purpose**: Reduce forgotten errands by merging context-bound items into the moment they become relevant.

**Example Exchange**:
```
User: "Hey Kiro, I'm heading to Superstore"
Kiro: "Will do. Don't forget cream, either."
       (merges "buy cream" task tagged with errands:Superstore)
```

#### Merge Rules

| Rule | Behavior |
|------|----------|
| **Merge key** | Items grouped by context key (e.g., `errands:Superstore`, `errands:grocery`) |
| **Status filter** | Only include `pending` or `active` items — never `done` or `cancelled` |
| **Cooldown** | Don't re-surface the same item within cooldown window (default: 4 hours) |
| **Deduplication** | If user already mentioned an item in the same request, omit it from nudge |
| **Ambiguity handling** | If context is ambiguous ("Superstore" vs generic "grocery store"), ask one clarifying question before surfacing |

#### Output Behavior

| Item Count | Response Style |
|------------|----------------|
| 0 | No mention (don't say "nothing to get") |
| 1-2 | Inline nudge: "Don't forget X" or "While you're there, grab X and Y" |
| 3-5 | Brief summary: "You have a few things for there — want me to list them?" |
| 6+ | Offer list mode: "You have quite a list for Superstore. Want me to read it out?" |

User can always request full list explicitly: "What do I need from Superstore?" triggers list mode regardless of count.

#### Done Criteria

- [ ] Context activation returns a stable, deterministic set of relevant commitments
- [ ] Spoken output merges items without duplication
- [ ] Cooldown prevents repetitive nagging within configured window
- [ ] "Read me the whole list" switches to full list mode

#### Non-Goals (Phase 1)

- ❌ GPS/geofence-based triggering (requires hardware integration)
- ❌ Predictive inference ("you might be going to...")
- ❌ Diary or check-in dependency for context activation

---

## 7. Daily Structure

### 7.1 Morning Briefing

**Trigger**: First interaction after configured wake time (default: first interaction after 6 AM)

**Content**:
1. **Today's hard deadlines** (if any)
2. **Commitments to others** (elevated priority)
3. **Stalled items** (max 2, with smallest next action)
4. **Today's suggested focus** (1-2 items from high-priority queue)

**Format** (spoken):
```
"Good morning. Quick rundown:

You have a dentist appointment at 2 PM — that's your only fixed thing today.

You told Mike you'd send him that document by end of week. That's tomorrow.

The garage shelf has been sitting for a week. Want to at least pull the 
plywood out today?

Otherwise, I'd suggest focusing on [task] if you have an hour or two.

That's it. Have a good one."
```

**Constraints**:
- Max 90 seconds spoken
- Max 5 items total
- Always ends with clear exit ("That's it")
- User can interrupt at any point

### 7.2 Midday Check-In (Optional)

**Trigger**: Configurable time (default: disabled)

**Purpose**: Catch-up on morning items, recalibrate

**Content**:
- "How's the day going?"
- If morning items untouched, gentle re-offer
- Check for new urgent items

**Constraint**: Only if user has enabled it. Never default to on.

### 7.3 Evening Review

**Status**: 🔮 **FUTURE PHASE — NOT CORE**

See Section 10 for design notes. Not implemented in v1.

---

## 8. Avoiding Overwhelm: The Anti-Nag Design

### 8.1 Core Principle

> **Kiro must reduce cognitive load, not add to it.**

An ADHD brain is already overwhelmed. Kiro fails if it becomes another source of guilt, pressure, or noise.

### 8.2 Anti-Overwhelm Rules

| Rule | Implementation |
|------|----------------|
| **Max daily prompts** | No more than 5 unsolicited prompts per day (configurable) |
| **Prompt spacing** | Minimum 2 hours between unsolicited prompts |
| **No guilt language** | Never say "you still haven't..." or "you said you would..." |
| **Easy escape** | Every prompt has a one-word dismissal ("later", "skip", "pause") |
| **Batch over interrupt** | Prefer morning briefing over drip-feed interruptions |
| **Silence is valid** | User can say "quiet mode" and get zero prompts until re-enabled |
| **Snooze everything** | "Snooze all reminders for 2 hours" must work |

### 8.3 Prompt Tone

**Bad prompt** (guilt-inducing):
```
"You said you'd call your mom three days ago and you still haven't done it."
```

**Good prompt** (neutral, helpful):
```
"You had a reminder to call your mom. Want to do that now or reschedule?"
```

**Bad prompt** (overwhelming):
```
"You have 12 overdue tasks. Here's the list..."
```

**Good prompt** (focused):
```
"A few things have piled up. Want to pick one to focus on, or should I 
suggest something?"
```

### 8.4 Overwhelm Detection

Kiro should detect when the user is overwhelmed and back off:

| Signal | Response |
|--------|----------|
| Multiple snoozed prompts in a row | Reduce prompt frequency |
| User says "too much" / "overwhelmed" / "stop" | Enter quiet mode |
| Large number of stalled tasks | Don't list them all; offer triage help |
| User sounds frustrated (future: tone detection) | Acknowledge and offer to pause |

---

## 9. Scaffolding Intensity Model

### 9.1 The Spectrum

Scaffolding intensity controls how proactive and persistent Kiro is:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      SCAFFOLDING INTENSITY SPECTRUM                        │
│                                                                             │
│  LIGHT              MODERATE              HEAVY                             │
│    │                    │                    │                              │
│    ▼                    ▼                    ▼                              │
│                                                                             │
│  • Morning briefing   • Morning briefing   • Morning briefing               │
│    only                 + 1-2 check-ins      + multiple check-ins           │
│                                                                             │
│  • Respond when       • Surface stalls     • Proactive task                 │
│    asked                after 7 days         suggestions                    │
│                                                                             │
│  • No stall alerts    • Gentle stall       • Persistent (kind)              │
│                         nudges               re-engagement                  │
│                                                                             │
│  • Max 2 prompts/day  • Max 5 prompts/day  • Max 8 prompts/day              │
│                                                                             │
│  • Snooze = 24 hrs    • Snooze = 4 hrs     • Snooze = 1 hr                  │
│                                                                             │
│  • User drives        • Kiro assists       • Kiro actively                  │
│    everything           when needed          scaffolds                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Intensity Parameters

| Parameter | Light | Moderate | Heavy |
|-----------|-------|----------|-------|
| `max_daily_prompts` | 2 | 5 | 8 |
| `min_prompt_spacing_hours` | 4 | 2 | 1 |
| `stall_threshold_days` | 14 | 7 | 3 |
| `stall_resurface_days` | 7 | 3 | 1 |
| `default_snooze_hours` | 24 | 4 | 1 |
| `morning_briefing` | enabled | enabled | enabled |
| `midday_checkin` | disabled | disabled | enabled |
| `offer_suggestions` | never | on_request | proactive |
| `commitment_urgency_boost` | 1.0x | 1.5x | 2.0x |

### 9.3 Intensity Selection

**Initial setting**: User chooses during setup, defaults to **Moderate**

**Runtime adjustment**: User can say:
- "Be more hands-off" → decrease intensity
- "I need more help staying on track" → increase intensity
- "Light mode" / "Heavy mode" → direct selection

### 9.4 Future: Adaptive Intensity

**Status**: 🔮 **FUTURE PHASE — NOT IN V1**

The system should eventually learn optimal intensity from signals:

| Signal | Interpretation |
|--------|----------------|
| Prompts frequently snoozed | Intensity too high |
| Prompts frequently acted on | Intensity appropriate |
| Tasks completed after stall alerts | Stall detection valuable |
| User explicitly says "too much" | Intensity too high |
| User explicitly says "remind me more" | Intensity too low |
| High task completion rate | Current settings working |
| Many dropped tasks | May need more scaffolding OR less (depends on cause) |

**Learning approach** (future):
- Track prompt → response patterns
- Weekly micro-adjustment (±10% on parameters)
- Never auto-adjust more than one level
- Always allow manual override

---

## 10. End-of-Day Reflection

### Status: 🔮 **FUTURE PHASE — EXPLICITLY NON-CORE**

This feature is valuable but not required for v1. It is documented here for architectural consideration.

### 10.1 Purpose

An optional evening check-in to:
1. Close open loops
2. Acknowledge accomplishments (dopamine reward)
3. Capture tomorrow's priorities
4. Calibrate scaffolding intensity

### 10.2 Proposed Flow

**Trigger**: User-configured time (e.g., 9 PM), or on-demand ("Let's do the daily review")

```
Kiro: "Want to do a quick end-of-day wrap-up?"

User: "Sure"

Kiro: "Okay. First — you got three things done today: [list]. Nice work.

       Two things are still open: [task A] and [task B]. 
       Want to carry those to tomorrow or drop them?

       Anything on your mind for tomorrow you want to capture now?

       Last thing — how's the reminder frequency feeling? Too much, 
       too little, or about right?"
```

### 10.3 Calibration Questions

The reflection can include scaffolding calibration:

| Question | Maps To |
|----------|---------|
| "Were reminders helpful today?" | General intensity |
| "Did I interrupt too much?" | `max_daily_prompts`, `min_prompt_spacing` |
| "Did anything slip through the cracks?" | `stall_threshold`, coverage |
| "Feeling overwhelmed or manageable?" | Overall adjustment direction |

### 10.4 Why Non-Core

- Requires consistent user participation (hard for ADHD)
- Morning briefing provides most value
- Can feel like homework/obligation
- Risk of becoming a guilt source if skipped

**Decision**: Design architecture to support it, but do not implement or enable in v1.

---

## 11. EFE Internal Architecture

### 11.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    EXECUTIVE FUNCTION ENGINE                                │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      CAPTURE PROCESSOR                              │   │
│  │  • Receives intents from Intent Router                              │   │
│  │  • Parses into entities                                             │   │
│  │  • Manages triage queue                                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      ENTITY STORE                                   │   │
│  │  • Tasks, Projects, Reminders, Commitments, Measurements            │   │
│  │  • SQLite backend                                                   │   │
│  │  • Query interface for other components                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐       │
│  │  CONTEXT TRACKER  │  │  STALL DETECTOR   │  │  SCHEDULER        │       │
│  │                   │  │                   │  │                   │       │
│  │  • Active context │  │  • Daily scan     │  │  • Time triggers  │       │
│  │  • Breadcrumbs    │  │  • Stall alerts   │  │  • Morning brief  │       │
│  │  • Interruption   │  │  • Re-engagement  │  │  • Reminders      │       │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘       │
│           │                      │                      │                   │
│           └──────────────────────┼──────────────────────┘                   │
│                                  ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      PROMPT GENERATOR                               │   │
│  │  • Constructs prompts from entity state                             │   │
│  │  • Applies scaffolding intensity                                    │   │
│  │  • Respects anti-overwhelm rules                                    │   │
│  │  • Routes to Conversation Manager for delivery                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      SCAFFOLDING CONFIG                             │   │
│  │  • Intensity settings                                               │   │
│  │  • Per-user parameters                                              │   │
│  │  • Future: learning state                                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 11.2 EFE API Surface

| Method | Description |
|--------|-------------|
| `capture(raw_text, context) → Capture` | Create new capture |
| `create_task(title, ...) → Task` | Create task directly |
| `create_project(name, ...) → Project` | Create project |
| `create_reminder(message, time, ...) → Reminder` | Create reminder |
| `record_measurement(project_id, label, value) → Measurement` | Store measurement |
| `get_active_context() → ActiveContext` | Current working state |
| `get_resumption_briefing() → string` | "Where was I?" response |
| `get_morning_briefing() → string` | Daily briefing content |
| `get_stalled_items() → [Entity]` | Items needing attention |
| `query_tasks(filters) → [Task]` | Search/filter tasks |
| `query_projects(filters) → [Project]` | Search/filter projects |
| `get_measurements(project_id) → [Measurement]` | Project measurements |
| `mark_done(entity_id)` | Complete a task |
| `snooze(entity_id, duration)` | Delay reminder/prompt |
| `set_intensity(level)` | Adjust scaffolding |

### 11.3 Event Emissions

The EFE emits events for other subsystems:

| Event | Payload | Consumers |
|-------|---------|-----------|
| `efe.task.created` | Task object | Memory (for episodic record) |
| `efe.task.completed` | Task object | Memory, Stats (future) |
| `efe.reminder.due` | Reminder object | Audio I/O (for prompt) |
| `efe.briefing.ready` | Briefing content | Conversation Manager |
| `efe.stall.detected` | Entity + suggested action | Prompt queue |

---

## 12. Summary

The Executive Function Engine is Kiro's core value proposition. It must:

1. **Capture effortlessly** — Voice-first, low-friction, fail-open
2. **Remember everything** — Never lose user intent
3. **Restore context** — Answer "Where was I?" instantly
4. **Detect drift** — Notice stalled work, propose re-entry
5. **Scaffold without nagging** — Helpful, not guilt-inducing
6. **Adapt intensity** — Tunable from light to heavy support

The EFE is not a todo app. It is a **cognitive extension** that holds what the brain cannot.

---

*Next: [04-persona-system.md](04-persona-system.md)*
