"""
KIRO UI — LLM Triage Router

Tiered model routing to minimise API spend.

Tier order (cheapest → most capable):
  local-fast     — Ollama small model on Beast GPU    (~$0/req)
  local-capable  — Ollama larger model on Beast GPU   (~$0/req)
  fast           — Claude Haiku / GPT-4o-mini         (~$0.001/req avg)
  balanced       — Claude Sonnet                      (~$0.01/req avg)
  deep           — Claude Opus                        (~$0.05/req avg)

Routing decision flow:
  1. Caller classifies message complexity → 'fast' | 'balanced' | 'deep'
  2. Apply persona floor (from config.yaml)  ← done in _classify_complexity
  3. Load per-persona / global policy + ceiling from DB (ModelConfig)
  4. Apply ceiling  (don't exceed configured max tier)
  5. Based on policy:
       cloud-only   → always cloud, use cloud_models[complexity]
       local-first  → prefer local, fall back to cloud on failure/unavailable
       auto         → local for fast-tier only (if Ollama healthy); else cloud
       cloud-first  → always cloud (never local)
  6. Return routing dict consumed by /api/chat
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import requests

log = logging.getLogger("kiro.model_router")

# ---------------------------------------------------------------------------
# Tier ordering — index 0 = cheapest/fastest
# ---------------------------------------------------------------------------
TIERS = ["local-fast", "local-capable", "fast", "balanced", "deep"]
CLOUD_TIERS = ["fast", "balanced", "deep"]   # what _classify_complexity returns

# ---------------------------------------------------------------------------
# Cost table — USD per 1 million tokens  (input_cost, output_cost)
# Sources: anthropic.com/pricing, openai.com/pricing  (April 2026)
# ---------------------------------------------------------------------------
COST_TABLE: Dict[str, Tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5":             (0.80,   4.00),
    "claude-sonnet-4-6":            (3.00,  15.00),
    "claude-opus-4-6":             (15.00,  75.00),
    "claude-3-5-haiku-20241022":    (0.80,   4.00),
    "claude-3-5-sonnet-20241022":   (3.00,  15.00),
    "claude-3-opus-20240229":      (15.00,  75.00),
    # OpenAI
    "gpt-4o":                       (5.00,  15.00),
    "gpt-4o-mini":                  (0.15,   0.60),
    "gpt-4-turbo":                 (10.00,  30.00),
    # Local — zero marginal cost
    "__local__":                    (0.00,   0.00),
}


def estimate_cost(tokens_in: int, tokens_out: int, model: str) -> float:
    """Return estimated USD cost for one request."""
    rates = COST_TABLE.get(model) or COST_TABLE["__local__"]
    return round(
        (tokens_in  / 1_000_000) * rates[0] +
        (tokens_out / 1_000_000) * rates[1],
        8,
    )


def tier_index(tier: str) -> int:
    """Numeric index for a tier string (higher = more capable / expensive)."""
    try:
        return TIERS.index(tier)
    except ValueError:
        return TIERS.index("balanced")


class ModelRouter:
    """
    Stateful LLM triage router — one instance per Flask app.

    State maintained:
      - Cached Ollama health (refreshed in background thread every N seconds)
      - Cached ModelConfig rows from DB (refreshed every 60 s)
    """

    POLICY_DEFAULT = "auto"

    def __init__(self, routing_cfg: Dict[str, Any], local_cfg: Dict[str, Any]):
        """
        routing_cfg : MODEL_ROUTING dict from config.py
        local_cfg   : LOCAL_MODEL_CONFIG dict from config.py
        """
        self._routing = routing_cfg
        self._local = local_cfg
        self._local_ok: Optional[bool] = None
        self._local_last_check: float = 0.0
        self._local_interval: float = float(
            local_cfg.get("health_check_interval_s", 30)
        )
        self._lock = threading.Lock()
        self._cfg_cache: Dict[str, Any] = {}
        self._cfg_ts: float = 0.0

    # ------------------------------------------------------------------
    # Ollama health
    # ------------------------------------------------------------------

    def _probe_local(self) -> bool:
        base = self._local.get("base_url", "http://127.0.0.1:11434")
        try:
            r = requests.get(f"{base}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def local_healthy(self) -> bool:
        """
        Return cached Ollama availability; stale checks kick off a background
        refresh while returning the last known value immediately.
        """
        if not self._local.get("enabled", False):
            return False
        now = time.monotonic()
        with self._lock:
            stale = (now - self._local_last_check) > self._local_interval
            first = self._local_ok is None
            if first or stale:
                threading.Thread(target=self._bg_health_check, daemon=True).start()
                if first:
                    # Block briefly on first call only — we have no cached value
                    self._local_ok = self._probe_local()
                    self._local_last_check = now
            return bool(self._local_ok)

    def _bg_health_check(self) -> None:
        result = self._probe_local()
        with self._lock:
            changed = result != self._local_ok
            self._local_ok = result
            self._local_last_check = time.monotonic()
        if changed:
            log.info("Ollama health changed → %s", "UP" if result else "DOWN")

    # ------------------------------------------------------------------
    # Per-persona config from DB
    # ------------------------------------------------------------------

    def _load_db_configs(self) -> Dict[str, Any]:
        now = time.monotonic()
        if now - self._cfg_ts < 60.0:
            return self._cfg_cache
        try:
            from models import ModelConfig  # noqa: avoid circular import at top
            rows = ModelConfig.query.all()
            self._cfg_cache = {r.key: r.to_dict() for r in rows}
        except Exception as exc:
            log.debug("ModelConfig query skipped (first run or missing table): %s", exc)
        self._cfg_ts = now
        return self._cfg_cache

    def get_persona_config(self, persona_key: str) -> Dict[str, Any]:
        """
        Effective config for a persona: persona row → global row → hard defaults.
        """
        db = self._load_db_configs()
        glb = db.get("global", {})
        per = db.get(persona_key, {})
        return {
            "policy":      per.get("policy")      or glb.get("policy")      or self.POLICY_DEFAULT,
            "ceiling":     per.get("ceiling")     or glb.get("ceiling")     or "deep",
            "force_model": per.get("force_model") or glb.get("force_model") or None,
        }

    def invalidate_config(self) -> None:
        """Force DB reload on next request (call after ModelConfig is updated)."""
        self._cfg_ts = 0.0

    # ------------------------------------------------------------------
    # Core routing
    # ------------------------------------------------------------------

    def route(self, complexity: str, persona_key: str) -> Dict[str, Any]:
        """
        Given a pre-classified complexity tier and persona key, return a
        routing dict:

          {
            provider:    "anthropic" | "openai" | "local"
            model:       "<model name>"
            tier:        "fast" | "balanced" | "deep" | "local-fast" | "local-capable"
            reason:      "<why this was chosen>"
            base_url:    str | None
            api_key_env: str | None
          }

        complexity should be one of: 'fast', 'balanced', 'deep'
        """
        cfg          = self.get_persona_config(persona_key)
        policy       = cfg["policy"]
        ceiling      = cfg["ceiling"]
        force        = cfg.get("force_model")
        cloud_models = self._routing.get("models", {})
        local_models = self._local.get("models", {})
        local_base   = self._local.get("base_url", "http://127.0.0.1:11434")

        # ── Forced model pin — skip all triage ───────────────────────────
        if force:
            return self._cloud(force, complexity, "forced-override")

        # ── Apply ceiling (don't exceed configured max tier) ─────────────
        if tier_index(complexity) > tier_index(ceiling):
            complexity = ceiling
            # Snap to nearest valid cloud tier label if ceiling was a local tier
            if complexity in ("local-fast", "local-capable"):
                complexity = "fast"

        # ── cloud-only policy ────────────────────────────────────────────
        if policy == "cloud-only":
            return self._cloud(
                cloud_models.get(complexity, cloud_models["balanced"]),
                complexity,
                "cloud-only",
            )

        # ── Local availability ────────────────────────────────────────────
        local_ok = self.local_healthy()

        # ── local-first policy ────────────────────────────────────────────
        if policy == "local-first":
            if complexity in ("fast", "balanced") and local_ok:
                key = "fast" if complexity == "fast" else "capable"
                m   = local_models.get(key) or local_models.get("fast")
                if m:
                    return self._local_result(m, local_base, key, "local-first")
            # deep always goes cloud; also fallback when local is down
            return self._cloud(
                cloud_models.get(complexity, cloud_models["balanced"]),
                complexity,
                "local-first:cloud-fallback",
            )

        # ── auto (default): local only for fast-tier ─────────────────────
        if policy == "auto" and complexity == "fast" and local_ok:
            m = local_models.get("fast")
            if m:
                return self._local_result(m, local_base, "fast", "auto:fast-local")

        # ── cloud-first or auto fallback ──────────────────────────────────
        return self._cloud(
            cloud_models.get(complexity, cloud_models["balanced"]),
            complexity,
            policy,
        )

    # ------------------------------------------------------------------
    # Result builders
    # ------------------------------------------------------------------

    def _cloud(self, model: str, tier: str, reason: str) -> Dict[str, Any]:
        return {
            "provider":    self._routing.get("provider", "anthropic"),
            "model":       model,
            "tier":        tier,
            "reason":      reason,
            "base_url":    self._routing.get("base_url"),
            "api_key_env": self._routing.get("api_key_env", "ANTHROPIC_API_KEY"),
        }

    def _local_result(
        self, model: str, base: str, model_key: str, reason: str
    ) -> Dict[str, Any]:
        tier = "local-fast" if model_key == "fast" else "local-capable"
        return {
            "provider":    "local",
            "model":       model,
            "tier":        tier,
            "reason":      reason,
            "base_url":    f"{base}/v1",   # Ollama OpenAI-compat endpoint
            "api_key_env": None,
        }

    # ------------------------------------------------------------------
    # Status summary (for /api/models/status)
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return a full status dict for the settings UI."""
        return {
            "local": {
                "enabled": self._local.get("enabled", False),
                "healthy": self.local_healthy() if self._local.get("enabled") else False,
                "base_url": self._local.get("base_url"),
                "models": self._local.get("models", {}),
                "tools_supported": self._local.get("tools_supported", False),
            },
            "cloud": {
                "provider": self._routing.get("provider"),
                "models":   self._routing.get("models", {}),
            },
            "cost_table": {
                k: {"input_per_1m": v[0], "output_per_1m": v[1]}
                for k, v in COST_TABLE.items()
                if not k.startswith("__")
            },
        }
