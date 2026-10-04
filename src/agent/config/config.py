"""Loading of the YAML configuration file into a validated :class:`Config`.

The loader is deliberately strict: every failure mode (missing file,
unreadable path, malformed YAML, empty document, non-mapping root, schema
violation) is normalised into a single :class:`ConfigError` so callers only
need one ``except`` clause.  The original exception is preserved as
``__cause__`` for diagnostics.
"""

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent.config.env_expand import EnvVarError, expand_env
from agent.config.schema import Config
from agent.config.secret_check import SecretError, secret_check


class ConfigError(Exception):
    """Raised when a config file cannot be read or fails validation."""


#: Default config location relative to the agent repository root.
DEFAULT_CONFIG_RELATIVE = Path("config/config.yaml")


def _agent_repo_root() -> Path:
    """Return the agent repository root (parent of the ``src`` directory)."""
    # ``config.py`` lives at ``<root>/src/agent/config/config.py``.
    return Path(__file__).resolve().parents[3]


def resolve_config_path(path: str | None = None) -> Path:
    """Resolve the configuration file path.

    Resolution order:

    1. An explicit *path* argument, if given.
    2. The ``AGENT_CONFIG`` environment variable, if set.
    3. ``config/config.yaml`` relative to the current working directory.
    4. ``config/config.yaml`` relative to the agent repository root.

    This lets the agent run against an arbitrary project directory (its CWD)
    while still finding its own configuration.
    """
    if path:
        return Path(path)

    env_path = os.environ.get("AGENT_CONFIG")
    if env_path:
        return Path(env_path)

    cwd_candidate = Path.cwd() / DEFAULT_CONFIG_RELATIVE
    if cwd_candidate.exists():
        return cwd_candidate

    return _agent_repo_root() / DEFAULT_CONFIG_RELATIVE


def load_config(path: str | None = None) -> Config:
    """Read *path* and return the validated configuration.

    Args:
        path: Filesystem path to a YAML config file.  When ``None`` the path
            is resolved with :func:`resolve_config_path`.

    Returns:
        The parsed and validated :class:`Config`.

    Raises:
        ConfigError: If the file is missing, unreadable, not valid YAML,
            does not contain a mapping at the root, or fails schema
            validation.
    """
    p = resolve_config_path(path)
    if not p.exists():
        raise ConfigError(f"Config not found: {p}")

    try:
        with open(p) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Malformed YAML in {p}") from e
    except OSError as e:
        raise ConfigError(f"Cannot read config {p}: {e}") from e

    if raw is None:
        raise ConfigError(f"Empty config in {p}")

    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping in {p}")

    try:
        secret_check(raw)
        expanded_conf = expand_env(raw)
        return Config(**expanded_conf)
    except SecretError as e:
        raise ConfigError(f"Secret in config {p}: {e}") from e
    except EnvVarError as e:
        raise ConfigError(f"Missing environment variable in {p}: {e}") from e
    except ValidationError as e:
        raise ConfigError(f"Invalid config in {p}") from e
