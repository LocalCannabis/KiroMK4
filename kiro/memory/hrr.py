"""
kiro.memory.hrr — FHRR (Fourier Holographic Reduced Representation) math primitives.

Ported faithfully from NeoVertex1/nuggets core.ts (MIT).
All vectors are complex-valued with unit magnitude per element (phase-only).
Binding is element-wise complex multiplication; unbinding uses conjugate.

Key insight from Nuggets: vectors are NEVER persisted. They are rebuilt
deterministically from text + a seeded PRNG. Storage stays tiny.

Mathematical operations:
  generate_vector(text, D, seed) → unit-magnitude complex vector
  bind(a, b)                     → element-wise complex product a * b
  unbind(m, key)                 → element-wise m * conj(key)
  bundle(vectors)                → sum + scale by 1/√N
  forget(composite, key, value)  → composite - bind(key, value), renormalize
  orthogonalize(keys)            → Gram-Schmidt decorrelation in ℝ²ᴰ → unit phase
  sharpen(z, p)                  → z * (|z| + ε)^(p-1)
  corvacs(z, a)                  → z * tanh(a·|z|) / |z|
  similarity(a, b)               → cosine similarity in ℝ²ᴰ
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import List, Tuple

import numpy as np

logger = logging.getLogger("kiro.memory.hrr")


# ─────────────────────────────────────────────────────────────────────────────
# Seeded PRNG — Mulberry32 (exact port from Nuggets core.ts)
# ─────────────────────────────────────────────────────────────────────────────

def _mulberry32(seed: int):
    """
    Mulberry32 PRNG — exact port from Nuggets core.ts.
    Returns a callable yielding floats in [0, 1).
    Same seed = same sequence, cross-language deterministic.
    """
    s = seed & 0xFFFFFFFF  # u32

    def _next() -> float:
        nonlocal s
        s = (s + 0x6D2B79F5) & 0xFFFFFFFF
        t = s ^ (s >> 15)
        t = ((t * (1 | s)) & 0xFFFFFFFF)
        t = (t + ((t ^ (t >> 7)) * (61 | t) & 0xFFFFFFFF)) & 0xFFFFFFFF
        t = t ^ (t >> 14)
        return (t & 0xFFFFFFFF) / 4294967296.0

    return _next


def seed_from_text(text: str) -> int:
    """Derive a u32 seed from a string via SHA-256 (first 4 bytes, little-endian)."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "little") & 0xFFFFFFFF


# ─────────────────────────────────────────────────────────────────────────────
# Key / vector generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_vector(text: str, dimension: int, seed: int) -> np.ndarray:
    """
    Deterministic unit-magnitude complex vector from text + seed.

    Same text + same seed = same vector every time. This is what makes glass
    reconstruction possible without persisting vectors.

    Each element has magnitude 1 (phase-only): v[d] = exp(i·φ[d])
    where φ ~ Uniform(0, 2π) from seeded PRNG.

    The seed is derived from hash(text) XOR glass_seed, so the same fact text
    produces different vectors in different glasses (preventing cross-glass
    interference if a fact is ever moved).

    Args:
        text:      String to encode (fact key or value).
        dimension: Vector dimensionality (must match the glass).
        seed:      Glass-level PRNG seed (XORed with text hash).

    Returns:
        Complex128 ndarray of shape (dimension,), |v[d]| = 1 ∀ d.
    """
    combined_seed = (seed_from_text(text) ^ seed) & 0xFFFFFFFF
    rng = _mulberry32(combined_seed)
    TWO_PI = 2.0 * math.pi

    re = np.empty(dimension, dtype=np.float64)
    im = np.empty(dimension, dtype=np.float64)
    for d in range(dimension):
        phi = TWO_PI * rng()
        re[d] = math.cos(phi)
        im[d] = math.sin(phi)

    return re + 1j * im


# ─────────────────────────────────────────────────────────────────────────────
# Orthogonalization (exact port from Nuggets core.ts orthogonalize())
# ─────────────────────────────────────────────────────────────────────────────

