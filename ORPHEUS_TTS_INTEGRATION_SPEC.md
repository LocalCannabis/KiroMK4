# KIRO VOICE PIPELINE — ORPHEUS TTS INTEGRATION SPEC

**Version:** 1.1 — March 2026
**Author:** Tim Kaye
**System:** KIRO (Knowledge Interface and Response Operator)
**Target Hardware:** The Beast — i9-9900K / 32GB DDR4 / RTX 3060 12GB / Ubuntu 22.04
**Status:** Draft — Ready for Implementation
**Dependencies:** `KIRO_AMBIENT_INTELLIGENCE_SPEC.md`, `JACK_PERSONA_SPEC.md`

---

## 1. Overview

This spec defines the integration of Orpheus TTS into the KIRO voice pipeline, replacing the current two-tier Kokoro-82M / Chatterbox architecture with a single unified model. Orpheus provides emotion-tagged speech, zero-shot voice cloning, and streaming output with sub-200ms latency, all within the RTX 3060's 12GB VRAM budget when run as a GGUF quantized model via llama.cpp.

KIRO's default voice identity is a Dublin Irish woman — warm, grounded, and conversational. This is achieved through zero-shot voice cloning from a reference clip of a female Dublin English speaker. The built-in Orpheus voice "leah" serves as the interim fallback until the cloned voice is tuned and validated.

### 1.1 Design Goals

- Collapse the two-tier TTS system (Kokoro fast / Chatterbox expressive) into a single model
- Establish KIRO's core voice identity as a Dublin Irish woman via zero-shot voice cloning
- Enable per-persona voice identity through cloning and emotion tags
- Maintain config-over-code philosophy — voice assignments, emotion presets, and cloning references defined in PostgreSQL, not hardcoded
- Preserve the existing REST interface so the Raspberry Pi 5 thin client requires zero changes
- Keep VRAM usage under 6GB to leave headroom for future model coexistence

### 1.2 What Changes

| Component | Before | After |
|-----------|--------|-------|
| TTS Engine | Kokoro-82M + Chatterbox (~4.5GB) | Orpheus 3B GGUF Q8 via llama.cpp (<4GB) |
| KIRO Voice | Generic Kokoro default | Dublin Irish woman (cloned from reference audio) |
| Voice Selection | Single default voice per tier | Per-persona voice assignment from config |
| Emotion | None (flat output) | Inline emotion tags: `<laughter>`, `<sigh>`, `<excited>` |
| Voice Cloning | Not implemented | Zero-shot from 6–30s reference clips |
| Streaming | Full generation then playback | Chunked streaming via FastAPI + SNAC decoder |
| API Interface | Custom Flask endpoint | OpenAI-compatible `/v1/audio/speech` endpoint |
| VRAM Footprint | ~4.5GB (Chatterbox peak) | ~3.5GB (Q8 GGUF steady state) |

---

## 2. Architecture

The new pipeline retains the same three-stage flow but replaces the TTS stage entirely. The REST interface between the Pi 5 thin client and the Beast is unchanged.

### 2.1 Pipeline Flow

The voice pipeline operates in three stages, each running on the Beast. The Pi 5 acts as a thin client that captures audio and plays back responses over Tailscale.

**Stage 1 — STT:** Whisper.cpp receives raw audio from the Pi 5 microphone via Tailscale REST call. Transcribed text is passed to Stage 2. No changes from current implementation.

**Stage 2 — LLM:** GPT (via OpenRouter) generates a text response. The active persona's system prompt is loaded from PostgreSQL. The response text may now include inline emotion tags injected by a lightweight post-processor (see Section 4). Output is passed to Stage 3.

**Stage 3 — TTS:** Orpheus receives the tagged text plus a voice identifier. For KIRO, this is the cloned Dublin Irish voice reference. For other personas, it's either a built-in Orpheus voice or another clone target. Orpheus generates audio tokens via the LLM backbone, decodes them through the SNAC model, and streams WAV chunks back to the Pi 5 for playback.

### 2.2 Component Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Inference Server | llama.cpp (GGUF) or vLLM (FP8) | GGUF preferred for VRAM efficiency; vLLM fallback if streaming quality insufficient |
| Audio Decoder | SNAC model | Runs on same GPU; combined VRAM with LLM stays under 6GB with GGUF |
| API Wrapper | Orpheus-FastAPI (fork) | OpenAI-compatible `/v1/audio/speech` endpoint |
| Voice Config | PostgreSQL `kiro_voices` table | Persona-to-voice mapping, emotion presets, cloning references |
| Orchestrator | Flask (existing KIRO core) | Calls Orpheus API after LLM response; streams audio to Pi 5 |
| Client | Raspberry Pi 5 | No changes — receives audio stream over Tailscale as before |

