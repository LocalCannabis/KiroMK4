# Kiro: Hardware & Deployment Roadmap

**Version**: 1.0 | **Date**: January 2026 | **Status**: Canonical Specification

---

## 1. Roadmap Overview

Kiro is designed to run across a spectrum of hardware, from a powerful desktop to a pocket-sized device. Each phase represents a deployment target with different capabilities and constraints.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DEPLOYMENT SPECTRUM                                  │
│                                                                             │
│  PHASE 1              PHASE 2              PHASE 3              PHASE 4     │
│  Desktop Daemon       Cloud/Headless       Raspberry Pi         Mobile      │
│                                                                             │
│  ┌─────────┐          ┌─────────┐          ┌─────────┐          ┌─────────┐│
│  │   🖥️    │          │   ☁️    │          │   🍓    │          │   📱    ││
│  │ Beast   │          │  GCP    │          │  Pi 5   │          │ Custom  ││
│  │ i9+3060 │          │ Server  │          │ Portable│          │ Device  ││
│  └─────────┘          └─────────┘          └─────────┘          └─────────┘│
│                                                                             │
│  Full capability      No local audio       Limited local        Battery +   │
│  Local + cloud        Client required      Cloud-dependent      Cellular    │
│                                                                             │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
│  NOW                  6-12 months          12-18 months         Future      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase 1: Desktop Daemon ("The Beast")

### 2.1 Hardware Profile

| Component | Specification |
|-----------|---------------|
| **CPU** | Intel i9 (high single-thread + multi-core) |
| **GPU** | NVIDIA RTX 3060 (12GB VRAM) |
| **RAM** | Assumed 32GB+ |
| **Storage** | SSD, ample space |
| **OS** | Ubuntu-based Linux |
| **Audio** | USB microphone, speakers/headphones |
| **Network** | Reliable broadband |
| **Power** | Always-on (desktop) |

### 2.2 Capability Matrix

| Capability | Status | Notes |
|------------|--------|-------|
| Wake word detection | ✅ Full | Local, always-on |
| Speech-to-text | ✅ Full | Cloud (Whisper API) or local (whisper.cpp on GPU) |
| Text-to-speech | ✅ Full | Cloud or local (Piper) |
| LLM inference | ✅ Full | Cloud primary, local fallback possible (llama.cpp on GPU) |
| Memory system | ✅ Full | SQLite, no constraints |
| EFE | ✅ Full | All features |
| Persona system | ✅ Full | All features |
| Proactive prompts | ✅ Full | Morning briefing, stall detection |
| Local embedding | ✅ Full | GPU-accelerated (future vector search) |

### 2.3 Architecture Notes

**This is the reference platform.** All features are designed for Phase 1 first.

- **Daemon runs continuously** — systemd service, auto-restart on crash
- **GPU available** — Can run local inference if cloud is unavailable or for privacy
- **No power constraints** — Can keep microphone hot, run background jobs freely
- **SQLite is fine** — Single-user, single-process, no concurrency issues

### 2.4 Limitations

- **Not portable** — User must be physically present at desktop
- **Single location** — No mobile access
- **Dependent on home network** — If internet drops, cloud features degrade

---

## 3. Phase 2: Headless / Cloud-Assisted Instance

### 3.1 Deployment Profile

| Aspect | Specification |
|--------|---------------|
| **Platform** | GCP Compute Engine (or similar) |
| **Instance type** | e2-medium or n1-standard-2 (2 vCPU, 4-8GB RAM) |
| **GPU** | None (cost prohibitive for always-on) |
| **Storage** | Persistent disk, Cloud SQL (PostgreSQL) |
| **Network** | GCP internal + external API access |
| **Audio** | None locally — requires client device |

### 3.2 Capability Matrix

