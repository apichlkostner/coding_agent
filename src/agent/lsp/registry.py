"""Backend registry and pooled client lifecycle for LSP integrations."""

from __future__ import annotations

import asyncio
import os
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.lsp.client import LanguageServerClient

_CLANGD_ARGS: tuple[str, ...] = (
    "--background-index",
    "--clang-tidy=0",
    "--header-insertion=never",
)


@dataclass(frozen=True)
class WorkspaceContext:
    """Derived workspace state for a file-backed LSP request."""

    path: Path
    language: str
    workspace_root: Path
    python_executable: Path | None = None
    python_venv_path: Path | None = None
    has_pyright_config: bool = False


@dataclass(frozen=True)
class ServerSpec:
    """Declarative description of an LSP backend."""

    server_id: str
    language_id: str
    file_extensions: frozenset[str]
    root_markers: tuple[str, ...]
    configuration_builder: (
        Callable[[WorkspaceContext], dict[str, Any] | None] | None
    ) = None

    def command(self, context: WorkspaceContext) -> tuple[str, ...]:
        if self.server_id == "clangd":
            return (os.environ.get("CLANGD_PATH", "clangd"), *_CLANGD_ARGS)
        if self.server_id == "pyright":
            return (
                os.environ.get("PYRIGHT_LANGSERVER_PATH", "pyright-langserver"),
                "--stdio",
            )
        raise ValueError(f"unsupported LSP backend: {self.server_id}")

    def build_client_kwargs(self, context: WorkspaceContext) -> dict[str, Any]:
        language_ids = {ext: self.language_id for ext in self.file_extensions}
        return {
            "command": self.command(context),
            "workspace_root": context.workspace_root,
            "server_name": self.server_id,
            "language_ids": language_ids,
        }


_SERVER_SPECS: tuple[ServerSpec, ...] = (
    ServerSpec(
        server_id="pyright",
        language_id="python",
        file_extensions=frozenset({".py", ".pyi"}),
        root_markers=(
            "pyrightconfig.json",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            ".venv",
            ".git",
        ),
        configuration_builder=lambda context: _build_pyright_configuration(context),
    ),
    ServerSpec(
        server_id="clangd",
        language_id="cpp",
        file_extensions=frozenset(
            {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
        ),
        root_markers=(
            "compile_commands.json",
            "compile_flags.txt",
            ".clangd",
            ".git",
        ),
    ),
)

_SERVER_BY_ID = {spec.server_id: spec for spec in _SERVER_SPECS}
_SERVER_ALIAS_TO_ID = {
    "python": "pyright",
    "pyright": "pyright",
    "cpp": "clangd",
    "c++": "clangd",
    "c": "clangd",
    "clangd": "clangd",
}


def _iter_ancestors(path: Path) -> list[Path]:
    current = path if path.is_dir() else path.parent
    return [current, *current.parents]


def _find_nearest_marker(path: Path, markers: tuple[str, ...]) -> Path | None:
    for candidate in _iter_ancestors(path):
        for marker in markers:
            if (candidate / marker).exists():
                return candidate
    return None


def _has_tool_pyright(pyproject_path: Path) -> bool:
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return False
    tool = data.get("tool")
    return isinstance(tool, dict) and "pyright" in tool


def _workspace_has_pyright_config(workspace_root: Path) -> bool:
    return (workspace_root / "pyrightconfig.json").is_file() or _has_tool_pyright(
        workspace_root / "pyproject.toml"
    )


def _detect_python_environment(workspace_root: Path) -> tuple[Path | None, Path | None]:
    for env_dir_name in (".venv", "venv", "env"):
        env_dir = workspace_root / env_dir_name
        interpreter = env_dir / "bin" / "python"
        if interpreter.is_file():
            return interpreter, env_dir
    executable = Path(sys.executable) if sys.executable else None
    return executable, None


def _build_pyright_settings(context: WorkspaceContext) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "python": {},
        "python.analysis": {
            "diagnosticMode": "workspace",
            "autoSearchPaths": True,
            "useLibraryCodeForTypes": True,
            "typeCheckingMode": "standard",
        },
    }
    if context.python_executable is not None:
        settings["python"]["pythonPath"] = str(context.python_executable)
    if context.python_venv_path is not None:
        settings["python"]["venvPath"] = str(context.python_venv_path.parent)
    return settings