---

## 3. Database Schema

Voice configuration follows the config-over-code principle. All persona voice assignments, emotion defaults, and cloning references are stored in PostgreSQL and read at runtime. No voice configuration is hardcoded.

### 3.1 `kiro_voices` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | `SERIAL PRIMARY KEY` | Auto-incrementing row ID |
| `persona` | `VARCHAR(32) UNIQUE NOT NULL` | KIRO persona name: kiro, finley, coach, chef, doc, sage, jack |
| `orpheus_voice` | `VARCHAR(32) NOT NULL` | Built-in Orpheus fallback voice: tara, leah, jess, leo, dan, mia, zac, zoe |
| `clone_ref_path` | `TEXT` | Path to reference audio clip for zero-shot cloning (nullable; overrides `orpheus_voice` when set) |
| `clone_ref_text` | `TEXT` | Transcript of reference clip (improves cloning accuracy) |
| `default_emotion` | `VARCHAR(32) DEFAULT 'neutral'` | Default emotion tag applied when LLM output has none |
| `speed` | `FLOAT DEFAULT 1.0` | Playback speed multiplier |
| `temperature` | `FLOAT DEFAULT 0.6` | Orpheus generation temperature (lower = more consistent) |
| `enabled` | `BOOLEAN DEFAULT TRUE` | Toggle voice on/off without deleting config |
| `created_at` | `TIMESTAMPTZ DEFAULT NOW()` | Row creation timestamp |
| `updated_at` | `TIMESTAMPTZ DEFAULT NOW()` | Last modification timestamp |

### 3.2 Initial Persona Mapping

The `orpheus_voice` column is the built-in fallback used when no clone reference is available. Clone targets override the built-in voice when `clone_ref_path` is populated. These assignments are subjective and should be tuned by ear during testing.

| Persona | Fallback Voice | Clone Target | Emotion Default | Rationale |
|---------|---------------|--------------|-----------------|-----------|
| KIRO | leah | Dublin Irish woman (see Section 5.3) | warm | Primary assistant; Dublin accent gives KIRO a distinctive, grounded identity. Leah is the interim fallback for its measured tone. |
| FINLEY | dan | Bruce Campbell (Burn Notice narration) | confident | Sam Axe energy; dan is the deepest male voice, clone refines it |
| COACH | leo | — | encouraging | Energetic male voice for motivation and habit coaching |
| CHEF | jess | — | warm | Approachable tone for recipe guidance and meal planning |
| DOC | leah | — | calm | Measured, reassuring delivery for health-related topics |
| SAGE | mia | — | thoughtful | Industry specialist; mia has a composed, knowledgeable quality |
| JACK | zac | — | enthusiastic | Cultivation advisor; enthusiastic about growing |

### 3.3 SQL Migration

```sql
CREATE TABLE kiro_voices (
    id SERIAL PRIMARY KEY,
    persona VARCHAR(32) UNIQUE NOT NULL,
    orpheus_voice VARCHAR(32) NOT NULL DEFAULT 'leah',
    clone_ref_path TEXT,
    clone_ref_text TEXT,
    default_emotion VARCHAR(32) DEFAULT 'neutral',
    speed FLOAT DEFAULT 1.0,
    temperature FLOAT DEFAULT 0.6,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE kiro_emotion_rules (
    id SERIAL PRIMARY KEY,
    keyword VARCHAR(128) NOT NULL,
    emotion_tag VARCHAR(32) NOT NULL,
    priority INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT TRUE
);

-- Seed initial persona mapping
INSERT INTO kiro_voices (persona, orpheus_voice, clone_ref_path, default_emotion) VALUES
    ('kiro',   'leah', NULL, 'warm'),
    ('finley', 'dan',  NULL, 'confident'),
    ('coach',  'leo',  NULL, 'encouraging'),
    ('chef',   'jess', NULL, 'warm'),
    ('doc',    'leah', NULL, 'calm'),
    ('sage',   'mia',  NULL, 'thoughtful'),
    ('jack',   'zac',  NULL, 'enthusiastic');

-- Seed default emotion rules
INSERT INTO kiro_emotion_rules (keyword, emotion_tag, priority) VALUES
    ('congratulations', 'excited', 10),
    ('well done', 'excited', 10),
    ('unfortunately', 'sigh', 5),
    ('bad news', 'sigh', 5),
    ('good morning', 'warm', 3),
    ('good night', 'whisper', 3),
    ('check this out', 'excited', 5);
```