| Capability | Status | Notes |
|------------|--------|-------|
| Wake word detection | ❌ N/A | No local audio — handled by client |
| Speech-to-text | ⚠️ Client-side | Client sends audio or text |
| Text-to-speech | ⚠️ Client-side | Server sends text, client synthesizes |
| LLM inference | ✅ Full | Cloud APIs (Claude, OpenAI) |
| Memory system | ✅ Full | PostgreSQL (Cloud SQL) |
| EFE | ✅ Full | All features |
| Persona system | ✅ Full | All features |
| Proactive prompts | ⚠️ Push-based | Server pushes to client app |
| Local embedding | ⚠️ Limited | CPU-only, slower |

### 3.3 Client Requirements

Phase 2 **requires a client device** to handle audio I/O:

```
┌─────────────────┐         ┌─────────────────┐
│  CLIENT DEVICE  │ ◄─────► │  CLOUD SERVER   │
│  (Phone/Laptop) │   API   │  (Kiro Core)    │
│                 │         │                 │
│  • Wake word    │         │  • EFE          │
│  • STT          │         │  • Memory       │
│  • TTS          │         │  • Personas     │
│  • Audio I/O    │         │  • LLM Gateway  │
└─────────────────┘         └─────────────────┘
```

**Client options**:
- Mobile app (iOS/Android) — custom or WebRTC-based
- Web app with microphone access
- Laptop running thin client
- Phase 1 Beast as client (audio only, brain in cloud)

### 3.4 Architecture Changes Required

| Change | Reason |
|--------|--------|
| **HTTP/WebSocket API** | Client-server communication |
| **PostgreSQL support** | Cloud SQL, managed backups |
| **Push notification system** | Proactive prompts to client |
| **Authentication** | Secure access from anywhere |
| **Audio I/O abstraction** | Core doesn't assume local audio |

### 3.5 Why Phase 2?

- **Access from anywhere** — Not tied to home
- **Beast freed up** — Use desktop for gaming/work without Kiro hogging resources
- **Reliability** — GCP uptime > home power/network reliability
- **Shared state** — Multiple clients, one brain

### 3.6 Cost Estimate

| Resource | Monthly Cost (GCP) |
|----------|-------------------|
| e2-medium (always-on) | ~$25-35 |
| Cloud SQL (db-f1-micro) | ~$10-15 |
| Cloud Storage (backups) | ~$1-5 |
| Egress (API calls) | ~$5-10 |
| **Total** | **~$40-65/month** |

**Note**: LLM API costs are additional and usage-dependent.

---

## 4. Phase 3: Raspberry Pi Portable

### 4.1 Hardware Profile

| Component | Specification |
|-----------|---------------|
| **Device** | Raspberry Pi 5 (8GB) |
| **CPU** | ARM Cortex-A76, 4-core @ 2.4GHz |
| **RAM** | 8GB |
| **Storage** | microSD (128GB+) or NVMe via HAT |
| **Audio** | USB microphone + speaker (ReSpeaker array or similar) |
| **Network** | WiFi, optional 4G/LTE HAT |
| **Power** | USB-C, battery pack for portability |
| **Enclosure** | Custom 3D-printed or commercial case |

### 4.2 Capability Matrix

| Capability | Status | Notes |
|------------|--------|-------|
| Wake word detection | ✅ Full | Local, optimized model (OpenWakeWord) |
| Speech-to-text | ⚠️ Degraded | Cloud required — local whisper too slow |
| Text-to-speech | ✅ Full | Piper runs well on Pi 5 |
| LLM inference | ❌ Cloud only | Local models too slow/limited |
| Memory system | ✅ Full | SQLite, plenty of storage |
| EFE | ✅ Full | All features |
| Persona system | ✅ Full | All features |
| Proactive prompts | ✅ Full | Local scheduling |
| Local embedding | ⚠️ Limited | Possible but slow |

### 4.3 Architecture Considerations

**The Pi can run the full Kiro daemon** — it's just slower and cloud-dependent for heavy inference.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      PI DEPLOYMENT ARCHITECTURE                            │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  RASPBERRY PI 5                                                     │   │
│  │                                                                     │   │
│  │  LOCAL (always available):              CLOUD (requires network):   │   │
│  │  • Wake word detection                  • STT (Whisper API)         │   │
│  │  • Audio capture/playback               • LLM inference             │   │
│  │  • TTS (Piper)                          • (Optional) embeddings     │   │
│  │  • EFE (full)                                                       │   │
│  │  • Memory (SQLite)                                                  │   │
│  │  • Persona system                                                   │   │
│  │  • Scheduling/proactivity                                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.4 Offline Behavior