def _build_pyright_configuration(
    context: WorkspaceContext,
) -> dict[str, Any] | None:
    if context.has_pyright_config or _workspace_has_pyright_config(
        context.workspace_root
    ):
        return None
    return _build_pyright_settings(context)


async def _configure_client(
    client: LanguageServerClient,
    spec: ServerSpec,
    context: WorkspaceContext,
) -> None:
    if not hasattr(client, "did_change_configuration"):
        return
    if spec.configuration_builder is None:
        return
    settings = spec.configuration_builder(context)
    if settings is None:
        return
    await client.did_change_configuration(settings)


def get_server_spec_for_path(
    path: str | Path,
    *,
    language: str = "",
) -> ServerSpec:
    """Resolve the backend spec for *path* or an explicit language override."""
    if language:
        server_id = _SERVER_ALIAS_TO_ID.get(language.strip().lower())
        if server_id is None:
            raise ValueError(f"unsupported LSP language override: {language!r}")
        return _SERVER_BY_ID[server_id]

    suffix = Path(path).suffix.lower()
    for spec in _SERVER_SPECS:
        if suffix in spec.file_extensions:
            return spec
    raise ValueError(f"unsupported LSP file extension: {suffix or '<none>'}")


def detect_workspace_context(
    path: str | Path,
    *,
    language: str = "",
) -> tuple[ServerSpec, WorkspaceContext]:
    """Resolve backend and workspace context for *path*."""
    resolved_path = Path(path).resolve()
    spec = get_server_spec_for_path(resolved_path, language=language)

    language_markers = tuple(marker for marker in spec.root_markers if marker != ".git")
    workspace_root = _find_nearest_marker(resolved_path, language_markers)
    if workspace_root is None:
        workspace_root = _find_nearest_marker(resolved_path, (".git",))
    if workspace_root is None:
        workspace_root = Path.cwd()

    python_executable: Path | None = None
    python_venv_path: Path | None = None
    has_pyright_config = False
    if spec.server_id == "pyright":
        python_executable, python_venv_path = _detect_python_environment(workspace_root)
        has_pyright_config = _workspace_has_pyright_config(workspace_root)

    context = WorkspaceContext(
        path=resolved_path,
        language=spec.language_id,
        workspace_root=workspace_root,
        python_executable=python_executable,
        python_venv_path=python_venv_path,
        has_pyright_config=has_pyright_config,
    )
    return spec, context


class LanguageServerClientManager:
    """Pool LSP clients by backend and workspace root."""

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], LanguageServerClient] = {}
        self._lock = asyncio.Lock()

    async def get_client_for_path(
        self,
        path: str | Path,
        *,
        language: str = "",
    ) -> tuple[LanguageServerClient, ServerSpec, WorkspaceContext]:
        spec, context = detect_workspace_context(path, language=language)
        key = (spec.server_id, str(context.workspace_root))
        async with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = LanguageServerClient(**spec.build_client_kwargs(context))
                await client.start()
                await _configure_client(client, spec, context)
                self._clients[key] = client
        return client, spec, context

    async def reset(self) -> None:
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            await client.stop()


_client_manager: LanguageServerClientManager | None = None
_client_manager_lock = asyncio.Lock()


async def get_client_manager() -> LanguageServerClientManager:
    """Return the process-wide LSP client manager."""
    global _client_manager
    async with _client_manager_lock:
        if _client_manager is None:
            _client_manager = LanguageServerClientManager()
        return _client_manager


async def reset_client_manager() -> None:
    """Stop and clear all pooled LSP clients."""
    global _client_manager
    async with _client_manager_lock:
        manager = _client_manager
        _client_manager = None
    if manager is not None:
        await manager.reset()
