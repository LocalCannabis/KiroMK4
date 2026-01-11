# Kiro MKII: Future Expansion Notes

**Status**: Post-v1 Roadmap — Not in current scope

---

## Purpose

This document captures features and ideas for **Kiro MKII** — the next major version after v1 is stable and battle-tested.

These items are explicitly **out of scope for v1**. They are documented here to:
1. Avoid scope creep during v1 development
2. Inform architectural decisions (don't block future work)
3. Capture ideas before they're forgotten

**Do not start MKII work until v1 has been in daily use for at least 3 months.**

---

## MKII Feature Candidates

### High Priority (v1.x)

- [ ] **Adaptive Scaffolding Learning** — Learn optimal prompt frequency from user feedback
- [ ] **End-of-Day Reflection** — Optional check-in for task/commitment review
- [ ] **Calendar Integration** — Sync with Google Calendar, etc.
- [ ] **Location Awareness** — Context triggers based on location
- [ ] **Smart Home Integration** — Control lights, etc. via voice

### Medium Priority (v2.x)

- [ ] **Vector Search Memory** — Semantic retrieval using embeddings
- [ ] **Local LLM Support** — Full local inference option
- [ ] **Multi-User Support** — Voice identification, separate contexts
- [ ] **Mobile Client** — Phone app connecting to Kiro daemon
- [ ] **Web Dashboard** — Visual task/memory management

### Exploratory (v3.x+)

- [ ] **Companion Mode** — Emotional support persona
- [ ] **Project Templates** — Pre-built project scaffolds (woodworking, home repair)
- [ ] **Learning Mode** — Kiro helps learn new skills with spaced repetition
- [ ] **Collaborative Mode** — Share project state with others
v1 Consideration |
|----------------|------------------|
| Vector search | Memory schema must support embeddings later |
| Local LLM | LLM Gateway abstraction must be provider-agnostic |
| Multi-user | User ID concept should exist even if unused |
| Mobile client | Core logic must be separable from audio I/O |

---

## When to Revisit

Start MKII planning when:
- v1 has been stable for 3+ months
- Core EFE features are trusted and used daily
- Cloud deployment (Phase 2) is operational
- Clear pain points have emerged from actual use

---

*This document is intentionally brief. Real MKII planning happens after v1 proves itself.ater |
| Local LLM | LLM Gateway abstraction must be provider-agnostic |
| Multi-user | User ID concept should exist even if unused |
| Mobile client | Core logic must be separable from audio I/O |

---

*Next: [10-assumptions-risks.md](10-assumptions-risks.md)*
