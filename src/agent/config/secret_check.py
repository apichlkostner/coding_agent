import re

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class SecretError(ValueError):
    """Raised when the data for a key with a secret is not an env variable."""


def _is_env_placeholder(value: object) -> bool:
    """Return ``True`` if *value* is exactly an env-expansion placeholder."""
    if not isinstance(value, str):
        return False
    match = _ENV_PATTERN.fullmatch(value)
    return match is not None and match.group(2) in (None, "")


_SECRET_KEYS: tuple[tuple[str, str], ...] = (
    ("model_provider", "api_key"),
    ("matrix_adapter", "access_token"),
    ("discord_adapter", "bot_token"),
)


def secret_check(config: dict) -> None:
    """Verify that every secret-bearing key is an env-expansion placeholder.

    Args:
        config: Raw config mapping, before env expansion.

    Raises:
        SecretError: If a secret key holds a literal value.  The message
            names the offending key as ``section.key``.
    """
    for section, key in _SECRET_KEYS:
        section_data = config.get(section)
        if not isinstance(section_data, dict) or key not in section_data:
            continue
        if not _is_env_placeholder(section_data[key]):
            raise SecretError(
                f"Secret in config: '{section}.{key}' must be an env "
                f"placeholder such as '${{{key.upper()}}}'"
            )
