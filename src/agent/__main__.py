"""CLI entry point — ``uv run agent`` or ``python -m agent``.

Public API
----------
``main()``
    Synchronous entry point registered in ``pyproject.toml`` as the
    ``agent`` console-script.  Calls :func:`asyncio.run` on :func:`_run`.

``build_router(settings, graph)``
    Pure factory used by :func:`_run` and directly by tests.  Builds a
    :class:`~agent.router.router.MessageRouter` and registers the adapters
    whose IDs appear in ``settings.enabled_adapters``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.adapters.batch_adapter import BatchAdapter
from agent.adapters.discord_adapter import DiscordAdapter
from agent.adapters.heartbeat_adapter import HeartbeatAdapter
from agent.adapters.matrix_adapter import MatrixAdapter
from agent.adapters.prompt_adapter import PromptAdapter
from agent.adapters.terminal_adapter import TerminalAdapter
from agent.config import Settings, get_settings
from agent.router import AgentService, MessageRouter

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

GraphType = Any

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """Configure root logger: INFO to ``agent.log`` file + WARNING to stderr."""
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g. during tests) — don't add duplicate handlers.
        return

    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler("agent.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)

    root.addHandler(fh)
    root.addHandler(sh)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for normal and one-shot execution modes."""
    parser = argparse.ArgumentParser(description="Run the coding agent")
    parser.add_argument(
        "working_dir",
        nargs="?",
        help="Working directory to switch into before starting the agent.",
    )
    parser.add_argument(
        "--workdir",
        dest="workdir",
        help="Working directory to switch into before starting the agent.",
    )
    parser.add_argument(
        "--prompt",
        help="Run the agent once on a direct prompt and exit.",
    )
    parser.add_argument(
        "--batch-input",
        help="Input file with one prompt per non-empty line.",
    )
    parser.add_argument(
        "--batch-output",
        help="Write structured batch results to this JSONL file.",
    )

    args = parser.parse_args(argv)

    if args.working_dir and args.workdir:
        parser.error("working directory may be provided only once.")

    args.working_dir = args.working_dir or args.workdir

    if args.prompt and (args.batch_input or args.batch_output):
        parser.error("--prompt cannot be combined with batch mode flags.")

    if bool(args.batch_input) != bool(args.batch_output):
        parser.error("--batch-input and --batch-output must be provided together.")

    return args


def build_router(
    settings: Settings,
    graph: GraphType | None = None,
) -> MessageRouter:
    """Build and return a configured :class:`~agent.router.router.MessageRouter`.

    Adapters are registered according to ``settings.enabled_adapters``.
    The Discord adapter is silently skipped when its token is absent even
    if ``"discord"`` is in the enabled set.

    Parameters
    ----------
    settings:
        Runtime configuration (from :func:`~agent.config.get_settings` or
        a hand-crafted instance in tests).
    graph:
        Optional compiled LangGraph.  When ``None`` the module-level
        singleton from :mod:`agent.graph` is used.  Pass an explicit graph
        in tests to avoid touching the real LLM.

    Returns
    -------
    MessageRouter
        Ready to be started with :meth:`~agent.router.router.MessageRouter.run`.
    """
    if graph is None:
        from agent.graph import graph as _default  # lazy import

        graph = _default

    service = AgentService(graph)
    router = MessageRouter(service)
    enabled = settings.enabled_adapters

    if "terminal" in enabled:
        router.register(TerminalAdapter())
        logger.info("Registered TerminalAdapter.")

    if "discord" in enabled:
        if settings.discord_token:
            router.register(DiscordAdapter(token=settings.discord_token))
            logger.info("Registered DiscordAdapter.")
        else:
            logger.warning(
                "Discord adapter is enabled but DISCORD_BOT_TOKEN is not set — skipping."
            )

    if "heartbeat" in enabled:
        router.register(HeartbeatAdapter(settings.heartbeat))
        logger.info("Registered HeartbeatAdapter.")

    ms = settings.matrix
    if "matrix" in enabled:
        if ms.homeserver_url and ms.access_token and ms.user_id:
            router.register(MatrixAdapter(ms))
            logger.info("Registered MatrixAdapter.")
        else:
            logger.warning(
                "Matrix adapter is enabled but credentials are incomplete "
                "(MATRIX_HOMESERVER_URL / MATRIX_ACCESS_TOKEN / MATRIX_USER_ID)"
                " — skipping."
            )
    elif ms.homeserver_url or ms.access_token or ms.user_id:
        logger.warning(
            "Matrix credentials are configured but the adapter is not enabled. "
            "Add 'matrix' to ENABLED_ADAPTERS to start the Matrix adapter."
        )

    return router


def build_one_shot_router(
    settings: Settings,
    graph: GraphType | None = None,
    *,
    prompt: str | None = None,
    batch_input: str | None = None,
    batch_output: str | None = None,
) -> MessageRouter:
    """Build a router for one-shot execution only."""
    if graph is None:
        from agent.graph import graph as _default  # lazy import

        graph = _default

    service = AgentService(graph)
    router = MessageRouter(service)

    if prompt is not None:
        router.register(PromptAdapter(prompt))
    elif batch_input is not None and batch_output is not None:
        router.register(BatchAdapter(batch_input, batch_output))
    else:
        raise ValueError("One-shot router requires either --prompt or batch args.")

    return router


async def _run(argv: list[str] | None = None) -> None:
    """Async body of the application."""
    args = parse_args(argv)
    try:
        if args.working_dir:
            os.chdir(Path(args.working_dir).expanduser().resolve())

        settings = get_settings()

        if args.prompt:
            router = build_one_shot_router(settings, prompt=args.prompt)
        elif args.batch_input and args.batch_output:
            router = build_one_shot_router(
                settings,
                batch_input=args.batch_input,
                batch_output=args.batch_output,
            )
        else:
            router = build_router(settings)

        await router.run()
    finally:
        try:
            from agent.lsp import reset_client_manager, reset_default_client

            await reset_client_manager()
            await reset_default_client()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            logger.debug("LSP client cleanup failed", exc_info=True)


def main() -> None:
    """Synchronous entry point for ``uv run agent``.

    This is the function registered as a console-script in ``pyproject.toml``.
    It *must* be synchronous so the script runner can call it directly.
    """
    _setup_logging()
    asyncio.run(_run(sys.argv[1:]))


if __name__ == "__main__":
    main()
