# Kiro: Assumptions & Risks

**Version**: 1.0 | **Date**: January 2026 | **Status**: Living Document

---

## Critical Assumptions

These assumptions underlie the entire design. If any prove false, significant redesign may be needed.

| Assumption | Impact if False |
|------------|-----------------|
| User has reliable internet for cloud LLM access | Must accelerate local LLM support |
| Wake word detection can run with acceptable CPU usage | May need dedicated hardware or different approach |
| Single-user system (no voice ID needed for v1) | Architecture may need user context earlier |
| SQLAlchemy abstraction handles SQLite↔PostgreSQL | If edge cases arise, may need raw SQL conditionals |
| Python async is sufficient for real-time audio | May need dedicated audio process or Rust components |
| Cloud STT latency is acceptable (~1-2s) | May need local STT from day one |

---

## Known Risks

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Wake word accuracy too low | Medium | High | Test multiple libraries; allow push-to-talk fallback |
| LLM latency breaks conversational flow | Medium | High | Pre-fetch, caching, tier routing |
| Memory retrieval relevance poor | Medium | Medium | Start simple; add vector search later |
| Audio pipeline complexity | High | Medium | Use proven libraries (sounddevice, pyaudio) |
| Daemon stability over long runs | Medium | High | Watchdog process, automatic restart |

### Product Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Proactive prompts feel annoying | High | High | Start conservative; user controls intensity |
| User doesn't trust Kiro to remember | Medium | Critical | Feedback loop; "got it" confirmations |
| Context window limits break long conversations | Medium | Medium | Summarization; thread isolation |
| Feature creep delays usable system | High | High | Strict phase scoping |

### Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Cloud API costs escalate | Medium | Medium | Tier routing; usage tracking; budgets |
| API provider deprecates model | Low | High | Provider abstraction; avoid single dependency |
| Data loss (SQLite corruption) | Low | Critical | Regular backups; journaling enabled |

---

## Open Questions

Questions that need answers during implementation:

1. **Wake word**: Which library/model? (Candidates: Porcupine, OpenWakeWord, Snowboy)
2. **STT**: Whisper API vs local whisper.cpp — latency vs cost tradeoff
3. **TTS**: Which voice? (Candidates: ElevenLabs, Piper, Coqui)
4. **Intent classification**: Train custom model or use LLM for all?
5. **Memory summarization**: LLM-based or extractive?

---

## Constraints

Non-negotiable constraints that limit design options:

| Constraint | Source | Impact |
|------------|--------|--------|
| Must run on Linux | User requirement | No Windows/Mac-specific dependencies |
| Python primary language | User requirement | Performance-critical paths may need optimization |
| Privacy-conscious | Core value | No unnecessary cloud data transmission |
| Single developer | Resource reality | Simplicity over elegance; minimal dependencies |

---

## Decision Log

Track key decisions made during design/implementation:

| Date | Decision | Rationale | Alternatives Rejected |
|------|----------|-----------|----------------------|
| 2026-01-10 | Single daemon process | Simplicity for v1 | Microservices (too complex) |
| 2026-01-10 | SQLite for Phase 1 persistence | Simple, reliable, zero-config | PostgreSQL (overkill for local) |
| 2026-01-10 | Cloud LLM first | Faster to usable system | Local-first (delays core functionality) |
| 2026-01-10 | Layered memory (L1/L2/L3) | Balance availability vs storage | Flat storage (doesn't scale) |
| 2026-01-11 | SQLAlchemy ORM abstraction | Enables SQLite→PostgreSQL migration | Raw SQL (locks to one DB), direct sqlite3 (not portable) |

---

*End of canonical specification documents.*
