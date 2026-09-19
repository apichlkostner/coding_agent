import os
import re
from typing import Any

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class EnvVarError(ValueError):
    """Raised when a referenced environment variable is unset or empty."""


def _substitute(value: str) -> str:
    def repl(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        env_value = os.environ.get(name)
        if env_value:
            return env_value
        if default is not None:
            return default
        raise EnvVarError(f"Environment variable '{name}' is not set or is empty")

    return _ENV_PATTERN.sub(repl, value)


def expand_env(obj: Any) -> Any:
    if isinstance(obj, str):
        return _substitute(obj)
    if isinstance(obj, dict):
        return {k: expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env(v) for v in obj]
    return obj