def orthogonalize(
    keys: List[np.ndarray],
    iters: int = 1,
    step: float = 0.4,
) -> List[np.ndarray]:
    """
    Gram-Schmidt-like decorrelation in ℝ²ᴰ, projected back to unit phase.

    This is the critical numerical stability fix from Nuggets. Without it,
    cross-key interference degrades retrieval accuracy ~100x faster.

    Algorithm (per iteration):
        1. Stack re/im → ℝ²ᴰ matrix K
        2. G = K @ Kᵀ, zero diagonal
        3. K = K - step·(G @ K) / 2D
        4. Row-normalize K
    After all iterations, convert back to unit-magnitude complex via atan2.

    Args:
        keys:  List of complex128 ndarrays (unit-magnitude per element).
        iters: Number of decorrelation iterations (default 1, diminishing returns).
        step:  Learning rate for correction (default 0.4 from Nuggets).

    Returns:
        New list of orthogonalized complex vectors (unit magnitude preserved).
    """
    if iters <= 0 or len(keys) == 0:
        return keys

    V = len(keys)
    D = keys[0].shape[0]
    D2 = D * 2

    # Stack to [V, 2D] real matrix
    K = np.empty((V, D2), dtype=np.float64)
    for v in range(V):
        K[v, :D] = keys[v].real
        K[v, D:] = keys[v].imag

    for _ in range(iters):
        # Gram matrix G = K @ K.T, zero diagonal
        G = K @ K.T
        np.fill_diagonal(G, 0.0)

        # K = K - step * (G @ K) / D2
        correction = G @ K
        K -= (step / D2) * correction

        # Row-normalize
        norms = np.linalg.norm(K, axis=1, keepdims=True) + 1e-9
        K /= norms

    # Convert back to unit-phase complex
    result = []
    for v in range(V):
        r = K[v, :D]
        i = K[v, D:]
        phase = np.arctan2(i, r)
        result.append(np.cos(phase) + 1j * np.sin(phase))

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Bind / Unbind (exact port from Nuggets — element-wise complex multiply)
# ─────────────────────────────────────────────────────────────────────────────

