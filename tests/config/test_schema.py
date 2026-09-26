"""Tests for the pydantic config schema.

Covers field validation on :class:`ModelProvider` (the ``api`` allow-list
and required fields) and structural validation on :class:`Config` (required
sections and propagation of nested errors).  These tests exercise the models
directly, without touching the filesystem; the YAML loading path is covered
in ``test_config.py``.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from agent.config.schema import Config, MatrixAdapter, Model, ModelProvider


@pytest.fixture
def valid_provider() -> dict[str, str]:
    """Baseline provider payload; tests override single keys as needed."""
    return {
        "name": "litellm",
        "api": "openai_compatible",
        "api_key": "sk-test",
        "endpoint": "https://api.example.com",
    }


@pytest.fixture
def valid_config(valid_provider: dict[str, str]) -> dict[str, Any]:
    """Baseline full config payload built from :func:`valid_provider`."""
    return {
        "model_provider": valid_provider,
        "model": {"name": "gpt-5.6-luna", "effort": "high"},
        "discord_adapter": {"bot_token": "token"},
        "heartbeat": {
            "interval": 30,
            "prompt_file": "HEARTBEAT.md",
            "output_adapter": "discord",
            "output_channel": "12345",
        },
        "matrix_adapter": {
            "homeserver_url": "https://matrix.example.com",
            "access_token": "syt_token",
            "user_id": "@bot:matrix.example.com",
        },
    }


class TestModelProvider:
    """Validation of the ``model_provider`` section."""

    def test_valid_chat_api(self, valid_provider: dict[str, str]) -> None:
        provider = ModelProvider(**valid_provider)
        assert provider.api == "openai_compatible"
        assert provider.name == "litellm"

    def test_valid_openai_api(self, valid_provider: dict[str, str]) -> None:
        provider = ModelProvider(**{**valid_provider, "api": "openai"})
        assert provider.api == "openai"

    def test_valid_anthropic_api(self, valid_provider: dict[str, str]) -> None:
        provider = ModelProvider(**{**valid_provider, "api": "anthropic"})
        assert provider.api == "anthropic"

    def test_invalid_api_rejected(self, valid_provider: dict[str, str]) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ModelProvider(**{**valid_provider, "api": "vertex"})
        assert "api" in str(exc_info.value)

    def test_missing_field_rejected(self, valid_provider: dict[str, str]) -> None:
        del valid_provider["endpoint"]
        with pytest.raises(ValidationError):
            ModelProvider(**valid_provider)


class TestMatrixAdapter:
    """Validation of the ``matrix_adapter`` section."""

    def test_required_fields(self) -> None:
        adapter = MatrixAdapter(
            homeserver_url="https://matrix.example.com",
            access_token="syt_token",
            user_id="@bot:matrix.example.com",
        )
        assert adapter.homeserver_url == "https://matrix.example.com"
        assert adapter.access_token == "syt_token"
        assert adapter.user_id == "@bot:matrix.example.com"

    def test_optional_fields_default(self) -> None:
        adapter = MatrixAdapter(
            homeserver_url="https://matrix.example.com",
            access_token="syt_token",
            user_id="@bot:matrix.example.com",
        )
        assert adapter.device_id == ""
        assert adapter.store_path == ""
        assert adapter.ignore_unverified_devices is True

    def test_optional_fields_override(self) -> None:
        adapter = MatrixAdapter(
            homeserver_url="https://matrix.example.com",
            access_token="syt_token",
            user_id="@bot:matrix.example.com",
            device_id="DEVICEID",
            store_path="/tmp/nio_store",
            ignore_unverified_devices=False,
        )
        assert adapter.device_id == "DEVICEID"
        assert adapter.store_path == "/tmp/nio_store"
        assert adapter.ignore_unverified_devices is False

    def test_missing_required_field_defaults_to_empty(self) -> None:
        adapter = MatrixAdapter(
            homeserver_url="https://matrix.example.com",
            access_token="syt_token",
        )
        assert adapter.user_id == ""


class TestConfig:
    """Validation of the top-level :class:`Config` model."""

    def test_valid_config(self, valid_config: dict[str, Any]) -> None:
        config = Config(**valid_config)
        assert config.model.name == "gpt-5.6-luna"
        assert config.model_provider.api == "openai_compatible"
        assert config.discord_adapter.bot_token == "token"
        assert config.matrix_adapter.user_id == "@bot:matrix.example.com"

    def test_missing_section_rejected(self, valid_config: dict[str, Any]) -> None:
        del valid_config["model"]
        with pytest.raises(ValidationError):
            Config(**valid_config)

    def test_invalid_nested_value_propagates(
        self, valid_config: dict[str, Any]
    ) -> None:
        valid_config["model_provider"] = {
            "name": "openai",
            "api": "bogus",
            "api_key": "sk-test",
            "endpoint": "https://api.example.com",
        }
        with pytest.raises(ValidationError):
            Config(**valid_config)

    def test_model_effort_roundtrip(self) -> None:
        model = Model(name="gpt-5.6-luna", effort="low")
        assert model.effort == "low"
