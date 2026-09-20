"""Loading of the YAML configuration file into a validated :class:`Config`.

The loader is deliberately strict: every failure mode (missing file,
unreadable path, malformed YAML, empty document, non-mapping root, schema
violation) is normalised into a single :class:`ConfigError` so callers only
need one ``except`` clause.  The original exception is preserved as
``__cause__`` for diagnostics.
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from agent.config.env_expand import EnvVarError, expand_env
from agent.config.schema import Config
from agent.config.secret_check import SecretError, secret_check


class ConfigError(Exception):
    """Raised when a config file cannot be read or fails validation."""


def load_config(path: str) -> Config:
    """Read *path* and return the validated configuration.

    Args:
        path: Filesystem path to a YAML config file.

    Returns:
        The parsed and validated :class:`Config`.

    Raises:
        ConfigError: If the file is missing, unreadable, not valid YAML,
            does not contain a mapping at the root, or fails schema
            validation.
    """
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config not found: {path}")

    try:
        with open(p) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Malformed YAML in {path}") from e
    except OSError as e:
        raise ConfigError(f"Cannot read config {path}: {e}") from e

    if raw is None:
        raise ConfigError(f"Empty config in {path}")

    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping in {path}")

    try:
        secret_check(raw)
        expanded_conf = expand_env(raw)
        return Config(**expanded_conf)
    except SecretError as e:
        raise ConfigError(f"Secret in config {path}: {e}") from e
    except EnvVarError as e:
        raise ConfigError(f"Missing environment variable in {path}: {e}") from e
    except ValidationError as e:
        raise ConfigError(f"Invalid config in {path}") from e