When network is unavailable:

| Function | Offline Behavior |
|----------|------------------|
| Wake word | ✅ Works |
| Listening | ✅ Captures audio, queues for transcription |
| STT | ❌ Queued until online |
| Simple commands | ⚠️ Pattern-matched locally (timer, reminder) |
| LLM conversation | ❌ "I'll need to think about that when I'm back online" |
| TTS | ✅ Works |
| EFE queries | ✅ Works (local database) |
| Reminders firing | ✅ Works |

**Graceful degradation**: Kiro acknowledges limitations rather than failing silently.

### 4.5 Why Phase 3?

- **Portability** — Take Kiro to the workshop, kitchen, car
- **Independence** — Works without Beast running
- **Low power** — Battery operation possible
- **Dedicated device** — Always listening, not sharing resources
- **Affordable** — ~$100-150 total hardware cost

### 4.6 Hardware BOM (Estimate)

| Component | Cost |
|-----------|------|
| Raspberry Pi 5 (8GB) | $80 |
| Power supply | $15 |
| microSD (128GB) | $15 |
| ReSpeaker 4-mic array | $35 |
| Speaker (small) | $15 |
| Case | $15-30 |
| **Total** | **~$175-190** |

---

## 5. Phase 4: Phone-Sized Cellular Device

### 5.1 Vision

A dedicated, pocket-sized Kiro device with:
- Always-on listening
- Cellular connectivity (not dependent on WiFi)
- All-day battery life
- Purpose-built for Kiro (not a general phone)

### 5.2 Hardware Candidates

| Option | Pros | Cons |
|--------|------|------|
| **Custom SBC + LTE** | Full control, Linux | Development effort, form factor |
| **Pine64 PinePhone** | Linux-native, hackable | Underpowered, poor battery |
| **Android device (repurposed)** | Cheap, capable, cellular | Android overhead, not true daemon |
| **ESP32-S3 + LTE module** | Ultra-low power | Too limited for full Kiro |
| **Future: custom hardware** | Ideal | Significant investment |

### 5.3 Realistic Phase 4 Path

**Most pragmatic approach**: Android app that acts as a **client to Phase 2 cloud**.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      PHASE 4: HYBRID APPROACH                              │
│                                                                             │
│  ┌─────────────────────────┐         ┌─────────────────────────┐           │
│  │  ANDROID CLIENT         │         │  CLOUD (Phase 2)        │           │
│  │                         │  LTE    │                         │           │
│  │  • Wake word (local)    │ ◄─────► │  • Full Kiro core       │           │
│  │  • Audio I/O            │         │  • EFE, Memory, etc.    │           │
│  │  • STT (on-device)      │         │                         │           │
│  │  • TTS (on-device)      │         │                         │           │
│  │  • Offline queue        │         │                         │           │
│  └─────────────────────────┘         └─────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Alternative**: Raspberry Pi Zero 2W + LTE HAT in a custom enclosure (bulkier but full Linux).

### 5.4 Capability Matrix (Android Client + Cloud)

| Capability | Status | Notes |
|------------|--------|-------|
| Wake word detection | ✅ Full | On-device (Porcupine or similar) |
| Speech-to-text | ✅ Full | On-device (Android STT or Whisper) |
| Text-to-speech | ✅ Full | On-device (Android TTS) |
| LLM inference | ✅ Full | Via cloud |
| Memory/EFE | ✅ Full | Via cloud |
| Proactive prompts | ✅ Full | Push notifications |
| Offline operation | ⚠️ Limited | Queue and sync |
| Battery life | ⚠️ Varies | Always-listening is expensive |

### 5.5 Why Phase 4?

