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
    "openai": "gpt-gpt-5.4-nano",
    "anthropic": "claude-haiku-4-5-20251001",
}


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    llm_provider: Provider = field(default="openai")
    model_name: str = field(default="")
    temperature: float = field(default=0.0)

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
    return Settings(
        llm_provider=raw_provider,  # type: ignore[arg-type]
        model_name=os.getenv("MODEL_NAME", ""),
        temperature=float(os.getenv("TEMPERATURE", "0")),
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
        model=cfg.resolved_model,
        temperature=cfg.temperature,
    )
