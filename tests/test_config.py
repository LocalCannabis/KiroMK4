"""
Tests for Kiro configuration system.
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from kiro.config import (
    KiroConfig,
    load_yaml_config,
    deep_merge,
    expand_path,
    reset_config,
)


class TestExpandPath:
    """Tests for path expansion."""

    def test_expand_home(self):
        result = expand_path("~/test")
        assert result is not None
        assert str(result).startswith(str(Path.home()))
        assert str(result).endswith("test")

    def test_expand_env_var(self):
        os.environ["TEST_KIRO_PATH"] = "/custom/path"
        result = expand_path("$TEST_KIRO_PATH/subdir")
        assert result is not None
        assert str(result) == "/custom/path/subdir"
        del os.environ["TEST_KIRO_PATH"]

    def test_expand_none(self):
        assert expand_path(None) is None


class TestLoadYamlConfig:
    """Tests for YAML loading."""

    def test_load_valid_yaml(self, tmp_path: Path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(
            """
            log:
              level: DEBUG
            database:
              echo: true
            """
        )
        result = load_yaml_config(config_file)
        assert result["log"]["level"] == "DEBUG"
        assert result["database"]["echo"] is True

    def test_load_missing_file(self):
        result = load_yaml_config(Path("/nonexistent/config.yaml"))
        assert result == {}

    def test_load_empty_file(self, tmp_path: Path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        result = load_yaml_config(config_file)
        assert result == {}


class TestDeepMerge:
    """Tests for deep dictionary merging."""

    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 3, "c": 4}}
        result = deep_merge(base, override)
        assert result == {"outer": {"a": 1, "b": 3, "c": 4}}

    def test_override_dict_with_value(self):
        base = {"a": {"nested": True}}
        override = {"a": "simple"}
        result = deep_merge(base, override)
        assert result == {"a": "simple"}


class TestKiroConfig:
    """Tests for main configuration class."""

    def setup_method(self):
        reset_config()

    def test_defaults(self):
        config = KiroConfig()
        assert config.kiro.name == "Kiro"
        assert config.log.level == "INFO"
        assert config.database.driver == "sqlite"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KIRO_LOG__LEVEL", "DEBUG")
        config = KiroConfig()
        assert config.log.level == "DEBUG"

    def test_database_url_sqlite(self, tmp_path: Path):
        config = KiroConfig(
            database={"driver": "sqlite", "path": str(tmp_path / "test.db")}
        )
        url = config.database.url
        assert url.startswith("sqlite+aiosqlite:///")
        assert "test.db" in url

    def test_database_url_postgresql(self):
        config = KiroConfig(
            database={
                "driver": "postgresql",
                "host": "localhost",
                "user": "kiro",
                "database": "kiro_db",
            }
        )
        url = config.database.url
        assert url.startswith("postgresql+asyncpg://")
        assert "kiro@localhost" in url