- **True mobility** — Kiro in your pocket, anywhere with cell signal
- **Always available** — Not dependent on home WiFi or being near a computer
- **Dedicated interface** — Not fighting with phone notifications/apps

### 5.6 Status

🔮 **FUTURE PHASE** — Requires Phase 2 (cloud) to be stable first. May evolve based on hardware landscape.

---

## 6. Architectural Abstractions

To support all four phases without rewrites, Kiro requires these abstraction layers:

### 6.1 Audio I/O Abstraction

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      AUDIO I/O ABSTRACTION                                 │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  AudioProvider (Interface)                                          │   │
│  │                                                                     │   │
│  │  • start_listening() → AudioStream                                  │   │
│  │  • stop_listening()                                                 │   │
│  │  • play_audio(data)                                                 │   │
│  │  • get_audio_level() → float                                        │   │
│  │  • is_available() → bool                                            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│          ┌─────────────────────────┼─────────────────────────┐             │
│          ▼                         ▼                         ▼             │
│  ┌───────────────┐        ┌───────────────┐        ┌───────────────┐       │
│  │ LocalAudio    │        │ RemoteAudio   │        │ NullAudio     │       │
│  │ (Phase 1, 3)  │        │ (Phase 2, 4)  │        │ (Headless)    │       │
│  │               │        │               │        │               │       │
│  │ PyAudio/      │        │ WebSocket/    │        │ No-op for     │       │
│  │ sounddevice   │        │ WebRTC        │        │ testing       │       │
│  └───────────────┘        └───────────────┘        └───────────────┘       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 STT/TTS Abstraction

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      STT PROVIDER ABSTRACTION                              │
│                                                                             │
│  STTProvider (Interface)                                                    │
│  • transcribe(audio) → TranscriptResult                                     │
│  • supports_streaming() → bool                                              │
│  • get_latency_estimate() → float                                           │
│                                                                             │
│  Implementations:                                                           │
│  • WhisperAPIProvider (cloud, all phases)                                   │
│  • WhisperLocalProvider (local, Phase 1 only)                               │
│  • GoogleSTTProvider (cloud, backup)                                        │
│  • AndroidSTTProvider (Phase 4, on-device)                                  │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                      TTS PROVIDER ABSTRACTION                              │
│                                                                             │
│  TTSProvider (Interface)                                                    │
│  • synthesize(text) → AudioData                                             │
│  • get_voices() → [Voice]                                                   │
│  • set_voice(voice_id)                                                      │
│                                                                             │
│  Implementations:                                                           │
│  • PiperTTSProvider (local, Phase 1, 3)                                     │
│  • ElevenLabsProvider (cloud, high quality)                                 │
│  • GoogleTTSProvider (cloud, backup)                                        │
│  • AndroidTTSProvider (Phase 4, on-device)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.3 Database Abstraction

Already covered in System Architecture — SQLAlchemy ORM with SQLite/PostgreSQL swap.

### 6.4 LLM Gateway Abstraction

Already designed — provider-agnostic interface with tiered routing.

### 6.5 Capability Negotiation

At startup, Kiro queries available capabilities and adjusts behavior:

```python
# Pseudocode
class CapabilityManager:
    def detect_capabilities(self) -> Capabilities:
        return Capabilities(
            has_local_audio=self._check_audio_devices(),
            has_gpu=self._check_cuda_available(),
            has_network=self._check_internet(),
            local_stt_available=self._check_whisper_local(),
            local_tts_available=self._check_piper(),
            local_llm_available=self._check_llama(),
            database_type=self._get_db_type(),
        )
    
    def get_stt_provider(self) -> STTProvider:
        if self.caps.local_stt_available and self.prefer_local:
            return WhisperLocalProvider()
        elif self.caps.has_network:
            return WhisperAPIProvider()
        else:
            return OfflineSTTProvider()  # Queue for later
```

### 6.6 Configuration by Phase

