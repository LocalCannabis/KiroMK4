"""
Kiro Configuration System

Loads configuration from:
1. Default config (config/default.yaml in package)
2. User config (~/.kiro/config/kiro.yaml)
3. Environment variables (KIRO_ prefix)

Uses Pydantic for validation and type coercion.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def expand_path(path: str | Path | None) -> Path | None:
    """Expand ~ and environment variables in paths."""
    if path is None:
        return None
    expanded = os.path.expandvars(os.path.expanduser(str(path)))
    return Path(expanded)


class KiroMeta(BaseModel):
    """Core Kiro metadata."""

    name: str = "Kiro"
    version: str = "0.1.0"


class LogConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"
    file: Path | None = None

    @field_validator("file", mode="before")
    @classmethod
    def expand_file_path(cls, v: Any) -> Path | None:
        return expand_path(v)


class DatabaseConfig(BaseModel):
    """Database configuration."""

    driver: Literal["sqlite", "postgresql"] = "sqlite"
    path: Path = Path("~/.kiro/data/kiro.db")
    echo: bool = False

    # PostgreSQL settings (future use)
    host: str | None = None
    port: int = 5432
    user: str | None = None
    password: str | None = None
    database: str | None = None

    @field_validator("path", mode="before")
    @classmethod
    def expand_db_path(cls, v: Any) -> Path:
        expanded = expand_path(v)
        return expanded if expanded else Path("~/.kiro/data/kiro.db")

    @property
    def url(self) -> str:
        """Generate SQLAlchemy connection URL."""
        if self.driver == "sqlite":
            # Ensure parent directory exists
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite+aiosqlite:///{self.path}"
        elif self.driver == "postgresql":
            if not all([self.host, self.user, self.database]):
                raise ValueError("PostgreSQL requires host, user, and database")
            auth = f"{self.user}:{self.password}@" if self.password else f"{self.user}@"
            return f"postgresql+asyncpg://{auth}{self.host}:{self.port}/{self.database}"
        else:
            raise ValueError(f"Unsupported database driver: {self.driver}")


class EventsConfig(BaseModel):
    """Event bus configuration."""

    max_queue_size: int = 1000
    handler_timeout: float = 30.0


class DaemonConfig(BaseModel):
    """Daemon process configuration."""

    shutdown_timeout: float = 5.0


class WakeWordConfig(BaseModel):
    """Wake word detection configuration."""

    threshold: float = 0.5
    refractory_period: float = 2.0


class VADConfig(BaseModel):
    """Voice activity detection configuration."""

    aggressiveness: int = 2
    min_speech_duration: float = 0.2
    max_silence_duration: float = 0.5  # Faster end-of-speech detection


class STTConfig(BaseModel):
    """Speech-to-text configuration."""

    engine: str = "auto"  # auto, faster-whisper, or whisper-api
    model: str = "base"  # faster-whisper: tiny/base/small/medium/large-v3, api: whisper-1
    language: str = "en"
    device: str = "auto"  # cuda, cpu, or auto
    compute_type: str = "float16"  # float16, int8_float16, int8


class TTSConfig(BaseModel):
    """Text-to-speech configuration."""

    engine: str = "piper"  # piper or openai
    piper_model: str = "en_US-lessac-high"  # High quality, natural voice
    piper_path: str | None = None  # Auto-detect if None
    models_dir: str | None = None  # Auto-detect if None
    openai_voice: str = "nova"


class LLMConfig(BaseModel):
    """LLM configuration."""

    primary_provider: str = "openai"  # claude or openai
    fallback_provider: str | None = None
    claude_model: str = "claude-sonnet-4-20250514"
    openai_model: str = "gpt-4o-mini"  # Fast model for responsiveness
    max_tokens: int = 256
    temperature: float = 0.7
    timeout: float = 30.0


class AudioConfig(BaseModel):
    """Audio pipeline configuration."""

    enabled: bool = True
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration: float = 0.1
    input_device: str | None = None
    output_device: str | None = None
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)


class KiroConfig(BaseSettings):
    """
    Main Kiro configuration.

    Loads from YAML files and environment variables.
    Environment variables use KIRO_ prefix and __ for nesting.
    Example: KIRO_LOG__LEVEL=DEBUG
    """

    model_config = SettingsConfigDict(
        env_prefix="KIRO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    kiro: KiroMeta = Field(default_factory=KiroMeta)
    log: LogConfig = Field(default_factory=LogConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def find_config_file() -> Path | None:
    """
    Find the configuration file.

    Search order:
    1. ~/.kiro/config/kiro.yaml (user config)
    2. ./config/default.yaml (development default)
    3. Package default (installed)
    """
    user_config = Path.home() / ".kiro" / "config" / "kiro.yaml"
    if user_config.exists():
        return user_config

    # Development: look for config relative to cwd
    dev_config = Path.cwd() / "config" / "default.yaml"
    if dev_config.exists():
        return dev_config

    # Package default: relative to this file
    package_config = Path(__file__).parent.parent.parent.parent / "config" / "default.yaml"
    if package_config.exists():
        return package_config

    return None


def load_yaml_config(path: Path | None) -> dict[str, Any]:
    """Load configuration from YAML file."""
    if path is None or not path.exists():
        return {}

    with open(path) as f:
        data = yaml.safe_load(f)

    return data if data else {}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries, with override taking precedence."""
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def load_config() -> KiroConfig:
    """
    Load complete configuration.

    Merges:
    1. Pydantic defaults
    2. YAML file configuration
    3. Environment variables (highest priority)
    """
    # Load YAML config
    config_path = find_config_file()
    yaml_config = load_yaml_config(config_path)

    # Create config with YAML values, env vars auto-applied by pydantic-settings
    config = KiroConfig(**yaml_config)

    # Ensure data directory exists
    if config.database.driver == "sqlite":
        config.database.path.parent.mkdir(parents=True, exist_ok=True)

    return config


# Global config instance (lazy-loaded)
_config: KiroConfig | None = None


def get_config() -> KiroConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Reset the global configuration (useful for testing)."""
    global _config
    _config = None
