"""
KIRO UI — Database Models
Chat sessions and messages, scoped per persona.
API tokens for remote client authentication.
"""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ChatSession(db.Model):
    """A conversation session with a specific persona."""

    __tablename__ = "kiro_chat_sessions"

    id = db.Column(db.Integer, primary_key=True)
    persona_key = db.Column(db.String(32), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False, default="New Session")
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    archived = db.Column(db.Boolean, nullable=False, default=False)

    messages = db.relationship(
        "ChatMessage",
        backref="session",
        lazy="dynamic",
        order_by="ChatMessage.created_at",
        cascade="all, delete-orphan",
    )

    def to_dict(self, message_count=False):
        d = {
            "id": self.id,
            "persona_key": self.persona_key,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "archived": self.archived,
        }
        if message_count:
            d["message_count"] = self.messages.count()
        return d


class ChatMessage(db.Model):
    """A single message within a chat session."""

    __tablename__ = "kiro_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("kiro_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = db.Column(db.String(16), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }


class KiroApiToken(db.Model):
    """
    Bearer token for remote client authentication (e.g., iMac over Tailscale).
    The plaintext token is never stored — only a bcrypt hash.
    Localhost requests bypass token checking entirely.
    """

    __tablename__ = "kiro_api_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.Text, nullable=False)
    label = db.Column(db.String(100))  # e.g. "work-imac"
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked": self.revoked,
        }


class ModelConfig(db.Model):
    """
    Per-persona (or global) model routing preferences.

    key = 'global'  → applies to all personas unless overridden
    key = persona   → e.g. 'kiro', 'finley', 'jack', etc.

    policy:
      auto         — local for fast-tier only; cloud otherwise  (default)
      local-first  — prefer local for fast+balanced; cloud for deep + fallback
      cloud-first  — always cloud, never local
      cloud-only   — cloud only; local is never attempted

    ceiling:
      fast / balanced / deep  — maximum tier that will be used
      (e.g. ceiling=balanced prevents Opus even on deep-pattern messages)

    force_model:
      Pin to a specific model name, bypassing all triage logic.
      Set to NULL/empty to remove the pin.
    """

    __tablename__ = "kiro_model_config"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), nullable=False, unique=True)
    policy = db.Column(db.String(32), nullable=False, default="auto")
    ceiling = db.Column(db.String(32), nullable=False, default="deep")
    force_model = db.Column(db.String(128), nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "key":         self.key,
            "policy":      self.policy,
            "ceiling":     self.ceiling,
            "force_model": self.force_model,
            "updated_at":  self.updated_at.isoformat(),
        }


class LLMUsageLog(db.Model):
    """
    Per-request LLM usage for cost tracking and spend analysis.
    Token counts come from actual API responses where available,
    otherwise estimated from character counts (~4 chars/token).
    """

    __tablename__ = "kiro_llm_usage"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    persona_key = db.Column(db.String(32), nullable=True, index=True)
    session_id = db.Column(db.Integer, nullable=True)
    provider = db.Column(db.String(32), nullable=False)   # anthropic/openai/local
    model = db.Column(db.String(128), nullable=False)
    tier = db.Column(db.String(32), nullable=False)        # fast/balanced/deep/local-fast/...
    tokens_in = db.Column(db.Integer, default=0)
    tokens_out = db.Column(db.Integer, default=0)
    cost_usd = db.Column(db.Float, default=0.0)
    latency_ms = db.Column(db.Integer, default=0)
    source = db.Column(db.String(16), default="chat")      # 'chat' / 'voice' / 'ambient'

    def to_dict(self):
        return {
            "id":          self.id,
            "created_at":  self.created_at.isoformat(),
            "persona_key": self.persona_key,
            "provider":    self.provider,
            "model":       self.model,
            "tier":        self.tier,
            "tokens_in":   self.tokens_in,
            "tokens_out":  self.tokens_out,
            "cost_usd":    self.cost_usd,
            "latency_ms":  self.latency_ms,
            "source":      self.source,
        }