```yaml
# Phase 1 (Desktop)
deployment:
  phase: desktop
  audio: local
  stt: local_preferred    # Use GPU whisper, fall back to API
  tts: local              # Piper
  llm: cloud_preferred    # API, local fallback available
  database: sqlite

# Phase 2 (Cloud)
deployment:
  phase: cloud
  audio: remote           # Expect client to handle
  stt: cloud              # Whisper API
  tts: cloud              # ElevenLabs or Google
  llm: cloud              # API only
  database: postgresql

# Phase 3 (Raspberry Pi)
deployment:
  phase: portable
  audio: local
  stt: cloud              # No local capability
  tts: local              # Piper works on Pi
  llm: cloud              # API only
  database: sqlite

# Phase 4 (Mobile client)
deployment:
  phase: mobile_client
  audio: local            # On-device
  stt: local              # On-device Android/iOS STT
  tts: local              # On-device
  llm: remote             # Via Phase 2 cloud
  database: remote        # Via Phase 2 cloud
```

---

## 7. Migration Paths

### 7.1 Phase 1 → Phase 2

| Step | Action |
|------|--------|
| 1 | Deploy Kiro core to GCP instance |
| 2 | Migrate SQLite → PostgreSQL (SQLAlchemy handles it) |
| 3 | Enable API authentication |
| 4 | Build/deploy client app |
| 5 | Point client to cloud instance |
| 6 | (Optional) Keep Beast as a client for home use |

### 7.2 Phase 1 → Phase 3

| Step | Action |
|------|--------|
| 1 | Install Kiro on Raspberry Pi |
| 2 | Copy SQLite database from Beast |
| 3 | Configure for cloud STT/LLM |
| 4 | Attach audio hardware |
| 5 | Test wake word and response loop |

### 7.3 Phase 2 + Phase 3 (Hybrid)

Multiple endpoints can share the same cloud brain:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Beast     │     │     Pi      │     │   Phone     │
│  (Client)   │     │  (Client)   │     │  (Client)   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                    ┌──────▼──────┐
                    │   Cloud     │
                    │   (Brain)   │
                    └─────────────┘
```

---

## 8. Power and Thermal Considerations

### 8.1 Phase 1 (Desktop)

- **Power**: Unlimited (wall power)
- **Thermal**: Managed by case fans, GPU cooling
- **Optimization**: None required

### 8.2 Phase 2 (Cloud)

- **Power**: GCP-managed
- **Thermal**: GCP-managed
- **Cost optimization**: Right-size instance, consider preemptible for dev

### 8.3 Phase 3 (Raspberry Pi)

| Mode | Power Draw | Battery Life (10Ah pack) |
|------|------------|--------------------------|
| Idle (listening) | ~3W | ~15 hours |
| Active (processing) | ~6W | ~7 hours |
| Peak (STT + TTS) | ~8W | ~5 hours |

**Optimization strategies**:
- Reduce wake word model size
- Aggressive audio VAD to minimize processing
- Sleep mode when user is away (future: presence detection)

### 8.4 Phase 4 (Mobile)

- **Always-on listening is expensive** — Major battery drain
- **Mitigation**: Motion/presence detection, scheduled listening windows
- **Realistic expectation**: 4-8 hours active use without aggressive optimization

---

## 9. Engine Selection by Hardware Tier

This section provides concrete engine recommendations for optimal latency on each hardware tier.

### 9.1 The Beast (Desktop with RTX 3060)

**Target latency**: < 1.5 seconds wake-to-speech

| Component | Engine | Model/Config | Expected Latency |
|-----------|--------|--------------|------------------|
| **STT** | faster-whisper | `large-v3` on GPU | ~0.3s |
| **TTS** | Piper | `en_US-amy-medium` | ~0.05s |
| **LLM** | Claude/OpenAI | claude-sonnet or gpt-4o-mini + streaming | ~0.5s to first token |
| **Wake word** | OpenWakeWord | `hey_jarvis` | Real-time |
| **VAD** | webrtcvad | Mode 3, 0.4s silence | N/A |

**Config override** (`config/beast.yaml`):
```yaml
stt:
  engine: faster-whisper
  model: large-v3
  device: cuda
tts:
  engine: piper
  model: en_US-amy-medium