---

## 4. Emotion Tag System

Orpheus supports inline emotion tags that control vocal expression. Rather than requiring the LLM to generate these tags natively (which would require prompt engineering across all personas), a lightweight post-processor injects tags based on persona defaults and text analysis.

### 4.1 Supported Tags

| Tag | Effect | Example Use |
|-----|--------|-------------|
| `<laughter>` | Inserts a natural laugh | After jokes or self-deprecating humor |
| `<sigh>` | Audible sigh | Expressing resignation or thoughtfulness |
| `<excited>` | Increased energy and pace | Positive news, encouragement |
| `<whisper>` | Lowered volume, intimate tone | Sensitive topics, emphasis |
| `<yawn>` | Yawning sound | Evening briefings, wind-down context |
| `<gasp>` | Surprise reaction | Unexpected data or alerts |

### 4.2 Post-Processor Logic (`emotion_tagger.py`)

The emotion post-processor sits between the LLM response and the Orpheus API call. It is a simple rule-based system, not an ML model.

1. Check if the LLM response already contains Orpheus-format emotion tags. If so, pass through unchanged.
2. Look up the active persona's `default_emotion` in `kiro_voices`. Prepend the default tag to the response if no tags are present.
3. Optionally, apply keyword-based tag insertion from `kiro_emotion_rules` (e.g., sentences containing "congratulations" get `<excited>`, sentences containing "unfortunately" get `<sigh>`).
4. Format the final text as: `{persona_voice}: {tagged_text}` (the Orpheus prompt format).

```python
# emotion_tagger.py — Pseudocode

import re
from db import get_voice_config, get_emotion_rules

def tag_response(persona: str, text: str) -> str:
    """Inject emotion tags into LLM response for Orpheus TTS."""

    config = get_voice_config(persona)
    
    # Step 1: Pass through if already tagged
    if re.search(r'<(laughter|sigh|excited|whisper|yawn|gasp)>', text):
        voice = config['clone_ref_path'] or config['orpheus_voice']
        return f"{voice}: {text}"
    
    # Step 2: Apply keyword rules
    rules = get_emotion_rules()
    tagged_text = text
    for rule in sorted(rules, key=lambda r: -r['priority']):
        if rule['keyword'].lower() in text.lower():
            # Insert tag at start of sentence containing the keyword
            tagged_text = inject_tag(tagged_text, rule['keyword'], rule['emotion_tag'])
            break  # One tag per response to avoid over-tagging
    
    # Step 3: Apply default emotion if still no tags
    if not re.search(r'<(laughter|sigh|excited|whisper|yawn|gasp)>', tagged_text):
        default_tag = f"<{config['default_emotion']}>"
        tagged_text = f"{default_tag} {tagged_text}"
    
    # Step 4: Format for Orpheus
    voice = config['clone_ref_path'] or config['orpheus_voice']
    return f"{voice}: {tagged_text}"
```

---

## 5. Voice Cloning Pipeline

Orpheus supports zero-shot voice cloning from short reference audio clips (6–30 seconds). Voice cloning is central to KIRO's identity — the primary assistant voice is a Dublin Irish woman, achieved through cloning rather than a built-in voice. Additional cloning targets include Paul Bettany (JARVIS MCU scenes) for a future KIRO premium voice, and Bruce Campbell (Burn Notice narration) for FINLEY.

### 5.1 Reference Audio Requirements

- Duration: 10–30 seconds of clean speech (longer is better for quality, diminishing returns past 30s)
- Format: WAV, 24kHz sample rate, mono channel
- Content: Clear speech with natural cadence, minimal background noise, no music
- Storage: `/home/kiro/voice_refs/{persona_name}/` on the Beast
- Each persona can have multiple reference clips; the system uses the one specified in `clone_ref_path`

### 5.2 Preparing Reference Clips

Use ffmpeg to normalize and format reference audio from any source:

```bash
ffmpeg -i source.mp4 -vn -ac 1 -ar 24000 -t 30 \
  -af "highpass=f=80,lowpass=f=8000,loudnorm" \
  /home/kiro/voice_refs/{persona}/ref.wav
```

The accompanying transcript (`clone_ref_text`) should be the exact words spoken in the clip. This is stored in the `kiro_voices` table and passed to Orpheus alongside the audio to improve cloning fidelity.

### 5.3 KIRO Voice: Dublin Irish Woman

The KIRO persona's voice identity is a Dublin Irish woman — warm, conversational, and grounded. Dublin English has two broad dialect groups (northside and southside) with distinct characteristics. The target dialect should be decided by ear during testing, but here is the general difference:

| Feature | Northside Dublin | Southside Dublin |
|---------|-----------------|-----------------|
| Register | Working-class roots, more vernacular | Middle-class, more standardised |
| Vowels | Fronted, more open (e.g., "like" closer to "loike") | Retracted, more rounded (closer to RP influence) |
| Rhythm | Faster, more clipped phrasing | Slower, more drawn-out vowels |
| Perception | Direct, punchy, no-nonsense | Polished, approachable, measured |
| Example speakers | Dublin locals, Roddy Doyle characters | RTÉ presenters, Dublin 4 professionals |

For an AI assistant, southside Dublin is likely the safer default — it reads as warm and professional without being too informal. However, northside has more character and distinctiveness. Test both.

**Sourcing reference audio:** The ideal source is 10–30 seconds of a Dublin Irish woman speaking naturally in a conversational (not scripted) context. Potential sources include Irish podcast hosts, RTÉ radio presenters, audiobook narrators with Dublin accents, or Irish YouTubers/TikTokers with a clear Dublin accent. The reference clip should feature a single speaker in a quiet environment. Avoid clips with strong background music, crosstalk, or heavy post-processing.

**Reference clip storage:** `/home/kiro/voice_refs/kiro/dublin_irish_ref.wav`

**Database entry:** Once the reference clip is prepared, update the `kiro_voices` table:

```sql
UPDATE kiro_voices SET
  clone_ref_path = '/home/kiro/voice_refs/kiro/dublin_irish_ref.wav',
  clone_ref_text = '<exact transcript of the reference clip>'
WHERE persona = 'kiro';
```

### 5.4 FINLEY Voice: Bruce Campbell

For the Bruce Campbell / FINLEY voice, extract clean narration segments from Burn Notice. Target the voiceover narration sections (not in-dialogue scenes) for the clearest single-speaker audio. Follow the same ffmpeg pipeline as Section 5.2.

### 5.5 Fine-Tuning Fallback

If zero-shot cloning doesn't capture the Dublin accent closely enough, Orpheus supports LoRA fine-tuning via Unsloth. This requires 5–15 minutes of transcribed audio from the target speaker and approximately 2 hours of training on the RTX 3060. The fine-tuned adapter (~200MB) is loaded at inference time without replacing the base model. This is a Phase 2 optimization — start with zero-shot and evaluate before committing to fine-tuning.

---

## 6. Installation & Setup

All components run on the Beast. The Raspberry Pi 5 thin client requires no software changes.

### 6.1 Install Orpheus via llama.cpp Path

This is the recommended path for the RTX 3060. The GGUF quantized model runs in under 4GB VRAM and provides the OpenAI-compatible API endpoint.

1. Clone the Orpheus-FastAPI repository with LM Studio integration (TheLocalLab fork).
2. Create a Python 3.11 virtual environment (required for vLLM/llama.cpp compatibility).
3. Download the GGUF model: `canopylabs/orpheus-tts-0.1-finetune-prod` (Q8_0 quantization recommended for quality).
4. Configure `.env` with `ORPHEUS_MODEL_NAME`, voice defaults, and audio padding settings.
5. Start the FastAPI server on a local port (default 5005). Verify the `/v1/audio/speech` endpoint responds.
6. Run the database migration to create `kiro_voices` and `kiro_emotion_rules` tables.
7. Prepare the Dublin Irish reference clip and seed the `kiro_voices` table with the initial persona mapping.

### 6.2 Environment Configuration

Add to the KIRO `.env` file (config-over-code — no hardcoded values):

