"""Runtime configuration loaded from environment variables.

Copy ``.env.example`` → ``.env`` and fill in your API keys before running.

Provider switching is done via the ``LLM_PROVIDER`` env var:
    LLM_PROVIDER=openai      # uses ChatOpenAI  (default)
    LLM_PROVIDER=anthropic   # uses ChatAnthropic
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

# Load .env at import time so settings are always populated.
load_dotenv()

Provider = Literal["openai", "anthropic"]

# Default models per provider — override with MODEL_NAME env var.
_DEFAULT_MODELS: dict[Provider, str] = {
    "openai": "gpt-5.4-nano",
    "anthropic": "claude-haiku-4-5-20251001",
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

_ALL_ADAPTERS: frozenset[str] = frozenset({"terminal", "discord", "heartbeat"})


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    llm_provider: Provider = field(default="openai")
    model_name: str = field(default="")
    temperature: float = field(default=0.0)
    discord_token: str = field(default="")
    heartbeat: HeartbeatSettings = field(default_factory=HeartbeatSettings)
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
    if raw_provider not in ("openai", "anthropic"):
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{raw_provider}'. "
            "Choose 'openai' or 'anthropic'."
        )
    discord_token = os.getenv("DISCORD_BOT_TOKEN", "")
    heartbeat = HeartbeatSettings(
        interval_seconds=int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "600")),
        prompt_file=os.getenv("HEARTBEAT_PROMPT_FILE", "HEARTBEAT.md"),
        output_adapter_id=os.getenv("HEARTBEAT_OUTPUT_ADAPTER", ""),
        output_channel_id=os.getenv("HEARTBEAT_OUTPUT_CHANNEL", ""),
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
        heartbeat=heartbeat,
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

    # anthropic
    from langchain_anthropic import ChatAnthropic  # noqa: PLC0415

    return ChatAnthropic(
        model = cfg.resolved_model,
        temperature = cfg.temperature,
    )
