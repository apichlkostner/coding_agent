"""Async client for a Language Server subprocess.

The :class:`LanguageServerClient` spawns a language server process,
speaks the Language Server Protocol over its stdio, and exposes the
features most useful to a coding agent: completion, definition,
references, document/workspace symbols, rename, type hierarchy and
call hierarchy.

Design notes
------------

- **Minimal stdlib only.** No third-party LSP libraries — the protocol
  is small enough that a few hundred lines of ``asyncio`` cover what we
  need.
- **One subprocess per process.** A module-level singleton for the
    default C/C++ backend (:func:`get_default_client`) is started lazily
    on first tool call and stopped on agent shutdown.
- **Sequential requests.** clangd (and the LSP spec in general) assumes
  the client issues one request at a time. We serialise requests
  through an ``asyncio.Lock`` — concurrent tool calls queue up rather
  than racing.
- **Disk-backed sync.** Documents are synced from disk on demand.  We
    track the last text we sent so ``sync_document`` can avoid redundant
    ``didChange`` notifications.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.lsp.framing import LSPProtocolError, read_message, write_message
from agent.lsp.types import (
    CallHierarchyIncomingCall,
    CallHierarchyItem,
    CallHierarchyOutgoingCall,
    CompletionList,
    Diagnostic,
    DocumentSymbol,
    InitializeResult,
    Location,
    Position,
    TextDocumentItem,
    TypeHierarchyItem,
    WorkspaceEdit,
    WorkspaceSymbol,
)

logger = logging.getLogger("agent.lsp.client")

# Cap on how many ``window/logMessage`` entries we keep in memory. Bounds
# the memory footprint if clangd is chatty and lets us return a useful
# last-N to interested callers.
_LOG_RING_SIZE = 100

# Default args to clangd. ``--background-index`` enables indexing across
# the workspace (for ``workspace/symbol``); ``--clang-tidy=0`` disables
# clang-tidy passes that clangd would otherwise try to run with no
# configuration; ``--header-insertion=never`` stops clangd from
# auto-injecting ``#include`` lines into completion items.
_DEFAULT_CLANGD_ARGS: tuple[str, ...] = (
    "--background-index",
    "--clang-tidy=0",
    "--header-insertion=never",
)
_DEFAULT_SERVER_COMMAND: tuple[str, ...] = ("clangd", *_DEFAULT_CLANGD_ARGS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def path_to_uri(path: str | Path) -> str:
    """Return the ``file://`` URI for *path* (absolute, resolved)."""
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> str:
    """Return the filesystem path encoded in a ``file://`` URI."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"unsupported URI scheme: {parsed.scheme!r}")
    return urllib.request.url2pathname(parsed.path)


# ---------------------------------------------------------------------------
# Per-document state
# ---------------------------------------------------------------------------


class _DocumentState:
    """Track the version, language, and synced content of an open document."""

    __slots__ = ("uri", "language_id", "version", "text")

    def __init__(
        self,
        uri: str,
        language_id: str,
        version: int,
        text: str,
    ) -> None:
        self.uri = uri
        self.language_id = language_id
        self.version = version
        self.text = text


# ---------------------------------------------------------------------------
# LanguageServerClient
# ---------------------------------------------------------------------------


class LanguageServerClient:
    """Asynchronous client for a language-server subprocess.

    Parameters
    ----------
    command:
        Command used to start the language server. The first element is
        resolved with :func:`shutil.which` at start-up when it is not an
        absolute path. Defaults to the built-in clangd command.
    workspace_root:
        Directory the server should treat as the project root. Defaults
        to the current working directory at construction time.  The
        directory must exist.
    startup_timeout:
        Seconds to wait for clangd to respond to the ``initialize``
        request. Default: 30 s.
    request_timeout:
        Seconds to wait for any individual request to receive a reply.
        Default: 30 s.
    server_name:
        Human-readable server label used in logs and error messages.
    initialize_params:
        Extra fields merged into the standard ``initialize`` params.
    language_ids:
        Optional suffix-to-language-id overrides for ``didOpen``.
    """

    def __init__(
        self,
        command: Sequence[str] | None = None,
        workspace_root: str | Path | None = None,
        *,
        startup_timeout: float = 30.0,
        request_timeout: float = 30.0,
        server_name: str = "language server",
        initialize_params: Mapping[str, Any] | None = None,
        language_ids: Mapping[str, str] | None = None,
    ) -> None:
        resolved_command = tuple(command or _DEFAULT_SERVER_COMMAND)
        if not resolved_command:
            raise ValueError("command must not be empty")
        self._command = resolved_command
        self._workspace_root = (
            Path(workspace_root).resolve() if workspace_root is not None else Path.cwd()
        )
        self._startup_timeout = startup_timeout
        self._request_timeout = request_timeout
        self._server_name = server_name
        self._initialize_params = dict(initialize_params or {})
        self._language_ids = {
            ".c": "c",
            ".h": "c",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".hpp": "cpp",
            ".hxx": "cpp",
            ".inc": "cpp",
            **dict(language_ids or {}),
        }

        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._request_lock = asyncio.Lock()
        self._server_caps: InitializeResult | None = None

        self._documents: dict[str, _DocumentState] = {}
        # URI -> list of diagnostics from the most recent
        # ``publishDiagnostics`` notification.  Cleared on every new
        # publication for the same URI.
        self._diagnostics: dict[str, list[Diagnostic]] = {}
        self._diagnostic_generations: dict[str, int] = {}
        self._diagnostic_events: dict[str, asyncio.Event] = {}
        self._log_ring: deque[dict[str, Any]] = deque(maxlen=_LOG_RING_SIZE)
        self._stopped = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _attach_pipes(
        self,
        stdin: asyncio.StreamWriter | None,
        stdout: asyncio.StreamReader | None,
        stderr: asyncio.StreamReader | None,
    ) -> None:
        """Wire *stdin* / *stdout* / *stderr* streams and start reader tasks.

        Used by tests to inject in-memory pipe pairs backed by a mock LSP
        server.  Production callers use :meth:`start` which spawns clangd
        and then calls this method with the subprocess's pipes.
        """

        class _FakeProcess:
            def __init__(self) -> None:
                self.stdin = stdin
                self.stdout = stdout
                self.stderr = stderr
                self.returncode: int | None = None

            async def wait(self) -> int:
                return 0

            def terminate(self) -> None:
                if self.returncode is None:
                    self.returncode = -15

            def kill(self) -> None:
                if self.returncode is None:
                    self.returncode = -9

        self._process = _FakeProcess()  # type: ignore[assignment]
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"{self._server_name}-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"{self._server_name}-stderr"
        )

    async def _do_handshake(self) -> InitializeResult:
        """Send ``initialize`` + ``initialized`` and return the result."""
        root_uri = self._workspace_root.as_uri()
        params = {
            "processId": os.getpid(),
            "rootPath": str(self._workspace_root),
            "rootUri": root_uri,
            "workspaceFolders": [{"uri": root_uri, "name": self._workspace_root.name}],
            "capabilities": {},
            "initializationOptions": {},
        }
        params.update(self._initialize_params)
        result = await self._request(
            "initialize",
            params,
            timeout=self._startup_timeout,
        )
        self._server_caps = result
        await self._notify("initialized", {})
        return result

    async def start(self) -> None:
        """Spawn the configured server and perform the ``initialize`` handshake."""
        if self._process is not None:
            raise RuntimeError("LanguageServerClient already started")

        if not self._workspace_root.is_dir():
            raise FileNotFoundError(
                f"workspace root does not exist: {self._workspace_root}"
            )

        executable, *args = self._command
        resolved = shutil.which(executable)
        if resolved is None:
            raise FileNotFoundError(
                f"{self._server_name} not found on PATH (looked for {executable!r})"
            )

        argv = [resolved, *args]
        logger.info("starting %s: %s", self._server_name, " ".join(argv))

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process  # type: ignore[assignment]
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"{self._server_name}-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"{self._server_name}-stderr"
        )

        try:
            await self._do_handshake()
        except Exception:
            await self._kill()
            raise

    async def stop(self) -> None:
        """Send ``shutdown`` + ``exit`` and terminate the subprocess.

        Idempotent: a second call is a no-op.
        """
        if self._stopped:
            return
        self._stopped = True

        if self._process is not None and self._process.returncode is None:
            try:
                await asyncio.wait_for(self._request("shutdown", None), timeout=5.0)
            except (TimeoutError, Exception):  # noqa: BLE001
                # Server may have died or be wedged — fall through to kill.
                logger.warning(
                    "%s shutdown request failed; terminating",
                    self._server_name,
                )
            try:
                await self._notify("exit", None)
            except (ConnectionError, OSError):
                pass

        await self._kill()

    async def _kill(self) -> None:
        """Terminate the subprocess and cancel reader tasks. Safe to call twice."""
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._reader_task = None
        self._stderr_task = None

        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                    await self._process.wait()
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
        self._process = None

        # Reject any pending requests so callers don't hang forever.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    ConnectionError(f"{self._server_name} client stopped")
                )
        self._pending.clear()

    async def __aenter__(self) -> LanguageServerClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Read / write loop
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Read framed LSP messages from the server's stdout forever."""
        assert self._process is not None
        assert self._process.stdout is not None
        try:
            while True:
                msg = await read_message(self._process.stdout)
                if msg is None:
                    logger.info(
                        "%s closed stdout; exiting read loop", self._server_name
                    )
                    return
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except LSPProtocolError as exc:
            logger.error("LSP protocol error: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("error in %s read loop: %s", self._server_name, exc)

    async def _drain_stderr(self) -> None:
        """Log the server's stderr output line by line."""
        assert self._process is not None
        assert self._process.stderr is not None
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("%s stderr: %s", self._server_name, text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("stderr drain ended: %s", exc)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route an incoming message to a pending future, a handler, or drop it."""
        if "id" in msg and "method" in msg:
            # Server-initiated request.
            asyncio.create_task(self._handle_server_request(msg))
            return

        if "id" in msg:
            # Response to one of our requests.
            req_id = msg["id"]
            fut = self._pending.pop(req_id, None)
            if fut is None or fut.done():
                return
            if "error" in msg:
                err = msg["error"]
                fut.set_exception(LSPError(err.get("code", -1), err.get("message", "")))
            else:
                fut.set_result(msg.get("result"))
            return

        if "method" in msg:
            self._handle_notification(msg)

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})
        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            diagnostics: list[Diagnostic] = list(params.get("diagnostics", []))
            self._diagnostics[uri] = diagnostics
            self._diagnostic_generations[uri] = (
                self._diagnostic_generations.get(uri, 0) + 1
            )
            event = self._diagnostic_events.get(uri)
            if event is not None:
                event.set()
            self._diagnostic_events[uri] = asyncio.Event()
            return
        if method in ("window/logMessage", "window/showMessage"):
            self._log_ring.append(params)
            return
        if method == "$/progress":
            # We don't expose progress to callers, but keep the log ring tidy.
            return
        # Unknown notifications are logged once at debug level.
        logger.debug("unhandled notification: %s", method)

    async def _handle_server_request(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params", {})

        if method == "workspace/configuration":
            items = params.get("items", [])
            await self._reply(req_id, [None] * len(items))
            return
        if method in ("client/registerCapability", "client/unregisterCapability"):
            await self._reply(req_id, None)
            return
        if method == "window/workDoneProgress/create":
            await self._reply(req_id, None)
            return
        if method == "workspace/semanticTokens/refresh":
            await self._reply(req_id, None)
            return

        logger.warning("unsupported server request: %s", method)
        await self._reply(
            req_id, None, error={"code": -32601, "message": "Method not found"}
        )

    async def _reply(
        self,
        req_id: Any,
        result: Any,
        *,
        error: dict[str, Any] | None = None,
    ) -> None:
        if self._process is None or self._process.stdin is None:
            return
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            await write_message(self._process.stdin, msg)
        except (ConnectionError, OSError) as exc:
            logger.debug("failed to reply to %s: %s", req_id, exc)

    # ------------------------------------------------------------------
    # Request / notify primitives
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        params: Any,
        *,
        timeout: float | None = None,
    ) -> Any:
        if self._process is None or self._process.stdin is None:
            raise ConnectionError("LanguageServerClient not started")
        if self._stopped:
            raise ConnectionError("LanguageServerClient stopped")

        req_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params

        async with self._request_lock:
            try:
                await write_message(self._process.stdin, payload)
            except Exception:
                self._pending.pop(req_id, None)
                raise
            return await asyncio.wait_for(fut, timeout=timeout or self._request_timeout)

    async def _notify(self, method: str, params: Any) -> None:
        if self._process is None or self._process.stdin is None:
            raise ConnectionError("LanguageServerClient not started")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await write_message(self._process.stdin, payload)

    async def did_change_configuration(self, settings: dict[str, Any]) -> None:
        """Notify the server that workspace configuration changed."""
        await self._notify("workspace/didChangeConfiguration", {"settings": settings})

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    def _language_id_for(self, path: str) -> str:
        ext = Path(path).suffix.lower()
        return self._language_ids.get(ext, "plaintext")

    async def did_open(self, path: str, text: str) -> None:
        """Send ``textDocument/didOpen`` for *path* with *text* as content."""
        uri = path_to_uri(path)
        if uri in self._documents:
            # Re-opening is a protocol error; update via did_change instead.
            await self.did_change(path, self._documents[uri].version + 1, text)
            return
        item = TextDocumentItem(
            uri=uri, languageId=self._language_id_for(path), version=1, text=text
        )
        self._documents[uri] = _DocumentState(
            uri=uri,
            language_id=item["languageId"],
            version=1,
            text=text,
        )
        await self._notify("textDocument/didOpen", {"textDocument": item})

    async def did_change(self, path: str, version: int, new_text: str) -> None:
        """Send ``textDocument/didChange`` for *path* with *new_text*.

        The whole document is replaced; we don't track per-line edits.
        """
        uri = path_to_uri(path)
        state = self._documents.get(uri)
        if state is None:
            # No didOpen yet — synthesise one.
            await self.did_open(path, new_text)
            return
        state.version = version
        state.text = new_text
        await self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": new_text}],
            },
        )

    async def did_close(self, path: str) -> None:
        """Send ``textDocument/didClose`` for *path*."""
        uri = path_to_uri(path)
        if uri not in self._documents:
            return
        del self._documents[uri]
        await self._notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    async def ensure_open(self, path: str) -> None:
        """Open *path* in the server if not already open; reads text from disk."""
        uri = path_to_uri(path)
        if uri in self._documents:
            return
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        await self.did_open(path, text)

    async def sync_document(self, path: str) -> bool:
        """Sync *path* from disk into the server state.

        Returns ``True`` when a ``didOpen`` or ``didChange`` notification was
        sent and ``False`` when the server state was already current.
        """
        uri = path_to_uri(path)
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        state = self._documents.get(uri)
        if state is None:
            await self.did_open(path, text)
            return True
        if state.text == text:
            return False
        await self.did_change(path, state.version + 1, text)
        return True

    # ------------------------------------------------------------------
    # LSP features
    # ------------------------------------------------------------------

    @staticmethod
    def _pos(line: int, character: int) -> Position:
        return {"line": int(line), "character": int(character)}

    async def completion(self, path: str, line: int, character: int) -> CompletionList:
        await self.sync_document(path)
        result = await self._request(
            "textDocument/completion",
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": self._pos(line, character),
            },
        )
        if isinstance(result, list):
            return {"isIncomplete": False, "items": result}
        if isinstance(result, dict):
            return {
                "isIncomplete": bool(result.get("isIncomplete", False)),
                "items": list(result.get("items", [])),
            }
        return {"isIncomplete": False, "items": []}

    async def definition(self, path: str, line: int, character: int) -> list[Location]:
        await self.sync_document(path)
        result = await self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": self._pos(line, character),
            },
        )
        if result is None:
            return []
        if isinstance(result, dict):
            return [result]  # type: ignore[list-item]
        return list(result)

    async def references(
        self,
        path: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[Location]:
        await self.sync_document(path)
        result = await self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": self._pos(line, character),
                "context": {"includeDeclaration": include_declaration},
            },
        )
        return list(result) if result else []

    async def document_symbol(self, path: str) -> list[DocumentSymbol]:
        await self.sync_document(path)
        result = await self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": path_to_uri(path)}},
        )
        return list(result) if result else []

    async def workspace_symbol(self, query: str) -> list[WorkspaceSymbol]:
        result = await self._request("workspace/symbol", {"query": query})
        return list(result) if result else []

    async def rename(
        self, path: str, line: int, character: int, new_name: str
    ) -> WorkspaceEdit | None:
        await self.sync_document(path)
        result = await self._request(
            "textDocument/rename",
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": self._pos(line, character),
                "newName": new_name,
            },
        )
        if result is None or not isinstance(result, dict):
            return None
        return result  # type: ignore[return-value]

    async def prepare_type_hierarchy(
        self, path: str, line: int, character: int
    ) -> list[TypeHierarchyItem]:
        await self.sync_document(path)
        result = await self._request(
            "textDocument/prepareTypeHierarchy",
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": self._pos(line, character),
            },
        )
        return list(result) if result else []

    async def type_hierarchy_supertypes(
        self, item: TypeHierarchyItem
    ) -> list[TypeHierarchyItem]:
        result = await self._request("typeHierarchy/supertypes", {"item": item})
        return list(result) if result else []

    async def type_hierarchy_subtypes(
        self, item: TypeHierarchyItem
    ) -> list[TypeHierarchyItem]:
        result = await self._request("typeHierarchy/subtypes", {"item": item})
        return list(result) if result else []

    async def prepare_call_hierarchy(
        self, path: str, line: int, character: int
    ) -> list[CallHierarchyItem]:
        await self.sync_document(path)
        result = await self._request(
            "textDocument/prepareCallHierarchy",
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": self._pos(line, character),
            },
        )
        return list(result) if result else []

    async def call_hierarchy_incoming(
        self, item: CallHierarchyItem
    ) -> list[CallHierarchyIncomingCall]:
        result = await self._request("callHierarchy/incomingCalls", {"item": item})
        return list(result) if result else []

    async def call_hierarchy_outgoing(
        self, item: CallHierarchyItem
    ) -> list[CallHierarchyOutgoingCall]:
        result = await self._request("callHierarchy/outgoingCalls", {"item": item})
        return list(result) if result else []

    # ------------------------------------------------------------------
    # Diagnostics & logging
    # ------------------------------------------------------------------

    def get_diagnostics(self, path: str) -> list[Diagnostic]:
        """Return the latest diagnostics for *path*, or an empty list."""
        return list(self._diagnostics.get(path_to_uri(path), []))

    def server_capabilities(self) -> dict[str, Any]:
        """Return the initialize capabilities payload from the current server."""
        if not isinstance(self._server_caps, dict):
            return {}
        capabilities = self._server_caps.get("capabilities")
        if isinstance(capabilities, dict):
            return capabilities
        return {}

    def diagnostics_generation(self, path: str) -> int:
        """Return the publication generation for *path* diagnostics."""
        return self._diagnostic_generations.get(path_to_uri(path), 0)

    async def await_diagnostics(
        self,
        path: str,
        timeout: float | None = None,
        *,
        after_generation: int | None = None,
    ) -> list[Diagnostic]:
        """Wait until diagnostics newer than *after_generation* are published."""
        uri = path_to_uri(path)
        target_generation = (
            self._diagnostic_generations.get(uri, 0) + 1
            if after_generation is None
            else int(after_generation) + 1
        )
        while self._diagnostic_generations.get(uri, 0) < target_generation:
            event = self._diagnostic_events.setdefault(uri, asyncio.Event())
            await asyncio.wait_for(event.wait(), timeout=timeout)
        return self.get_diagnostics(path)

    def recent_log(self) -> list[dict[str, Any]]:
        """Return a copy of the most recent log/show message payloads."""
        return list(self._log_ring)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LSPError(Exception):
    """An error returned by the language server."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"LSP error {code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


ClangdClient = LanguageServerClient


_default_client: LanguageServerClient | None = None
_default_lock = asyncio.Lock()


async def get_default_client() -> LanguageServerClient:
    """Return the default C/C++ client, starting it if needed."""
    global _default_client
    async with _default_lock:
        if _default_client is None:
            clangd_path = os.environ.get("CLANGD_PATH", "clangd")
            _default_client = LanguageServerClient(
                command=(clangd_path, *_DEFAULT_CLANGD_ARGS),
                server_name="clangd",
            )
            await _default_client.start()
        return _default_client


async def reset_default_client() -> None:
    """Stop and clear the singleton. Safe to call when not started."""
    global _default_client
    async with _default_lock:
        if _default_client is not None:
            await _default_client.stop()
        _default_client = None
