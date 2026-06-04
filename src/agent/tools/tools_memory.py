"""Persistent key-value memory tools for the agent.

The agent can store and retrieve arbitrary string values across agent runs
using a simple JSON file as backend.  This is *not* a database -- it is
designed for small state variables (e.g. last known weather condition).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ── Persistent key-value memory (file-backed) ──────────────────────────────


def _memory_path() -> Path:
    return Path.cwd() / ".agent_memory.json"


def _load_memory() -> dict[str, str]:
    path = _memory_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load agent memory: %s", exc)
    return {}


def _save_memory(data: dict[str, str]) -> None:
    path = _memory_path()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@tool
def store_memory(key: str, value: str) -> str:
    """Store a value persistently across agent runs.

    Use this to remember facts, state, or context between separate
    invocations.  For example, remember the last weather condition so
    you can detect changes.

    Args:
        key: A unique name for the value (e.g. "last_weather").
        value: The value to remember (use empty string to clear).

    Returns:
        "ok" on success.
    """
    memory = _load_memory()
    memory[key] = value
    _save_memory(memory)
    return "ok"


@tool
def read_memory(key: str) -> str:
    """Read a previously stored value.

    Returns the stored value, or an empty string if the key does not
    exist.

    Args:
        key: The name of the value to retrieve.

    Returns:
        The stored value, or "" if not found.
    """
    return _load_memory().get(key, "")
