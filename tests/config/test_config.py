"""Tests for the YAML config loader.

Each test writes its own config file into ``tmp_path`` so the loader's
file I/O, YAML parsing and validation wrapping are exercised end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.config.config import ConfigError, load_config
from agent.config.schema import Config


@pytest.fixture
def valid_config_dict() -> dict[str, object]:
    """Baseline config payload; tests mutate single keys as needed."""
    return {
        "model_provider": {
            "name": "litellm",
            "api": "openai_compatible",
            "api_key": "${OPENAI_API_KEY}",
            "endpoint": "https://api.example.com",
        },
        "model": {"name": "gpt-5.6-luna", "effort": "high"},
        "discord_adapter": {"bot_token": "${DISCORD_BOT_TOKEN}"},
        "heartbeat": {
            "interval": 30,
            "prompt_file": "HEARTBEAT.md",
            "output_adapter": "discord",
            "output_channel": "12345",
        },
        "matrix_adapter": {
            "homeserver_url": "https://matrix.example.com",
            "access_token": "${MATRIX_ACCESS_TOKEN}",
            "user_id": "@bot:matrix.example.com",
            "device_id": "DEVICEID",
            "store_path": "/tmp/nio_store",
            "ignore_unverified_devices": True,
        },
    }


def write_config(tmp_path: Path, data: object) -> str:
    """Serialise *data* to ``config.yaml`` under *tmp_path* and return its path."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


class TestLoadConfig:
    """End-to-end tests for :func:`agent.config.config.load_config`."""

    def test_loads_valid_file(
        self,
        tmp_path: Path,
        valid_config_dict: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-1234abcd")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "98761234")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "56784321")
        config = load_config(write_config(tmp_path, valid_config_dict))

        assert isinstance(config, Config)
        assert config.model.name == "gpt-5.6-luna"
        assert config.model.effort == "high"
        assert config.model_provider.api == "openai_compatible"
        assert config.model_provider.api_key == "sk-1234abcd"
        assert config.discord_adapter.bot_token == "98761234"
        assert config.heartbeat.interval == 30
        assert config.heartbeat.prompt_file == "HEARTBEAT.md"
        assert config.heartbeat.output_adapter == "discord"
        assert config.heartbeat.output_channel == "12345"
        assert config.matrix_adapter.homeserver_url == "https://matrix.example.com"
        assert config.matrix_adapter.access_token == "56784321"
        assert config.matrix_adapter.user_id == "@bot:matrix.example.com"
        assert config.matrix_adapter.device_id == "DEVICEID"
        assert config.matrix_adapter.store_path == "/tmp/nio_store"
        assert config.matrix_adapter.ignore_unverified_devices is True

    def test_missing_file_raises_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Config not found"):
            load_config(str(tmp_path / "does_not_exist.yaml"))

    def test_invalid_schema_raises_config_error(
        self,
        tmp_path: Path,
        valid_config_dict: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-1234abcd")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "98761234")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "56784321")
        provider = valid_config_dict["model_provider"]
        assert isinstance(provider, dict)
        provider["api"] = "vertex"

        with pytest.raises(ConfigError, match="Invalid config"):
            load_config(write_config(tmp_path, valid_config_dict))

    def test_validation_error_is_chained(
        self, tmp_path: Path, valid_config_dict: dict[str, object]
    ) -> None:
        del valid_config_dict["model"]

        with pytest.raises(ConfigError) as exc_info:
            load_config(write_config(tmp_path, valid_config_dict))

        assert exc_info.value.__cause__ is not None

    def test_malformed_yaml_raises_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("model_provider: [unclosed\n")

        with pytest.raises(ConfigError, match="Malformed YAML"):
            load_config(str(path))

    def test_empty_file_raises_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("")

        with pytest.raises(ConfigError, match="Empty config"):
            load_config(str(path))

    def test_non_mapping_root_raises_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("- just\n- a\n- list\n")

        with pytest.raises(ConfigError, match="must be a mapping"):
            load_config(str(path))

    def test_directory_path_raises_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_config(str(tmp_path))

    def test_missing_env_var_raises_config_error(
        self,
        tmp_path: Path,
        valid_config_dict: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "98761234")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "56784321")
        provider = valid_config_dict["model_provider"]
        assert isinstance(provider, dict)
        provider["api_key"] = "${CONFIG_TEST_MISSING_VAR}"

        with pytest.raises(ConfigError, match="CONFIG_TEST_MISSING_VAR"):
            load_config(write_config(tmp_path, valid_config_dict))

    def test_unknown_key_raises_config_error(
        self,
        tmp_path: Path,
        valid_config_dict: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-1234abcd")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "98761234")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "56784321")
        provider = valid_config_dict["model_provider"]
        assert isinstance(provider, dict)
        provider["api_kye"] = "typo"

        with pytest.raises(ConfigError, match="Invalid config"):
            load_config(write_config(tmp_path, valid_config_dict))

    def test_unknown_top_level_key_raises_config_error(
        self,
        tmp_path: Path,
        valid_config_dict: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-1234abcd")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "98761234")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "56784321")
        valid_config_dict["unknown_section"] = {"key": "value"}

        with pytest.raises(ConfigError, match="Invalid config"):
            load_config(write_config(tmp_path, valid_config_dict))

    def test_adapter_sections_optional(
        self,
        tmp_path: Path,
        valid_config_dict: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-1234abcd")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "98761234")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "56784321")
        del valid_config_dict["discord_adapter"]
        del valid_config_dict["matrix_adapter"]
        del valid_config_dict["heartbeat"]

        config = load_config(write_config(tmp_path, valid_config_dict))

        assert config.discord_adapter.bot_token == ""
        assert config.matrix_adapter.homeserver_url == ""
        assert config.heartbeat.interval == 600
