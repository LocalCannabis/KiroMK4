# Kiro v1 — Canonical Specification

**Knowledge Interface & Response Operator**

Version: 1.0  
Created: January 2026  
Status: Design Phase

---

## Document Index

| Document | Description |
|----------|-------------|
| [01-executive-summary.md](01-executive-summary.md) | What Kiro is, the problem it solves, and what success looks like |
| [02-system-architecture.md](02-system-architecture.md) | High-level architecture, subsystems, and data flow |
| [03-executive-function-engine.md](03-executive-function-engine.md) | EFE design — the core ADHD support system |
| [04-persona-system.md](04-persona-system.md) | Multi-persona architecture and design |
| [05-memory-architecture.md](05-memory-architecture.md) | Layered memory system design |
| [06-hardware-roadmap.md](06-hardware-roadmap.md) | Hardware tiers and portability strategy |
| [07-development-plan.md](07-development-plan.md) | Phased implementation plan with deliverables |
| [08-future-expansion.md](08-future-expansion.md) | MKII ideas — explicitly out of v1 scope |
| [09-assumptions-risks.md](09-assumptions-risks.md) | Known constraints and risk mitigation |

---

## Quick Reference

**Core Identity**: Voice-first, always-available personal operating layer

**Primary Mission**: Heavy executive-function counterweight for an ADHD brain

**Runtime Model**: Long-running daemon (not foreground app)

**Primary Interface**: Voice (text as fallback/debug)

**Target Platform (Phase 1)**: Linux desktop ("The Beast" — i9, RTX 3060)

---

## How to Use This Documentation

This specification is written to be:

1. **Self-consistent** — Each document builds on previous ones
2. **Re-entrant** — Any document can be read standalone with sufficient context
3. **Executable** — Detailed enough to implement without further clarification
4. **Iterative** — Designed to be updated as development progresses

When implementing, start with [07-development-plan.md](07-development-plan.md) for phased guidance.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-01-10 | Initial executive summary and system architecture |
| 2026-01-11 | Documentation structure created |
