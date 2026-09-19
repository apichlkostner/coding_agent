"""Pydantic models describing the structure of the YAML config file.

These models are the single source of truth for what a valid config looks
like.  :func:`agent.config.config.load_config` feeds the parsed YAML into
:class:`Config`, so any change here immediately changes what the loader
accepts.
"""

from pydantic import BaseModel, ConfigDict, field_validator


class ModelProvider(BaseModel):
    """Connection settings for the LLM provider.

    Attributes:
        name: Provider label, e.g. ``litellm``.
        api: Wire protocol; one of ``openai``, ``openai_compatible`` or
            ``anthropic``.
        api_key: Credential passed to the provider.
        endpoint: Base URL of the provider API.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    api: str
    api_key: str
    endpoint: str

    @field_validator("api")
    @classmethod
    def validate_api(cls, api: str) -> str:
        """Reject any ``api`` value outside the supported allow-list."""
        if api not in {"openai", "openai_compatible", "anthropic"}:
            raise ValueError("api must be openai, openai_compatible or anthropic")
        return api


class Model(BaseModel):
    """Model selection and reasoning effort.

    Attributes:
        name: Model identifier sent to the provider.
        effort: Reasoning effort hint, e.g. ``low`` or ``high``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    effort: str


class DiscordAdapter(BaseModel):
    """Credentials for the Discord adapter.

    Attributes:
        bot_token: Discord bot token. Empty disables the adapter.
    """

    model_config = ConfigDict(extra="forbid")

    bot_token: str = ""


class Heartbeat(BaseModel):
    """Heartbeat scheduling and output routing.

    Attributes:
        interval: Interval of the heartbeat in seconds.
        prompt_file: Prompt file executed on each heartbeat.
        output_adapter: Adapter used for the output.
        output_channel: Channel of the adapter to be used.
    """

    model_config = ConfigDict(extra="forbid")

    interval: int = 600
    prompt_file: str = "HEARTBEAT.md"
    output_adapter: str = ""
    output_channel: str = ""


class MatrixAdapter(BaseModel):
    """Credentials and session settings for the Matrix adapter.

    Attributes:
        homeserver_url: Base URL of the Matrix homeserver.
        access_token: Bot access token obtained from the homeserver.
        user_id: Fully-qualified Matrix user ID of the bot.
        device_id: Optional Matrix device ID used for encrypted room sessions.
        store_path: Optional matrix-nio crypto store path for persisted E2EE state.
        ignore_unverified_devices: Whether outbound sends should ignore
            unverified devices in encrypted rooms.

    Empty ``homeserver_url``/``access_token``/``user_id`` disable the adapter.
    """

    model_config = ConfigDict(extra="forbid")

    homeserver_url: str = ""
    access_token: str = ""
    user_id: str = ""
    device_id: str = ""
    store_path: str = ""
    ignore_unverified_devices: bool = True


class Config(BaseModel):
    """Root configuration object.

    Attributes:
        model_provider: Provider connection settings.
        model: Model selection.
        discord_adapter: Discord adapter credentials.
        heartbeat: Heartbeat scheduling and output routing.
        matrix_adapter: Matrix adapter credentials and session settings.

    Adapter sections are optional; empty credentials disable the adapter.
    """

    model_config = ConfigDict(extra="forbid")

    model_provider: ModelProvider
    model: Model
    discord_adapter: DiscordAdapter = DiscordAdapter()
    heartbeat: Heartbeat = Heartbeat()
    matrix_adapter: MatrixAdapter = MatrixAdapter()