def bind(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Element-wise complex product a * b.

    For unit-magnitude vectors this is equivalent to phase addition:
    if a = exp(iα) and b = exp(iβ), then bind(a,b) = exp(i(α+β)).

    This is FHRR binding — NOT circular convolution via FFT.
    The result is dissimilar to both inputs but invertible via conjugate.

    Args:
        a: First complex vector (typically the key).
        b: Second complex vector (typically the value).

    Returns:
        Bound vector (same dimension, unit magnitude if inputs are unit magnitude).
    """
    return a * b


def unbind(composite: np.ndarray, key: np.ndarray) -> np.ndarray:
    """
    Element-wise m * conj(key).

    Inverse of bind: unbind(bind(a, b), a) = b (exact for single binding).
    For a superposition, returns b + noise from other bindings.

    Args:
        composite: Glass vector (bundled bindings).
        key:       Key vector to unbind with.

    Returns:
        Approximate value vector.
    """
    return composite * np.conj(key)


# ─────────────────────────────────────────────────────────────────────────────
# Bundle (superposition)
# ─────────────────────────────────────────────────────────────────────────────

def bundle(vectors: List[np.ndarray]) -> np.ndarray:
    """
    Sum multiple bound vectors into one glass, scaled by 1/√N.

    This is the Nuggets superposition pattern: sum + scale to prevent
    magnitude explosion while preserving relative signal strength.

    Args:
        vectors: List of bound key-value pair vectors.

    Returns:
        Bundled glass vector. Zero vector if list is empty.
    """
    if not vectors:
        logger.warning("bundle() called with empty vector list")
        return np.zeros(1, dtype=np.complex128)

    stacked = np.stack(vectors)
    total = stacked.sum(axis=0)
    scale = 1.0 / math.sqrt(len(vectors))
    return total * scale


# ─────────────────────────────────────────────────────────────────────────────
# Forget (subtraction-based removal)
# ─────────────────────────────────────────────────────────────────────────────

def forget(
    composite: np.ndarray,
    key_vec: np.ndarray,
    value_vec: np.ndarray,
    old_count: int,
) -> np.ndarray:
    """
    Remove a key-value binding from a glass by subtraction.

    The composite was built as sum(bindings) * (1/√N). To remove one binding:
    1. Undo the scaling: composite * √N
    2. Subtract bind(key, value)
    3. Re-scale: * 1/√(N-1)

    NOTE: After many incremental forget/add cycles, floating-point drift
    accumulates. Track this via fidelity tests. If fidelity drops below
    threshold, do a full rebuild from Postgres facts + seed.

    Args:
        composite:  Current glass vector.
        key_vec:    Key vector of the fact to remove.
        value_vec:  Value vector of the fact to remove.
        old_count:  How many facts were in the glass before removal.

    Returns:
        Updated glass vector with the binding removed.
    """
    if old_count <= 1:
        return np.zeros_like(composite)

    # Undo 1/√N scaling
    unscaled = composite * math.sqrt(old_count)
    # Subtract the binding
    unscaled = unscaled - bind(key_vec, value_vec)
    # Re-scale for N-1
    new_count = old_count - 1
    return unscaled * (1.0 / math.sqrt(new_count))


# ─────────────────────────────────────────────────────────────────────────────
# Signal processing (exact port from Nuggets)
# ─────────────────────────────────────────────────────────────────────────────

def sharpen(z: np.ndarray, p: float = 1.0, eps: float = 1e-12) -> np.ndarray:
    """
    Magnitude-sharpening nonlinearity (from Nuggets sharpen()).

    z_out = z * (|z| + ε)^(p - 1)

    p > 1  → contrast-increasing (amplifies strong signals, suppresses noise)
    p < 1  → softening
    p == 1 → identity

    Args:
        z:   Complex vector (typically the result of unbind).
        p:   Sharpening exponent (default 1.0 = no-op).
        eps: Numerical stability constant.

    Returns:
        Sharpened complex vector.
    """
    if p == 1.0:
        return z

    mag = np.abs(z)
    scale = (mag + eps) ** (p - 1.0)
    return z * scale


def corvacs(z: np.ndarray, a: float = 0.0) -> np.ndarray:
    """
    Gentle magnitude limiter (CORVACS-lite, from Nuggets corvacsLite()).

    z_out = z * tanh(a·|z|) / |z|

    Prevents any single dimension from dominating the cosine similarity.
    Acts as automatic gain control.

    a == 0 → identity (disabled)
    a > 0  → soft saturation

    Args:
        z: Complex vector.
        a: Saturation strength (0 = disabled, 0.9 typical).

    Returns:
        Magnitude-limited complex vector.
    """
    if a <= 0:
        return z

    mag = np.abs(z) + 1e-12
    scale = np.tanh(a * mag) / mag
    return z * scale


# ─────────────────────────────────────────────────────────────────────────────
# Similarity (cosine in ℝ²ᴰ, matching Nuggets stackAndUnitNorm pattern)
# ─────────────────────────────────────────────────────────────────────────────

def _to_real2d(z: np.ndarray) -> np.ndarray:
    """Convert complex vector to [re, im] real vector for similarity."""
    return np.concatenate([z.real, z.imag])


def _unit_norm(v: np.ndarray) -> np.ndarray:
    """Normalize a real vector to unit length."""
    n = np.linalg.norm(v)
    if n < 1e-12:
        return v
    return v / n


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Cosine similarity between two complex vectors in ℝ²ᴰ space.

    Converts complex → [re, im] real vectors, unit-normalizes, then dot product.
    This matches the Nuggets decoding similarity exactly.

    Args:
        a: First complex vector.
        b: Second complex vector.

    Returns:
        Cosine similarity score in [-1, 1].
    """
    a_real = _unit_norm(_to_real2d(a))
    b_real = _unit_norm(_to_real2d(b))
    return float(np.dot(a_real, b_real))


def similarity_matrix(query: np.ndarray, candidates: List[np.ndarray]) -> np.ndarray:
    """
    Compute cosine similarity between a query and multiple candidates in ℝ²ᴰ.

    Efficient batch version using matrix multiplication (Nuggets pattern:
    sims = vocab_norm @ query_2d).

    Args:
        query:      Complex query vector.
        candidates: List of complex candidate vectors.

    Returns:
        Float64 array of similarity scores, one per candidate.
    """
    if not candidates:
        return np.array([], dtype=np.float64)

    q = _unit_norm(_to_real2d(query))
    D2 = q.shape[0]
    V = len(candidates)

    # Stack candidates to [V, 2D] and unit-normalize rows
    mat = np.empty((V, D2), dtype=np.float64)
    for i, c in enumerate(candidates):
        mat[i] = _to_real2d(c)

    # Row-normalize
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12
    mat /= norms

    # sims = mat @ q
    return mat @ q


def softmax_temp(sims: np.ndarray, T: float = 1.0) -> np.ndarray:
    """
    Temperature-scaled softmax over similarity logits (from Nuggets).

    Lower T → sharper distribution (more confident).
    Higher T → flatter distribution.

    Args:
        sims: Array of similarity scores.
        T:    Temperature (default 1.0).

    Returns:
        Probability distribution (sums to 1).
    """
    T = max(T, 1e-6)
    z = sims / T
    z = z - z.max()  # numerical stability
    e = np.exp(z)
    return e / (e.sum() + 1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Full decode pipeline (Nuggets pattern: sharpen → corvacs → sim → softmax)
# ─────────────────────────────────────────────────────────────────────────────

def decode(
    retrieved: np.ndarray,
    candidate_vecs: List[np.ndarray],
    sharpen_p: float = 1.0,
    corvacs_a: float = 0.0,
    temp_T: float = 0.9,
) -> Tuple[int, float, np.ndarray]:
    """
    Full Nuggets decode pipeline: sharpen → corvacs → similarity → softmax.

    Given a noisy retrieved vector (from unbind) and a list of known
    candidate value vectors, returns the best matching candidate index,
    its similarity score, and the full probability distribution.

    Args:
        retrieved:      Noisy complex vector from unbind().
        candidate_vecs: Known value vectors for all facts in this glass.
        sharpen_p:      Sharpening exponent (1.0 = disabled, 1.25 typical).
        corvacs_a:      CORVACS magnitude limiter (0.0 = disabled, 0.9 typical).
        temp_T:         Softmax temperature (0.9 default).

    Returns:
        (best_index, best_similarity, probabilities)
    """
    # Signal processing chain: sharpen → corvacs
    cleaned = sharpen(retrieved, sharpen_p)
    cleaned = corvacs(cleaned, corvacs_a)

    # Cosine similarity against all candidates
    sims = similarity_matrix(cleaned, candidate_vecs)

    # Softmax for probability distribution
    probs = softmax_temp(sims, temp_T)

    best_idx = int(np.argmax(sims))
    best_sim = float(sims[best_idx])

    return best_idx, best_sim, probs


# ─────────────────────────────────────────────────────────────────────────────
# Glass construction (build from clean facts + seed — NEVER persisted)
# ─────────────────────────────────────────────────────────────────────────────

def build_glass_vector(
    facts: List[dict],
    dimension: int,
    seed: int,
    orth_iters: int = 1,
    orth_step: float = 0.4,
) -> np.ndarray:
    """
    Build an HRR glass vector from clean facts + a deterministic seed.

    This is the core Nuggets pattern: facts are stored as text in Postgres,
    vectors are rebuilt on demand from text + seed. Never persisted.

    Algorithm:
        1. Generate key and value vectors for each fact using seeded PRNG
        2. Optionally orthogonalize all value vectors (reduces interference)
        3. Bind each key-value pair
        4. Bundle all bindings (sum + 1/√N scaling)

    Args:
        facts:      List of dicts with 'hrr_key' and 'hrr_value'.
        dimension:  HRR vector dimensionality.
        seed:       Glass-level PRNG seed for deterministic reconstruction.
        orth_iters: Orthogonalization iterations (0 to disable).
        orth_step:  Orthogonalization learning rate.

    Returns:
        Bundled glass vector (complex128 ndarray).
    """
    if not facts:
        return np.zeros(dimension, dtype=np.complex128)

    # Generate all key and value vectors
    key_vecs = [generate_vector(f["hrr_key"], dimension, seed) for f in facts]
    val_vecs = [generate_vector(f["hrr_value"], dimension, seed) for f in facts]

    # Orthogonalize value vectors to reduce cross-interference
    if orth_iters > 0 and len(val_vecs) > 1:
        val_vecs = orthogonalize(val_vecs, iters=orth_iters, step=orth_step)

    # Bind each key-value pair and bundle
    bindings = [bind(k, v) for k, v in zip(key_vecs, val_vecs)]
    return bundle(bindings)


def add_to_glass_vector(
    glass_vector: np.ndarray,
    key: str,
    value: str,
    current_count: int,
    dimension: int,
    seed: int,
) -> np.ndarray:
    """
    Incrementally add a single binding to an existing glass vector.

    Undo 1/√N scaling → add new binding → re-scale for N+1.

    NOTE: Incremental adds skip orthogonalization. After many adds,
    fidelity may degrade. Track via fidelity tests; rebuild if needed.

    Args:
        glass_vector:  Current cached glass vector.
        key:           New fact key string.
        value:         New fact value string.
        current_count: Facts in glass before this addition.
        dimension:     HRR dimensionality.
        seed:          Glass PRNG seed.

    Returns:
        Updated glass vector.
    """
    key_vec = generate_vector(key, dimension, seed)
    val_vec = generate_vector(value, dimension, seed)
    new_binding = bind(key_vec, val_vec)

    if current_count == 0:
        # First fact — just return the binding scaled by 1/√1 = 1
        return new_binding

    # Undo 1/√N, add binding, re-scale for 1/√(N+1)
    unscaled = glass_vector * math.sqrt(current_count)
    unscaled = unscaled + new_binding
    new_count = current_count + 1
    return unscaled * (1.0 / math.sqrt(new_count))