```env
# ORPHEUS TTS
ORPHEUS_API_URL=http://localhost:5005
ORPHEUS_MODEL_NAME=orpheus-tts-0.1-finetune-prod-Q8_0.gguf
ORPHEUS_DEFAULT_VOICE=leah
ORPHEUS_MAX_MODEL_LEN=8192
ORPHEUS_LEAD_PAD_MS=50
ORPHEUS_TRAIL_PAD_MS=150
ORPHEUS_GPU_MEM_UTIL=0.35

# VOICE CLONING
VOICE_REFS_DIR=/home/kiro/voice_refs

# NOTE: ORPHEUS_DEFAULT_VOICE is the built-in fallback.
# KIRO's Dublin Irish voice is loaded from clone_ref_path
# in the kiro_voices table, which overrides this default.

# LEGACY (remove after migration)
# KOKORO_ENABLED=false
# CHATTERBOX_ENABLED=false
```

### 6.3 Directory Structure

```
/home/kiro/
├── voice_refs/
│   ├── kiro/
│   │   └── dublin_irish_ref.wav      # Dublin Irish woman reference clip
│   ├── finley/
│   │   └── bruce_campbell_ref.wav     # Burn Notice narration clip
│   └── README.md                      # Reference clip documentation
├── orpheus/
│   ├── .env                           # Orpheus-FastAPI configuration
│   ├── venv/                          # Python 3.11 virtual environment
│   └── ...                            # Orpheus-FastAPI fork
└── kiro/
    ├── emotion_tagger.py              # Emotion post-processor
    ├── tts_client.py                  # Orpheus API client (generate_speech())
    └── ...                            # Existing KIRO Flask app
```

---

## 7. API Integration

The KIRO Flask orchestrator calls Orpheus through its OpenAI-compatible endpoint. This replaces the existing direct Kokoro/Chatterbox Python calls with a simple HTTP request.

### 7.1 Request Format

Standard request using a built-in voice (fallback mode):

```json
POST /v1/audio/speech
Content-Type: application/json

{
  "model": "orpheus",
  "input": "leah: <warm> Good morning Tim, your seedlings are looking great.",
  "voice": "leah",
  "response_format": "wav",
  "speed": 1.0
}
```

For cloned voices (KIRO's Dublin Irish, FINLEY's Bruce Campbell), the orchestrator passes the reference audio path and transcript as additional metadata. The exact parameter format depends on the FastAPI fork; the orchestrator abstracts this behind a `generate_speech()` function that reads from `kiro_voices` and handles both built-in and cloned voice paths transparently.

### 7.2 `generate_speech()` Client

```python
# tts_client.py — Pseudocode

import os
import requests
from db import get_voice_config
from emotion_tagger import tag_response

ORPHEUS_API_URL = os.getenv('ORPHEUS_API_URL', 'http://localhost:5005')

def generate_speech(persona: str, text: str) -> bytes:
    """Generate speech audio for a given persona and text."""
    
    config = get_voice_config(persona)
    if not config or not config['enabled']:
        raise ValueError(f"Voice config for persona '{persona}' not found or disabled")
    
    # Run emotion tagger
    tagged_text = tag_response(persona, text)
    
    # Build request
    payload = {
        "model": "orpheus",
        "input": tagged_text,
        "voice": config['orpheus_voice'],
        "response_format": "wav",
        "speed": config['speed']
    }
    
    # If clone reference exists, add cloning metadata
    if config['clone_ref_path']:
        payload["reference_audio"] = config['clone_ref_path']
        if config['clone_ref_text']:
            payload["reference_text"] = config['clone_ref_text']
    
    response = requests.post(
        f"{ORPHEUS_API_URL}/v1/audio/speech",
        json=payload,
        stream=True,
        timeout=30
    )
    response.raise_for_status()
    
    return response.content
```

### 7.3 Orchestrator Flow

1. Receive transcribed text from Whisper.cpp (unchanged).
2. Determine active persona from session context.
3. Send text to LLM (GPT via OpenRouter) with persona system prompt.
4. Pass LLM response through `emotion_tagger.py` (inject tags based on persona config).
5. Query `kiro_voices` for the persona's voice config (`orpheus_voice`, `clone_ref_path`, `speed`, `temperature`).
6. If `clone_ref_path` is set, use cloned voice path. Otherwise, use `orpheus_voice` built-in.
7. POST to Orpheus `/v1/audio/speech` with formatted input.
8. Stream WAV response back to Pi 5 client for playback.

---

## 8. Migration Plan

