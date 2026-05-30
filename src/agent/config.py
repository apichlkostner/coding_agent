"""Runtime configuration loaded from environment variables.

Copy ``.env.example`` → ``.env`` and fill in your API keys before running.

Provider switching is done via the ``LLM_PROVIDER`` env var:
    LLM_PROVIDER=openai      # uses ChatOpenAI  (default)
    LLM_PROVIDER=anthropic   # uses ChatAnthropic
    LLM_PROVIDER=ollama      # uses ChatOllama (local/hosted)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

# Load .env at import time so settings are always populated.
load_dotenv()

Provider = Literal["openai", "anthropic", "ollama"]

# Default models per provider — override with MODEL_NAME env var.
_DEFAULT_MODELS: dict[Provider, str] = {
    "openai": "gpt-5.4-nano",
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "qwen2.5-coder:14b",
}


@dataclass(frozen=True)
class HeartbeatSettings:
    """Configuration for the :class:`~agent.adapters.heartbeat_adapter.HeartbeatAdapter`."""

    interval_seconds: int = field(default=600)
    """How long to sleep between heartbeat runs (seconds)."""

    prompt_file: str = field(default="HEARTBEAT.md")
    """Path to the Markdown file whose content is sent to the agent each tick."""

    output_adapter_id: str = field(default="")
    """Adapter ID to forward heartbeat responses to (e.g. ``"discord"``).  Empty = log only."""

    output_channel_id: str = field(default="")
    """Adapter-specific destination for forwarded responses (e.g. a Discord channel ID)."""

_ALL_ADAPTERS: frozenset[str] = frozenset({"terminal", "discord", "heartbeat", "matrix"})


@dataclass(frozen=True)
class MatrixSettings:
    """Configuration for the :class:`~agent.adapters.matrix_adapter.MatrixAdapter`."""

    homeserver_url: str = field(default="")
    """Base URL of the Matrix homeserver (e.g. ``https://matrix.org``)."""

    access_token: str = field(default="")
    """Bot access token obtained from the homeserver."""

    user_id: str = field(default="")
    """Fully-qualified Matrix user ID of the bot (e.g. ``@bot:matrix.org``)."""


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    llm_provider: Provider = field(default="openai")
    model_name: str = field(default="")
    temperature: float = field(default=0.0)
    discord_token: str = field(default="")
    ollama_base_url: str = field(default="http://localhost:11434")
    heartbeat: HeartbeatSettings = field(default_factory=HeartbeatSettings)
    matrix: MatrixSettings = field(default_factory=MatrixSettings)
    enabled_adapters: frozenset[str] = field(
        default_factory=lambda: frozenset({"terminal", "discord", "heartbeat"})
    )
    """Set of adapter IDs that should be started.  Controlled by ``ENABLED_ADAPTERS``."""

    @property
    def resolved_model(self) -> str:
        """Return the explicit model name, or the provider default."""
        return self.model_name or _DEFAULT_MODELS[self.llm_provider]


def get_settings() -> Settings:
    """Build a ``Settings`` instance from environment variables."""
    raw_provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if raw_provider not in ("openai", "anthropic", "ollama"):
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{raw_provider}'. "
            "Choose 'openai', 'anthropic', or 'ollama'."
        )
    discord_token = os.getenv("DISCORD_BOT_TOKEN", "")
    heartbeat = HeartbeatSettings(
        interval_seconds=int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "600")),
        prompt_file=os.getenv("HEARTBEAT_PROMPT_FILE", "HEARTBEAT.md"),
        output_adapter_id=os.getenv("HEARTBEAT_OUTPUT_ADAPTER", ""),
        output_channel_id=os.getenv("HEARTBEAT_OUTPUT_CHANNEL", ""),
    )
    matrix = MatrixSettings(
        homeserver_url=os.getenv("MATRIX_HOMESERVER_URL", ""),
        access_token=os.getenv("MATRIX_ACCESS_TOKEN", ""),
        user_id=os.getenv("MATRIX_USER_ID", ""),
    )
    raw_enabled = os.environ.get("ENABLED_ADAPTERS")
    enabled_adapters: frozenset[str] = (
        frozenset({"terminal", "discord", "heartbeat"})
        if raw_enabled is None
        else frozenset(a.strip() for a in raw_enabled.split(",") if a.strip())
    )
    return Settings(
        llm_provider=raw_provider,  # type: ignore[arg-type]
        model_name=os.getenv("MODEL_NAME", ""),
        temperature=float(os.getenv("TEMPERATURE", "0")),
        discord_token=discord_token,
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        heartbeat=heartbeat,
        matrix=matrix,
        enabled_adapters=enabled_adapters,
    )


def get_llm(settings: Settings | None = None) -> BaseChatModel:
    """Instantiate and return the configured chat model.

    Parameters
    ----------
    settings:
        Pass an explicit ``Settings`` object or leave ``None`` to call
        ``get_settings()`` automatically.
    """
    cfg = settings or get_settings()

    if cfg.llm_provider == "openai":
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        return ChatOpenAI(
            model=cfg.resolved_model,
            temperature=cfg.temperature,
        )

    if cfg.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415

        return ChatAnthropic(
            model=cfg.resolved_model,
            temperature=cfg.temperature,
        )

    if cfg.llm_provider == "ollama":
        from langchain_ollama import ChatOllama  # noqa: PLC0415

        return ChatOllama(
            model=cfg.resolved_model,
            temperature=cfg.temperature,
            base_url=cfg.ollama_base_url,
        )

    raise ValueError(
        f"Unsupported LLM provider: {cfg.llm_provider}. "
        "Expected one of: openai, anthropic, ollama."
    )
