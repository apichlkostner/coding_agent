"""Tests for the secret-key check in config values.

Covers :func:`agent.config.secret_check.secret_check`, which requires every
key holding a secret to be an env-expansion placeholder (``${VAR}`` or
``${VAR:-default}``) rather than a literal value.  The end-to-end path through
the YAML loader is covered by :class:`TestLoadConfigExpansion` at the bottom of
this module.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.config.config import ConfigError, load_config
from agent.config.secret_check import SecretError, secret_check


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
        },
    }


def write_config(tmp_path: Path, data: object) -> str:
    """Serialise *data* to ``config.yaml`` under *tmp_path* and return its path."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


class TestSecretCheck:
    def test_no_secrets(self, valid_config_dict: dict[str, object]) -> None:
        secret_check(valid_config_dict)

    def test_secret_key_provider(self, valid_config_dict: dict[str, object]) -> None:

        valid_config_dict["model_provider"]["api_key"] = "sk-12345abcde"
        with pytest.raises(SecretError, match=r"model_provider\.api_key"):
            secret_check(valid_config_dict)

    def test_secret_key_discord(self, valid_config_dict: dict[str, object]) -> None:
        valid_config_dict["discord_adapter"]["bot_token"] = "sk-12345abcde"
        with pytest.raises(SecretError, match=r"discord_adapter\.bot_token"):
            secret_check(valid_config_dict)

    def test_secret_key_matrix(self, valid_config_dict: dict[str, object]) -> None:
        valid_config_dict["matrix_adapter"]["access_token"] = "sk-12345abcde"
        with pytest.raises(SecretError, match=r"matrix_adapter\.access_token"):
            secret_check(valid_config_dict)

    def test_placeholder_with_empty_default_accepted(
        self, valid_config_dict: dict[str, object]
    ) -> None:
        valid_config_dict["model_provider"]["api_key"] = "${OPENAI_API_KEY:-}"
        secret_check(valid_config_dict)

    def test_placeholder_with_non_empty_default_rejected(
        self, valid_config_dict: dict[str, object]
    ) -> None:
        valid_config_dict["model_provider"]["api_key"] = (
            "${OPENAI_API_KEY:-sk-fallback}"
        )
        with pytest.raises(SecretError, match=r"model_provider\.api_key"):
            secret_check(valid_config_dict)

    def test_placeholder_with_surrounding_text_rejected(
        self, valid_config_dict: dict[str, object]
    ) -> None:
        valid_config_dict["model_provider"]["api_key"] = "Bearer ${OPENAI_API_KEY}"
        with pytest.raises(SecretError, match=r"model_provider\.api_key"):
            secret_check(valid_config_dict)

    def test_empty_secret_rejected(self, valid_config_dict: dict[str, object]) -> None:
        valid_config_dict["discord_adapter"]["bot_token"] = ""
        with pytest.raises(SecretError, match=r"discord_adapter\.bot_token"):
            secret_check(valid_config_dict)

    def test_non_string_secret_rejected(
        self, valid_config_dict: dict[str, object]
    ) -> None:
        valid_config_dict["matrix_adapter"]["access_token"] = 12345
        with pytest.raises(SecretError, match=r"matrix_adapter\.access_token"):
            secret_check(valid_config_dict)

    def test_missing_secret_key_accepted(self) -> None:
        secret_check({"model": {"name": "gpt-5.6-luna"}})

    def test_non_mapping_section_accepted(self) -> None:
        secret_check({"model_provider": "not-a-mapping"})

    def test_first_offending_key_reported(
        self, valid_config_dict: dict[str, object]
    ) -> None:
        valid_config_dict["model_provider"]["api_key"] = "sk-12345abcde"
        valid_config_dict["discord_adapter"]["bot_token"] = "sk-12345abcde"
        with pytest.raises(SecretError, match=r"model_provider\.api_key"):
            secret_check(valid_config_dict)


class TestLoadConfigExpansion:
    def test_secret_in_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid_config_dict: dict[str, object],
    ) -> None:
        provider = valid_config_dict["model_provider"]
        assert isinstance(provider, dict)
        provider["api_key"] = "sk-1234abcd"
        with pytest.raises(ConfigError, match=r"model_provider\.api_key"):
            _config = load_config(write_config(tmp_path, valid_config_dict))

    def test_placeholder_secret_expanded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid_config_dict: dict[str, object],
    ) -> None:
        monkeypatch.setenv("SECRET_CHECK_API_KEY", "sk-from-env")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "matrix-token")
        provider = valid_config_dict["model_provider"]
        assert isinstance(provider, dict)
        provider["api_key"] = "${SECRET_CHECK_API_KEY}"

        config = load_config(write_config(tmp_path, valid_config_dict))

        assert config.model_provider.api_key == "sk-from-env"