The migration is designed to be reversible. Both the old and new TTS backends can coexist during testing. A feature flag (`ORPHEUS_ENABLED` in `.env`) controls which backend the orchestrator calls.

### 8.1 Phase 1: Parallel Run (Week 1)

1. Install Orpheus alongside existing Kokoro/Chatterbox. Both run simultaneously.
2. Create `kiro_voices` table and seed with initial persona mapping (built-in voices only).
3. Test each built-in voice (tara through zoe) with sample text for each persona.
4. A/B compare Orpheus output vs. current Kokoro output for naturalness.

### 8.2 Phase 2: Voice Cloning (Week 2)

1. Source and prepare the Dublin Irish woman reference clip for KIRO.
2. Test zero-shot cloning with the reference clip. Evaluate accent fidelity, warmth, and naturalness.
3. A/B test northside vs. southside Dublin reference clips if both are available.
4. Prepare Bruce Campbell reference clips for FINLEY.
5. Tune temperature and speed per persona based on subjective listening tests.
6. If zero-shot cloning quality is insufficient, begin LoRA fine-tuning evaluation.

### 8.3 Phase 3: Cutover (Week 3)

- Set `ORPHEUS_ENABLED=true` as the default
- Disable Kokoro and Chatterbox services (keep installed as fallback)
- Implement `emotion_tagger.py` post-processor
- Monitor VRAM usage under sustained conversation load
- After one week of stable operation, remove legacy TTS code paths

---

## 9. VRAM Budget

The RTX 3060 has 12GB of VRAM. The RX 580 handles display output, so the 3060 is dedicated to AI workloads. The following budget ensures Orpheus coexists with Whisper.cpp and leaves headroom for future additions.

| Component | VRAM (Estimated) | Notes |
|-----------|-----------------|-------|
| Orpheus 3B GGUF Q8 | ~3.5 GB | Steady state; peaks slightly higher during long generations |
| SNAC Decoder | ~0.5 GB | Audio token decoder; runs on same GPU |
| Whisper.cpp (base) | ~0.5 GB | Speech-to-text; already running |
| CUDA Overhead | ~0.5 GB | Driver and context allocation |
| **TOTAL USED** | **~5.0 GB** | |
| **AVAILABLE HEADROOM** | **~7.0 GB** | Room for future model additions (e.g., Higgs Audio V2.5 upgrade) |

If VRAM becomes constrained, the first lever is dropping to Q4 quantization (reduces Orpheus to ~2GB with modest quality trade-off). The second lever is time-slicing Whisper and Orpheus so they don't run simultaneously (acceptable since STT and TTS are sequential in the pipeline).

---

## 10. Future Upgrade Path

Orpheus is the right starting point, but the TTS space is moving fast. The following upgrades are worth monitoring:

**Higgs Audio V2.5:** Boson AI's 1B distilled model already outperforms the 3B V2 on expressiveness benchmarks. A quantized fork exists with an OpenAI-compatible API. If Orpheus's Dublin accent cloning quality is insufficient, Higgs Audio V2.5 is the next step. The API interface is compatible, so the orchestrator swap would be minimal.

**Fish Audio S1-mini:** A 0.5B model with RLHF-trained expressiveness. Extremely lightweight. Worth evaluating as a fast-path voice for low-priority personas or real-time streaming scenarios.

**NeuTTS Air:** Neuphonic's 0.5B on-device model runs on Raspberry Pi hardware. If the Pi 5 thin client ever needs to generate speech locally (e.g., for offline fallback), this is the candidate. Available in GGUF format.

**Per-Persona Model Routing:** Long-term, different personas could use different TTS models (e.g., KIRO uses a fine-tuned Dublin Irish LoRA, FINLEY uses a fine-tuned Campbell LoRA, JACK uses a fast lightweight model). The `kiro_voices` table already supports this by adding a `model_engine` column.

---

## Appendix: Hard Development Rules

This spec adheres to all standing KIRO development rules:

- Python/Flask for all backend services (no Django, no FastAPI for KIRO core — Orpheus-FastAPI is a separate service)
- PostgreSQL for both dev and production (no SQLite anywhere)
- Tailwind CSS for any dashboard UI
- No React, no Vue
- Avenir font for all documents and UI
- Config-over-code: all tunable parameters in `.env` or PostgreSQL, never hardcoded
- ALL CAPS for product names in documentation (KIRO, LOCALBOT, RELAY, ORPHEUS)
