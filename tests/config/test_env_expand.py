"""Tests for environment-variable expansion in config values.

Covers :func:`agent.config.env_expand.expand_env` and the string-level
substitution it delegates to.  These are pure-function tests: no filesystem
access, and every environment mutation goes through ``monkeypatch`` so tests
stay order-independent.  The end-to-end path through the YAML loader is
covered by :class:`TestLoadConfigExpansion` at the bottom of this module.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.config.config import load_config
from agent.config.env_expand import expand_env


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


class TestSubstitute:
    """String-level expansion of ``${VAR}`` and ``${VAR:-default}``."""

    def test_set_variable_is_replaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPAND_TEST_TOKEN", "s3cret")
        assert expand_env("${EXPAND_TEST_TOKEN}") == "s3cret"

    def test_default_used_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXPAND_TEST_MISSING", raising=False)
        assert expand_env("${EXPAND_TEST_MISSING:-fallback}") == "fallback"

    def test_default_used_when_set_but_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXPAND_TEST_EMPTY_VAR", "")
        assert expand_env("${EXPAND_TEST_EMPTY_VAR:-fallback}") == "fallback"

    def test_env_value_wins_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPAND_TEST_PRESENT", "real")
        assert expand_env("${EXPAND_TEST_PRESENT:-fallback}") == "real"

    def test_empty_default_yields_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EXPAND_TEST_EMPTY", raising=False)
        assert expand_env("${EXPAND_TEST_EMPTY:-}") == ""

    def test_unset_without_default_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EXPAND_TEST_REQUIRED", raising=False)
        with pytest.raises(ValueError, match="EXPAND_TEST_REQUIRED"):
            expand_env("${EXPAND_TEST_REQUIRED}")

    def test_surrounding_literal_text_preserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXPAND_TEST_HOST", "matrix.example.com")
        assert expand_env("https://${EXPAND_TEST_HOST}/api") == (
            "https://matrix.example.com/api"
        )

    def test_multiple_placeholders_in_one_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXPAND_TEST_USER", "bot")
        monkeypatch.setenv("EXPAND_TEST_PASS", "pw")
        assert expand_env("${EXPAND_TEST_USER}:${EXPAND_TEST_PASS}") == "bot:pw"

    def test_repeated_placeholder_resolves_consistently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXPAND_TEST_REPEAT", "x")
        assert expand_env("${EXPAND_TEST_REPEAT}-${EXPAND_TEST_REPEAT}") == "x-x"

    def test_string_without_placeholder_unchanged(self) -> None:
        assert expand_env("plain value") == "plain value"

    # -- Intended behaviour: unsupported syntax is left verbatim -------------

    def test_bare_dollar_var_left_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXPAND_TEST_BARE", "value")
        assert expand_env("$EXPAND_TEST_BARE") == "$EXPAND_TEST_BARE"

    def test_empty_braces_left_verbatim(self) -> None:
        assert expand_env("${}") == "${}"

    def test_invalid_identifier_left_verbatim(self) -> None:
        assert expand_env("${1BAD}") == "${1BAD}"

    def test_unterminated_placeholder_left_verbatim(self) -> None:
        assert expand_env("${EXPAND_TEST_UNCLOSED") == "${EXPAND_TEST_UNCLOSED"


class TestExpandEnv:
    """Recursive expansion across nested containers."""

    @pytest.mark.parametrize("value", [1, 1.5, True, False, None])
    def test_non_string_scalars_pass_through(self, value: object) -> None:
        assert expand_env(value) == value

    def test_nested_dict_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPAND_TEST_NESTED", "deep")
        obj = {"outer": {"inner": "${EXPAND_TEST_NESTED}"}}
        assert expand_env(obj) == {"outer": {"inner": "deep"}}

    def test_nested_list_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPAND_TEST_ITEM", "one")
        obj = {"items": ["${EXPAND_TEST_ITEM}", "literal"]}
        assert expand_env(obj) == {"items": ["one", "literal"]}

    def test_list_of_dicts_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPAND_TEST_ENTRY", "v")
        obj = [{"k": "${EXPAND_TEST_ENTRY}"}]
        assert expand_env(obj) == [{"k": "v"}]

    def test_empty_containers_returned_as_is(self) -> None:
        assert expand_env({}) == {}
        assert expand_env([]) == []

    def test_input_not_mutated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPAND_TEST_IMMUTABLE", "expanded")
        source = {"key": "${EXPAND_TEST_IMMUTABLE}"}
        result = expand_env(source)
        assert result == {"key": "expanded"}
        assert source == {"key": "${EXPAND_TEST_IMMUTABLE}"}

    def test_error_propagates_from_nested_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EXPAND_TEST_DEEP_MISSING", raising=False)
        obj = {"outer": ["${EXPAND_TEST_DEEP_MISSING}"]}
        with pytest.raises(ValueError, match="EXPAND_TEST_DEEP_MISSING"):
            expand_env(obj)

    # -- Intended behaviour: keys are not expanded --------------------------

    def test_dict_keys_not_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPAND_TEST_KEY", "expanded")
        obj = {"${EXPAND_TEST_KEY}": "value"}
        assert expand_env(obj) == {"${EXPAND_TEST_KEY}": "value"}


class TestLoadConfigExpansion:
    """End-to-end: the YAML loader expands env vars before validation."""

    def test_env_var_expanded_in_loaded_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid_config_dict: dict[str, object],
    ) -> None:
        monkeypatch.setenv("EXPAND_TEST_API_KEY", "sk-from-env")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "matrix-token")
        provider = valid_config_dict["model_provider"]
        assert isinstance(provider, dict)
        provider["api_key"] = "${EXPAND_TEST_API_KEY}"

        config = load_config(write_config(tmp_path, valid_config_dict))

        assert config.model_provider.api_key == "sk-from-env"

    def test_default_used_when_env_var_unset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid_config_dict: dict[str, object],
    ) -> None:
        monkeypatch.delenv("EXPAND_TEST_ABSENT_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "openai-token")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")
        monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "matrix-token")
        heartbeat = valid_config_dict["heartbeat"]
        assert isinstance(heartbeat, dict)
        heartbeat["output_channel"] = "${EXPAND_TEST_ABSENT_KEY:-fallback-channel}"

        config = load_config(write_config(tmp_path, valid_config_dict))

        assert config.heartbeat.output_channel == "fallback-channel"