llm:
  stream: true
  tier_models:
    fast: gpt-4o-mini
    standard: claude-3-5-sonnet-20241022
```

### 9.2 Raspberry Pi 5

**Target latency**: < 3 seconds wake-to-speech (limited by cloud STT)

| Component | Engine | Model/Config | Expected Latency |
|-----------|--------|--------------|------------------|
| **STT** | Whisper API | `whisper-1` | ~1.5-2s (network) |
| **TTS** | Piper | `en_US-amy-low` | ~0.08s |
| **LLM** | OpenAI | gpt-4o-mini + streaming | ~0.8s to first token |
| **Wake word** | OpenWakeWord | `hey_jarvis` (pruned) | Real-time |
| **VAD** | webrtcvad | Mode 3, 0.5s silence | N/A |

**Config override** (`config/pi.yaml`):
```yaml
stt:
  engine: whisper-api  # No GPU for local
tts:
  engine: piper
  model: en_US-amy-low  # Smaller, faster
llm:
  stream: true
  tier_models:
    fast: gpt-4o-mini
    standard: gpt-4o-mini  # Stay fast on Pi
```

**Note**: Future faster-whisper with ONNX may enable acceptable local STT on Pi 5.

### 9.3 Cloud-Only Deployment

**Target latency**: < 2 seconds (client-side audio processing assumed)

| Component | Engine | Model/Config | Notes |
|-----------|--------|--------------|-------|
| **STT** | Whisper API | `whisper-1` | Or client-side |
| **TTS** | OpenAI TTS | `nova` | Or client-side Piper |
| **LLM** | Claude/OpenAI | Streaming always | Network is fast |

### 9.4 Engine Installation Requirements

| Engine | Installation | Size | Notes |
|--------|--------------|------|-------|
| **faster-whisper** | `pip install faster-whisper` | ~3GB (large-v3) | Requires CUDA |
| **Piper** | Download binary + voice | ~100MB per voice | Cross-platform |
| **OpenWakeWord** | `pip install openwakeword` | ~10MB per model | Already installed |

### 9.5 Automatic Hardware Detection

At startup, Kiro profiles the system:

```python
class HardwareProfile:
    gpu_available: bool
    gpu_vram_mb: int | None
    cpu_cores: int
    ram_mb: int
    piper_installed: bool
    
    @classmethod
    def detect(cls) -> "HardwareProfile":
        # Check nvidia-smi, /proc/meminfo, etc.
        ...
    
    def select_engines(self) -> EngineConfig:
        if self.gpu_available and self.gpu_vram_mb >= 4096:
            return EngineConfig(stt="faster-whisper", tts="piper")
        elif self.piper_installed:
            return EngineConfig(stt="whisper-api", tts="piper")
        else:
            return EngineConfig(stt="whisper-api", tts="openai")
```

This auto-selection can always be overridden via config file.

---

## 10. Summary by Phase

| Phase | Where | Audio | STT | TTS | LLM | Database | Status |
|-------|-------|-------|-----|-----|-----|----------|--------|
| 1 | Desktop | Local | Local/Cloud | Local | Cloud/Local | SQLite | **Primary** |
| 2 | Cloud | Remote (client) | Cloud | Cloud | Cloud | PostgreSQL | Planned |
| 3 | Pi | Local | Cloud | Local | Cloud | SQLite | Planned |
| 4 | Mobile | Local | Local | Local | Remote | Remote | Future |

---

## 11. Key Takeaways

1. **Design for Phase 1, abstract for all** — Every component uses interfaces
2. **Cloud is the bridge** — Phase 2 enables 3 and 4 as clients
3. **SQLite ↔ PostgreSQL** — One config change, SQLAlchemy handles it
4. **Audio I/O is the variable** — Core logic doesn't assume local audio
5. **Graceful degradation** — Each phase knows its limits and communicates them
6. **Don't over-engineer Phase 4** — Build 1-2-3 first, 4 will clarify itself
7. **Latency is king** — Sub-2s response time is the target; streaming is mandatory

*Next: [07-development-plan.md](07-development-plan.md)*
