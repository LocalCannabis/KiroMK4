"""
kiro.memory — Four-tier memory architecture.

Tier 0: Promoted facts (instant recall, no HRR needed)
Tier 1: pgvector shelf (coarse retrieval via embedding similarity)
Tier 2: FHRR glasses (dense in-memory retrieval via element-wise complex multiply)
Tier 3: PostgreSQL fact store (full-text fallback, source of truth)

Vectors are NEVER persisted. They are rebuilt from facts + deterministic PRNG seed.
"""

from .hrr import (
    generate_vector,
    bind,
    unbind,
    bundle,
    forget,
    similarity,
    decode,
    build_glass_vector,
    add_to_glass_vector,
    orthogonalize,
    sharpen,
    corvacs,
)

__all__ = [
    "generate_vector",
    "bind",
    "unbind",
    "bundle",
    "forget",
    "similarity",
    "decode",
    "build_glass_vector",
    "add_to_glass_vector",
    "orthogonalize",
    "sharpen",
    "corvacs",
]
