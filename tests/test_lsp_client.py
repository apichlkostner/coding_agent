"""Tests for the :class:`agent.lsp.LanguageServerClient` and its framing layer.

The tests run without a real ``clangd`` binary: an in-process mock
server exchanges LSP messages over ``asyncio`` pipes with the client,
so the full protocol path is exercised.  A separate :class:`TestFraming`
class exercises the message framing on its own.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent.lsp import (
    LanguageServerClient,
    LanguageServerClientManager,
    ServerSpec,
    WorkspaceContext,
    detect_workspace_context,
    get_server_spec_for_path,
    path_to_uri,
    uri_to_path,
)
from agent.lsp.framing import (
    LSPProtocolError,
    read_message,
    write_message,
)
from agent.lsp.registry import _configure_client
from agent.lsp.types import WorkspaceSymbol

# ---------------------------------------------------------------------------
# In-memory bidirectional pipe
# ---------------------------------------------------------------------------


class _BidiClientPipe:
    """Bidirectional pipe that provides StreamReader/Writer pairs for
    both the client and the mock server.  Writes on the client_writer
    become reads on the server_reader and vice versa.
    """

    def __init__(self) -> None:
        loop = asyncio.get_event_loop()
        self.client_reader = asyncio.StreamReader(loop=loop)
        self.server_reader = asyncio.StreamReader(loop=loop)
        self.client_writer = self._build_writer(self.server_reader)
        self.server_writer = self._build_writer(self.client_reader)

    def _build_writer(
        self, target_reader: asyncio.StreamReader
    ) -> asyncio.StreamWriter:
        loop = asyncio.get_event_loop()

        class _T(asyncio.WriteTransport):
            def __init__(self) -> None:
                super().__init__()
                self._closing = False

            def write(self, data: bytes) -> None:  # type: ignore[override]
                target_reader.feed_data(data)

            def writelines(self, data: list[bytes]) -> None:  # type: ignore[override]
                for chunk in data:
                    target_reader.feed_data(chunk)

            def close(self) -> None:
                if not self._closing:
                    self._closing = True
                    target_reader.feed_eof()

            def abort(self) -> None:
                self.close()

            def is_closing(self) -> bool:  # type: ignore[override]
                return self._closing

            def get_extra_info(  # type: ignore[override]
                self, name: str, default: Any = None
            ) -> Any:
                return default

        transport = _T()  # type: ignore[abstract]
        protocol = asyncio.StreamReaderProtocol(target_reader, loop=loop)
        return asyncio.StreamWriter(transport, protocol, target_reader, loop)

    def close(self) -> None:
        if self.client_writer is not None:
            self.client_writer.close()
        if self.server_writer is not None:
            self.server_writer.close()
        self.client_reader.feed_eof()
        self.server_reader.feed_eof()


# ---------------------------------------------------------------------------
# TestFraming — direct tests of the byte-level framing
# ---------------------------------------------------------------------------


def _make_bidir() -> _BidiClientPipe:
    return _BidiClientPipe()


class TestFraming:
    async def test_read_well_formed_message(self) -> None:
        pipe = _make_bidir()
        try:
            pipe.server_writer.write(b'Content-Length: 17\r\n\r\n{"jsonrpc":"2.0"}')
            await pipe.server_writer.drain()
            msg = await read_message(pipe.client_reader)
            assert msg == {"jsonrpc": "2.0"}
        finally:
            pipe.close()

    async def test_read_multiple_messages_in_one_buffer(self) -> None:
        pipe = _make_bidir()
        try:
            body1 = b'{"id":1}'
            body2 = b'{"id":2}'
            buf = (
                f"Content-Length: {len(body1)}\r\n\r\n".encode()
                + body1
                + f"Content-Length: {len(body2)}\r\n\r\n".encode()
                + body2
            )
            pipe.server_writer.write(buf)
            await pipe.server_writer.drain()
            m1 = await read_message(pipe.client_reader)
            m2 = await read_message(pipe.client_reader)
            assert m1 == {"id": 1}
            assert m2 == {"id": 2}
        finally:
            pipe.close()

    async def test_read_returns_none_on_eof(self) -> None:
        pipe = _make_bidir()
        pipe.server_writer.close()
        await asyncio.sleep(0)  # let eof propagate
        assert await read_message(pipe.client_reader) is None

    async def test_read_rejects_oversized_content_length(self) -> None:
        pipe = _make_bidir()
        try:
            huge = 100 * 1024 * 1024  # > _MAX_CONTENT_LENGTH
            pipe.server_writer.write(f"Content-Length: {huge}\r\n\r\n".encode())
            await pipe.server_writer.drain()
            with pytest.raises(LSPProtocolError):
                await read_message(pipe.client_reader)
        finally:
            pipe.close()

    async def test_write_then_read_roundtrip(self) -> None:
        pipe = _make_bidir()
        try:
            await write_message(
                pipe.client_writer,
                {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            )
            msg = await read_message(pipe.server_reader)
            assert msg == {"jsonrpc": "2.0", "id": 7, "method": "ping"}
        finally:
            pipe.close()

    async def test_read_rejects_missing_content_length(self) -> None:
        pipe = _make_bidir()
        try:
            pipe.server_writer.write(b"\r\n\r\n")
            await pipe.server_writer.drain()
            with pytest.raises(LSPProtocolError):
                await read_message(pipe.client_reader)
        finally:
            pipe.close()

    async def test_read_rejects_invalid_json(self) -> None:
        pipe = _make_bidir()
        try:
            pipe.server_writer.write(b"Content-Length: 5\r\n\r\nnot j")
            await pipe.server_writer.drain()
            with pytest.raises(LSPProtocolError):
                await read_message(pipe.client_reader)
        finally:
            pipe.close()


# ---------------------------------------------------------------------------
# Mock LSP server
# ---------------------------------------------------------------------------


class MockLspServer:
    """In-process mock LSP server.

    Drives a :class:`LanguageServerClient` over an in-memory stdio pair. The
    mock auto-responds to ``initialize``/``initialized`` and lets tests
    register canned responses keyed by JSON-RPC ``method``.  Server
    notifications and requests are recorded and exposed for assertions.
    """

    def __init__(self) -> None:
        self.sent_to_client: list[dict[str, Any]] = []
        self.requests_by_method: dict[str, list[dict[str, Any]]] = {}
        self.canned: dict[str, Any] = {}
        self.canned_errors: dict[str, dict[str, Any]] = {}
        self.silent_methods: set[str] = set()
        self.delay_seconds: float = 0.0
        self.client_streams: _BidiClientPipe | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._next_id = 1000

    def set_canned(self, method: str, result: Any) -> None:
        self.canned[method] = result

    def set_canned_error(self, method: str, code: int, message: str) -> None:
        """Make the server reply to *method* with a JSON-RPC error."""
        self.canned_errors[method] = {"code": code, "message": message}

    def silence(self, method: str) -> None:
        """Record requests for *method* but never answer them."""
        self.silent_methods.add(method)

    def set_delay(self, seconds: float) -> None:
        self.delay_seconds = seconds

    async def send_notification(self, method: str, params: Any) -> None:
        assert self.client_streams is not None
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await write_message(self.client_streams.server_writer, msg)

    async def send_request(self, method: str, params: Any) -> int:
        """Push a server-to-client request; returns the request id."""
        assert self.client_streams is not None
        self._next_id += 1
        req_id = self._next_id
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        await write_message(self.client_streams.server_writer, msg)
        return req_id

    async def _serve(self) -> None:
        assert self.client_streams is not None
        reader = self.client_streams.server_reader
        writer = self.client_streams.server_writer
        while True:
            try:
                msg = await read_message(reader)
            except LSPProtocolError:
                return
            if msg is None:
                return
            self.sent_to_client.append(msg)
            await self._handle_message(msg, writer)

    async def _handle_message(
        self, msg: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        method = msg.get("method")
        req_id = msg.get("id")
        if method is not None and req_id is not None:
            self.requests_by_method.setdefault(method, []).append(msg)
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if method in self.silent_methods:
                return
            if method in self.canned_errors:
                await write_message(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": self.canned_errors[method],
                    },
                )
                return
            result = self.canned.get(method)
            response: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result,
            }
            await write_message(writer, response)
            return
        if method is not None:
            self.requests_by_method.setdefault(method, []).append(msg)


# ---------------------------------------------------------------------------
# Wire-up helpers
# ---------------------------------------------------------------------------


def _wire_client_with_mock(
    tmp_path: Path, **client_kwargs: Any
) -> tuple[LanguageServerClient, MockLspServer, _BidiClientPipe]:
    pipe = _BidiClientPipe()
    mock = MockLspServer()
    mock.client_streams = pipe
    client = LanguageServerClient(workspace_root=tmp_path, **client_kwargs)
    return client, mock, pipe


async def _start_wired(
    client: LanguageServerClient, mock: MockLspServer, pipe: _BidiClientPipe
) -> None:
    """Attach the pipe to the client, start the mock server, and handshake."""
    client._attach_pipes(
        stdin=pipe.client_writer,
        stdout=pipe.client_reader,
        stderr=asyncio.StreamReader(),
    )
    mock.client_streams = pipe
    mock._server_task = asyncio.create_task(mock._serve())
    try:
        await client._do_handshake()
    except Exception:
        # Mirror LanguageServerClient.start(): a failed handshake must tear
        # down the pipes and reader tasks rather than leave them dangling.
        await client._kill()
        raise
    # Yield so the mock server's _serve task processes any trailing
    # notifications (``initialized``) sent during the handshake.
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestUriHelpers:
    def test_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "hello.cpp"
        p.write_text("// hi")
        uri = path_to_uri(p)
        assert uri.startswith("file://")
        assert Path(uri_to_path(uri)) == p

    def test_uri_to_path_rejects_non_file(self) -> None:
        with pytest.raises(ValueError):
            uri_to_path("https://example.com/x.cpp")


class TestRegistryWorkspaceDetection:
    def test_py_resolves_to_pyright(self, tmp_path: Path) -> None:
        target = tmp_path / "pkg" / "module.py"
        target.parent.mkdir(parents=True)
        target.write_text("value = 1\n")

        spec = get_server_spec_for_path(target)

        assert spec.server_id == "pyright"

    def test_cpp_resolves_to_clangd(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "main.cpp"
        target.parent.mkdir(parents=True)
        target.write_text("int main() { return 0; }\n")

        spec = get_server_spec_for_path(target)

        assert spec.server_id == "clangd"

    def test_nearest_language_specific_root_wins_over_outer_git(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        inner = tmp_path / "services" / "pyproj"
        inner.mkdir(parents=True)
        (inner / "pyproject.toml").write_text("[project]\nname='demo'\n")
        target = inner / "app.py"
        target.write_text("print('hi')\n")

        spec, context = detect_workspace_context(target)

        assert spec.server_id == "pyright"
        assert context.workspace_root == inner

    def test_python_interpreter_prefers_local_dot_venv(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
        interpreter = tmp_path / ".venv" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("#!/usr/bin/env python3\n")
        target = tmp_path / "main.py"
        target.write_text("print('hi')\n")

        _, context = detect_workspace_context(target)

        assert context.python_executable == interpreter
        assert context.python_venv_path == tmp_path / ".venv"

    def test_pyproject_tool_pyright_sets_config_flag(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname='demo'\n\n[tool.pyright]\ntypeCheckingMode='standard'\n"
        )
        target = tmp_path / "main.py"
        target.write_text("print('hi')\n")

        _, context = detect_workspace_context(target)

        assert context.has_pyright_config is True


class _FakeManagedClient:
    created: list[_FakeManagedClient] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.__class__.created.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeConfigurableClient(_FakeManagedClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.configuration_changes: list[dict[str, Any]] = []

    async def did_change_configuration(self, settings: dict[str, Any]) -> None:
        self.configuration_changes.append(settings)


class TestLanguageServerClientManager:
    async def test_manager_reuses_same_backend_root_client(
        self, tmp_path: Path
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname='demo'\n")
        target = tmp_path / "app.py"
        target.write_text("print('hi')\n")
        manager = LanguageServerClientManager()
        _FakeManagedClient.created.clear()

        with patch("agent.lsp.registry.LanguageServerClient", _FakeManagedClient):
            client1, spec1, context1 = await manager.get_client_for_path(target)
            client2, spec2, context2 = await manager.get_client_for_path(target)

        assert client1 is client2
        assert spec1.server_id == spec2.server_id == "pyright"
        assert context1.workspace_root == context2.workspace_root == tmp_path
        assert len(_FakeManagedClient.created) == 1
        assert _FakeManagedClient.created[0].started is True

    async def test_manager_reset_stops_all_pooled_clients(self, tmp_path: Path) -> None:
        py_root = tmp_path / "pyproj"
        py_root.mkdir()
        (py_root / "pyproject.toml").write_text("[project]\nname='demo'\n")
        py_file = py_root / "app.py"
        py_file.write_text("print('hi')\n")

        cpp_root = tmp_path / "cppproj"
        cpp_root.mkdir()
        (cpp_root / ".clangd").write_text("CompileFlags:\n")
        cpp_file = cpp_root / "main.cpp"
        cpp_file.write_text("int main() { return 0; }\n")

        manager = LanguageServerClientManager()
        _FakeManagedClient.created.clear()

        with patch("agent.lsp.registry.LanguageServerClient", _FakeManagedClient):
            await manager.get_client_for_path(py_file)
            await manager.get_client_for_path(cpp_file)
            await manager.reset()

        assert len(_FakeManagedClient.created) == 2
        assert all(client.started for client in _FakeManagedClient.created)
        assert all(client.stopped for client in _FakeManagedClient.created)

    async def test_configure_client_uses_spec_configuration_builder(
        self, tmp_path: Path
    ) -> None:
        client = _FakeConfigurableClient()
        context = WorkspaceContext(
            path=tmp_path / "main.py",
            language="python",
            workspace_root=tmp_path,
        )
        spec = ServerSpec(
            server_id="custom",
            language_id="python",
            file_extensions=frozenset({".py"}),
            root_markers=("pyproject.toml",),
            configuration_builder=lambda _context: {"custom": {"enabled": True}},
        )

        await _configure_client(client, spec, context)  # type: ignore[arg-type]

        assert client.configuration_changes == [{"custom": {"enabled": True}}]


class TestInitializeHandshake:
    async def test_initialize_sends_root_uri_and_capabilities(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            init_msgs = mock.requests_by_method.get("initialize", [])
            assert len(init_msgs) == 1
            params = init_msgs[0]["params"]
            assert params["rootUri"] == workspace.as_uri()
            assert "capabilities" in params
            assert "processId" in params
            assert mock.requests_by_method.get("initialized")
        finally:
            await client.stop()
            pipe.close()

    async def test_initialize_timeout_raises(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace, startup_timeout=0.2)
        try:
            mock.set_delay(1.0)
            with pytest.raises((asyncio.TimeoutError, OSError)):
                await _start_wired(client, mock, pipe)
        finally:
            await client.stop()
            pipe.close()


class TestCompletionRequest:
    async def test_completion(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned(
                "textDocument/completion",
                {"isIncomplete": False, "items": [{"label": "foo"}]},
            )
            await _start_wired(client, mock, pipe)
            test_file = workspace / "a.cpp"
            test_file.write_text("int main() {}\n")
            result = await client.completion(str(test_file), 1, 0)
            assert result.get("items") == [{"label": "foo"}]
            sent = mock.requests_by_method["textDocument/completion"]
            assert sent[0]["params"]["textDocument"]["uri"] == path_to_uri(test_file)
        finally:
            await client.stop()
            pipe.close()

    async def test_completion_accepts_bare_list(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned("textDocument/completion", [{"label": "bar"}])
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("//\n")
            result = await client.completion(str(f), 0, 0)
            assert result.get("items") == [{"label": "bar"}]
        finally:
            await client.stop()
            pipe.close()


class TestDefinitionReferences:
    async def test_definition(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned(
                "textDocument/definition",
                [
                    {
                        "uri": "file:///x.cpp",
                        "range": {
                            "start": {"line": 5, "character": 0},
                            "end": {"line": 5, "character": 3},
                        },
                    }
                ],
            )
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("//\n")
            result = await client.definition(str(f), 0, 0)
            assert len(result) == 1
            assert result[0].get("uri") == "file:///x.cpp"
        finally:
            await client.stop()
            pipe.close()

    async def test_references(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned(
                "textDocument/references",
                [{"uri": "file:///y.cpp", "range": {}}],
            )
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("//\n")
            result = await client.references(str(f), 0, 0, include_declaration=False)
            assert len(result) == 1
            sent = mock.requests_by_method["textDocument/references"]
            assert sent[0]["params"]["context"]["includeDeclaration"] is False
        finally:
            await client.stop()
            pipe.close()


class TestSymbols:
    async def test_document_symbol(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned(
                "textDocument/documentSymbol",
                [{"name": "foo", "kind": 6, "range": {}, "selectionRange": {}}],
            )
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("void foo() {}\n")
            syms = await client.document_symbol(str(f))
            assert syms and syms[0].get("name") == "foo"
        finally:
            await client.stop()
            pipe.close()

    async def test_workspace_symbol(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned("workspace/symbol", [{"name": "global_fn", "kind": 12}])
            await _start_wired(client, mock, pipe)
            result = await client.workspace_symbol("global")
            assert result[0].get("name") == "global_fn"
            sent = mock.requests_by_method["workspace/symbol"]
            assert sent[0]["params"]["query"] == "global"
        finally:
            await client.stop()
            pipe.close()


class TestRename:
    async def test_rename(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned(
                "textDocument/rename",
                {"changes": {"file:///a.cpp": [{"newText": "bar", "range": {}}]}},
            )
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int foo;\n")
            result = await client.rename(str(f), 0, 4, "bar")
            assert result is not None
            assert "changes" in result
        finally:
            await client.stop()
            pipe.close()


class TestHierarchies:
    async def test_type_hierarchy(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned(
                "textDocument/prepareTypeHierarchy",
                [{"name": "Base", "kind": 5, "uri": "file:///a.h", "range": {}}],
            )
            mock.set_canned(
                "typeHierarchy/subtypes",
                [{"name": "Derived", "kind": 5, "uri": "file:///b.h", "range": {}}],
            )
            await _start_wired(client, mock, pipe)
            f = workspace / "a.h"
            f.write_text("class Base {};\n")
            items = await client.prepare_type_hierarchy(str(f), 0, 6)
            assert items[0].get("name") == "Base"
            sub = await client.type_hierarchy_subtypes(items[0])
            assert sub[0].get("name") == "Derived"
        finally:
            await client.stop()
            pipe.close()

    async def test_call_hierarchy(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned(
                "textDocument/prepareCallHierarchy",
                [{"name": "main", "kind": 12, "uri": "file:///main.cpp", "range": {}}],
            )
            mock.set_canned(
                "callHierarchy/outgoingCalls",
                [
                    {
                        "to": {
                            "name": "helper",
                            "kind": 12,
                            "uri": "file:///a.cpp",
                            "range": {},
                        },
                        "fromRanges": [],
                    }
                ],
            )
            mock.set_canned("callHierarchy/incomingCalls", [])
            await _start_wired(client, mock, pipe)
            f = workspace / "main.cpp"
            f.write_text("int main(){helper();}\n")
            items = await client.prepare_call_hierarchy(str(f), 0, 4)
            assert items[0].get("name") == "main"
            out = await client.call_hierarchy_outgoing(items[0])
            assert out[0].get("to", {}).get("name") == "helper"
            inc = await client.call_hierarchy_incoming(items[0])
            assert inc == []
        finally:
            await client.stop()
            pipe.close()


class TestDocumentLifecycle:
    async def test_did_open_sends_full_text(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await client.did_open(str(workspace / "a.cpp"), "int x;\n")
            await asyncio.sleep(0)  # let server task process the notification
            notifs = mock.requests_by_method.get("textDocument/didOpen", [])
            assert len(notifs) == 1
            assert notifs[0]["params"]["textDocument"]["text"] == "int x;\n"
        finally:
            await client.stop()
            pipe.close()

    async def test_did_change_increments_version(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await client.did_open(str(workspace / "a.cpp"), "int x;\n")
            await client.did_change(str(workspace / "a.cpp"), 2, "int y;\n")
            await asyncio.sleep(0)  # let server task process notifications
            notifs = mock.requests_by_method.get("textDocument/didChange", [])
            assert len(notifs) == 1
            assert notifs[0]["params"]["textDocument"]["version"] == 2
            assert notifs[0]["params"]["contentChanges"] == [{"text": "int y;\n"}]
        finally:
            await client.stop()
            pipe.close()

    async def test_did_close(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await client.did_open(str(workspace / "a.cpp"), "//\n")
            await client.did_close(str(workspace / "a.cpp"))
            await asyncio.sleep(0)  # let server task process the notification
            notifs = mock.requests_by_method.get("textDocument/didClose", [])
            assert len(notifs) == 1
        finally:
            await client.stop()
            pipe.close()

    async def test_ensure_open_reads_from_disk(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned("textDocument/documentSymbol", [])
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("void a() {}\n")
            await client.ensure_open(str(f))
            await client.document_symbol(str(f))
            notifs = mock.requests_by_method.get("textDocument/didOpen", [])
            assert (
                notifs
                and notifs[0]["params"]["textDocument"]["text"] == "void a() {}\n"
            )
        finally:
            await client.stop()
            pipe.close()

    async def test_sync_document_sends_did_open_on_first_access(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")

            changed = await client.sync_document(str(f))
            await asyncio.sleep(0)

            assert changed is True
            notifs = mock.requests_by_method.get("textDocument/didOpen", [])
            assert len(notifs) == 1
            assert notifs[0]["params"]["textDocument"]["text"] == "int x;\n"
        finally:
            await client.stop()
            pipe.close()

    async def test_sync_document_sends_did_change_only_on_content_drift(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")

            assert await client.sync_document(str(f)) is True
            assert await client.sync_document(str(f)) is False
            f.write_text("int y;\n")
            assert await client.sync_document(str(f)) is True
            await asyncio.sleep(0)

            notifs = mock.requests_by_method.get("textDocument/didChange", [])
            assert len(notifs) == 1
            assert notifs[0]["params"]["textDocument"]["version"] == 2
            assert notifs[0]["params"]["contentChanges"] == [{"text": "int y;\n"}]
        finally:
            await client.stop()
            pipe.close()


class TestDiagnostics:
    async def test_diagnostics_notification_stored(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")
            await mock.send_notification(
                "textDocument/publishDiagnostics",
                {
                    "uri": path_to_uri(f),
                    "diagnostics": [{"range": {}, "severity": 1, "message": "oops"}],
                },
            )
            # Single-step: server writes → client reads → stores sync in
            # _handle_notification. A single yield is sufficient.
            await asyncio.sleep(0)
            diags = client.get_diagnostics(str(f))
            assert len(diags) == 1
            assert diags[0].get("message") == "oops"
        finally:
            await client.stop()
            pipe.close()

    async def test_await_diagnostics_wakes_waiters(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")

            generation = client.diagnostics_generation(str(f))
            waiter = asyncio.create_task(
                client.await_diagnostics(
                    str(f), timeout=1.0, after_generation=generation
                )
            )
            await asyncio.sleep(0)
            await mock.send_notification(
                "textDocument/publishDiagnostics",
                {
                    "uri": path_to_uri(f),
                    "diagnostics": [
                        {"range": {}, "severity": 1, "message": "oops again"}
                    ],
                },
            )

            diags = await waiter
            assert len(diags) == 1
            assert diags[0].get("message") == "oops again"
            assert client.diagnostics_generation(str(f)) == generation + 1
        finally:
            await client.stop()
            pipe.close()


class TestServerRequests:
    async def test_workspace_configuration_acked(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await mock.send_request(
                "workspace/configuration",
                {"items": [{"section": "clangd"}, {"section": "other"}]},
            )
            # Two-step pipeline: client's _read_loop reads the server request,
            # then _handle_server_request (in a separate create_task) writes the
            # reply, then _serve reads it. Each step is one event-loop cycle, so
            # a single asyncio.sleep(0) is insufficient — we need the wall-clock
            # yield to flush all three cycles.
            await asyncio.sleep(0.05)
            # Filter for client responses (have id but no method).
            responses = [
                m
                for m in mock.sent_to_client
                if m.get("id") is not None and "method" not in m
            ]
            assert len(responses) == 1
            assert responses[0]["result"] == [None, None]
        finally:
            await client.stop()
            pipe.close()

    async def test_register_capability_acked(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await mock.send_request("client/registerCapability", {"registrations": []})
            # See workspace_configuration_acked — same two-step pipeline.
            await asyncio.sleep(0.05)
            responses = [
                m
                for m in mock.sent_to_client
                if m.get("id") is not None and "method" not in m
            ]
            assert len(responses) == 1
            assert responses[0]["result"] is None
        finally:
            await client.stop()
            pipe.close()

    async def test_unknown_server_request_returns_method_not_found(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await mock.send_request("totally/unknown", {})
            # See workspace_configuration_acked — same two-step pipeline.
            await asyncio.sleep(0.05)
            responses = [
                m
                for m in mock.sent_to_client
                if m.get("id") is not None and "method" not in m
            ]
            assert len(responses) == 1
            assert responses[0]["error"]["code"] == -32601
        finally:
            await client.stop()
            pipe.close()


class TestStop:
    async def test_stop_is_idempotent(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await client.stop()
            await client.stop()  # no exception
        finally:
            pipe.close()

    async def test_async_context_manager_starts_and_stops(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})

            async def fake_start() -> None:
                client._attach_pipes(
                    pipe.client_writer,
                    pipe.client_reader,
                    asyncio.StreamReader(),
                )
                mock.client_streams = pipe
                mock._server_task = asyncio.create_task(mock._serve())
                await client._do_handshake()

            orig_stop = client.stop

            async def fake_stop() -> None:
                await orig_stop()

            client.start = fake_start  # type: ignore[method-assign]
            client.stop = fake_stop  # type: ignore[method-assign]
            async with client:
                assert client._process is not None
            assert client._process is None
        finally:
            pipe.close()


class TestLanguageServerMissing:
    async def test_start_raises_when_server_not_found(self, workspace: Path) -> None:
        client = LanguageServerClient(
            command=("definitely_not_language_server_xyz",),
            workspace_root=workspace,
        )
        with patch("agent.lsp.client.shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="language server not found"):
                await client.start()


class TestRequestErrorPaths:
    """Error handling of the request/response machinery."""

    async def test_json_rpc_error_response_raises_lsp_error(
        self, workspace: Path
    ) -> None:
        from agent.lsp.client import LSPError

        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned_error("workspace/symbol", -32600, "bad request")
            await _start_wired(client, mock, pipe)
            with pytest.raises(LSPError) as exc_info:
                await client.workspace_symbol("anything")
            assert exc_info.value.code == -32600
            assert exc_info.value.message == "bad request"
            assert "LSP error -32600" in str(exc_info.value)
        finally:
            await client.stop()
            pipe.close()

    async def test_request_timeout_on_normal_request(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace, request_timeout=0.2)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.silence("workspace/symbol")
            await _start_wired(client, mock, pipe)
            with pytest.raises(asyncio.TimeoutError):
                await client.workspace_symbol("anything")
            # The pending future must have been cleaned up.
            assert not client._pending
        finally:
            await client.stop()
            pipe.close()

    async def test_request_before_start_raises_connection_error(
        self, workspace: Path
    ) -> None:
        client, _mock, _pipe = _wire_client_with_mock(workspace)
        with pytest.raises(ConnectionError, match="not started"):
            await client.workspace_symbol("anything")

    async def test_request_after_stop_raises_connection_error(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
        finally:
            await client.stop()
            pipe.close()
        with pytest.raises(ConnectionError, match="stopped"):
            await client.workspace_symbol("anything")

    async def test_server_crash_rejects_pending_requests(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.silence("workspace/symbol")
            await _start_wired(client, mock, pipe)

            task = asyncio.create_task(client.workspace_symbol("anything"))
            await asyncio.sleep(0.05)
            assert not task.done()

            # Simulate a server crash: close the pipe (EOF on client stdout)
            # and cancel the mock server task.
            assert mock._server_task is not None
            mock._server_task.cancel()
            pipe.server_writer.close()

            # The read loop must reject the in-flight request promptly
            # instead of letting it block until its own timeout.
            with pytest.raises(ConnectionError, match="connection lost"):
                await asyncio.wait_for(task, timeout=2.0)
        finally:
            await client.stop()
            pipe.close()

    async def test_protocol_error_ends_read_loop_and_requests_fail(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace, request_timeout=0.3)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)

            # Feed garbage that violates the framing rules directly into
            # the client's stdout stream.
            pipe.server_writer.write(b"Content-Length: 999999999999\r\n\r\n")
            await asyncio.sleep(0.05)

            # A new request must fail immediately once the read loop ends.
            with pytest.raises(ConnectionError, match="connection lost"):
                await client.workspace_symbol("anything")
        finally:
            await client.stop()
            pipe.close()

    async def test_unknown_response_id_is_dropped(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace, request_timeout=0.3)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.silence("workspace/symbol")
            await _start_wired(client, mock, pipe)

            # Push a response with an id that matches no pending request.
            await mock.send_notification("ignored", {})  # warm the pipeline
            await write_message(
                pipe.server_writer,
                {"jsonrpc": "2.0", "id": 99999, "result": {"bogus": True}},
            )
            await asyncio.sleep(0.05)

            # The stale response must not have answered anything, and the
            # client must still be functional.
            with pytest.raises(asyncio.TimeoutError):
                await client.workspace_symbol("anything")
        finally:
            await client.stop()
            pipe.close()

    async def test_stop_with_wedged_server_still_terminates(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.silence("shutdown")
            await _start_wired(client, mock, pipe)
            # Must not hang: shutdown request times out, kill fallback runs.
            await asyncio.wait_for(client.stop(), timeout=10.0)
            assert client._process is None
        finally:
            pipe.close()

    async def test_handshake_failure_cleans_up(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace, startup_timeout=0.2)
        try:
            mock.set_delay(1.0)
            with pytest.raises((asyncio.TimeoutError, OSError)):
                await _start_wired(client, mock, pipe)
            # _kill() must have run: no process, no pending futures, no tasks.
            assert client._process is None
            assert not client._pending
            assert client._reader_task is None
            assert client._stderr_task is None
        finally:
            pipe.close()

    async def test_did_open_twice_sends_did_change(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            await client.did_open(str(f), "int x;\n")
            await client.did_open(str(f), "int y;\n")
            await asyncio.sleep(0)
            opens = mock.requests_by_method.get("textDocument/didOpen", [])
            changes = mock.requests_by_method.get("textDocument/didChange", [])
            assert len(opens) == 1
            assert len(changes) == 1
            assert changes[0]["params"]["textDocument"]["version"] == 2
            assert changes[0]["params"]["contentChanges"] == [{"text": "int y;\n"}]
        finally:
            await client.stop()
            pipe.close()

    async def test_await_diagnostics_times_out(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")
            with pytest.raises(asyncio.TimeoutError):
                await client.await_diagnostics(str(f), timeout=0.1)
        finally:
            await client.stop()
            pipe.close()

    async def test_log_ring_records_window_messages(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await mock.send_notification(
                "window/logMessage", {"type": 3, "message": "hello log"}
            )
            await asyncio.sleep(0.05)
            ring = client.recent_log()
            assert {"type": 3, "message": "hello log"} in ring
        finally:
            await client.stop()
            pipe.close()


class TestClientEdgeCases:
    """Behaviours not exercised by the happy-path feature tests."""

    async def test_start_twice_raises_runtime_error(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            with pytest.raises(RuntimeError, match="already started"):
                await client.start()
        finally:
            await client.stop()
            pipe.close()

    async def test_start_rejects_missing_workspace_root(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        client = LanguageServerClient(workspace_root=missing)
        with pytest.raises(FileNotFoundError, match="workspace root does not exist"):
            await client.start()

    async def test_notify_before_start_raises_connection_error(
        self, workspace: Path
    ) -> None:
        client, _mock, _pipe = _wire_client_with_mock(workspace)
        with pytest.raises(ConnectionError, match="not started"):
            await client.did_change_configuration({"a": 1})

    async def test_request_write_failure_clears_pending(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)

            # Make the next write blow up; the pending future must not leak.
            def boom(_data: bytes) -> None:
                raise OSError("broken pipe")

            pipe.client_writer.write = boom  # type: ignore[method-assign]
            with pytest.raises(OSError, match="broken pipe"):
                await client.workspace_symbol("anything")
            assert not client._pending
        finally:
            await client.stop()
            pipe.close()

    async def test_did_change_without_open_synthesises_did_open(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            await client.did_change(str(f), 5, "int z;\n")
            await asyncio.sleep(0)
            opens = mock.requests_by_method.get("textDocument/didOpen", [])
            changes = mock.requests_by_method.get("textDocument/didChange", [])
            assert len(opens) == 1
            assert not changes
        finally:
            await client.stop()
            pipe.close()

    async def test_did_close_unopened_document_is_noop(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await client.did_close(str(workspace / "never-opened.cpp"))
            await asyncio.sleep(0)
            assert "textDocument/didClose" not in mock.requests_by_method
        finally:
            await client.stop()
            pipe.close()

    async def test_ensure_open_is_idempotent(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")
            await client.ensure_open(str(f))
            await client.ensure_open(str(f))
            await asyncio.sleep(0)
            opens = mock.requests_by_method.get("textDocument/didOpen", [])
            assert len(opens) == 1
        finally:
            await client.stop()
            pipe.close()

    async def test_sync_document_reports_whether_it_sent(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")
            assert await client.sync_document(str(f)) is True  # didOpen
            assert await client.sync_document(str(f)) is False  # unchanged
            f.write_text("int y;\n")
            assert await client.sync_document(str(f)) is True  # didChange
        finally:
            await client.stop()
            pipe.close()

    async def test_server_capabilities_empty_without_initialize(
        self, workspace: Path
    ) -> None:
        client, _mock, _pipe = _wire_client_with_mock(workspace)
        assert client.server_capabilities() == {}

    async def test_server_capabilities_returns_capabilities_dict(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {"hoverProvider": True}})
            await _start_wired(client, mock, pipe)
            assert client.server_capabilities() == {"hoverProvider": True}
        finally:
            await client.stop()
            pipe.close()

    async def test_server_capabilities_empty_when_key_missing(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {})
            await _start_wired(client, mock, pipe)
            assert client.server_capabilities() == {}
        finally:
            await client.stop()
            pipe.close()

    async def test_unknown_notification_is_ignored(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await mock.send_notification("$/progress", {"token": "x"})
            await mock.send_notification("totally/unknown", {"a": 1})
            await asyncio.sleep(0.05)
            # Neither notification should land in the log ring.
            assert client.recent_log() == []
        finally:
            await client.stop()
            pipe.close()

    async def test_stderr_output_is_drained(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            # The stderr reader was wired to an empty StreamReader in
            # _start_wired; feeding EOF must let the drain task finish.
            assert client._stderr_task is not None
            client._process.stderr.feed_eof()  # type: ignore[union-attr]
            await asyncio.sleep(0.05)
            assert client._stderr_task.done()
        finally:
            await client.stop()
            pipe.close()

    async def test_kill_falls_back_to_kill_when_terminate_hangs(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            process = client._process
            assert process is not None

            # terminate() does nothing and the first wait() times out,
            # forcing the kill() fallback path.
            process.terminate = lambda: None  # type: ignore[method-assign]
            calls = 0

            async def wait() -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise TimeoutError
                return 0

            process.wait = wait  # type: ignore[method-assign]
            killed = False

            def record_kill() -> None:
                nonlocal killed
                killed = True

            process.kill = record_kill  # type: ignore[method-assign]

            await asyncio.wait_for(client._kill(), timeout=10.0)
            assert killed
            assert calls == 2
            assert client._process is None
        finally:
            pipe.close()

    async def test_reply_ignored_when_process_gone(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            client._process = None
            # Must not raise even though there is nowhere to write.
            await client._reply(1, {"ok": True})
        finally:
            await client.stop()
            pipe.close()

    async def test_reply_write_failure_is_swallowed(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)

            def boom(_data: bytes) -> None:
                raise OSError("broken pipe")

            pipe.client_writer.write = boom  # type: ignore[method-assign]
            # _reply logs and returns instead of propagating the write error.
            await client._reply(1, {"ok": True})
        finally:
            await client.stop()
            pipe.close()

    async def test_reject_pending_skips_already_done_futures(
        self, workspace: Path
    ) -> None:
        client, _mock, _pipe = _wire_client_with_mock(workspace)
        loop = asyncio.get_running_loop()
        done: asyncio.Future[Any] = loop.create_future()
        done.set_result("already")
        pending: asyncio.Future[Any] = loop.create_future()
        client._pending[1] = done
        client._pending[2] = pending

        client._reject_pending(ConnectionError("boom"))

        assert done.result() == "already"  # untouched
        assert isinstance(pending.exception(), ConnectionError)
        assert not client._pending

    async def test_drain_stderr_logs_non_empty_lines(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            assert client._process is not None
            stderr = client._process.stderr
            assert stderr is not None
            stderr.feed_data(b"warning: something\n\n")
            stderr.feed_eof()
            await asyncio.sleep(0.05)
            assert client._stderr_task is not None
            assert client._stderr_task.done()
        finally:
            await client.stop()
            pipe.close()

    async def test_rename_returns_none_for_non_dict_result(
        self, workspace: Path
    ) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            mock.set_canned("textDocument/rename", ["not", "a", "dict"])
            await _start_wired(client, mock, pipe)
            f = workspace / "a.cpp"
            f.write_text("int x;\n")
            assert await client.rename(str(f), 0, 0, "y") is None
        finally:
            await client.stop()
            pipe.close()

    async def test_reset_default_client_when_unset_is_noop(self) -> None:
        import agent.lsp.client as client_module

        await client_module.reset_default_client()
        assert client_module._default_client is None
        # Calling again with no singleton must not raise.
        await client_module.reset_default_client()
        assert client_module._default_client is None

    def test_empty_command_rejected(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="command must not be empty"):
            LanguageServerClient(command=(), workspace_root=workspace)

    async def test_start_cleans_up_when_handshake_fails(self, workspace: Path) -> None:
        client = LanguageServerClient(
            command=("fake-server",), workspace_root=workspace, startup_timeout=0.2
        )
        pipe = _BidiClientPipe()
        mock = MockLspServer()
        mock.client_streams = pipe
        mock.set_delay(1.0)  # never answers initialize in time

        async def fake_exec(*_args: Any, **_kwargs: Any) -> Any:
            class _Proc:
                stdin = pipe.client_writer
                stdout = pipe.client_reader
                stderr = asyncio.StreamReader()
                returncode: int | None = None

                async def wait(self) -> int:
                    return 0

                def terminate(self) -> None:
                    self.returncode = -15

                def kill(self) -> None:
                    self.returncode = -9

            return _Proc()

        try:
            with (
                patch("agent.lsp.client.shutil.which", return_value="/fake/server"),
                patch(
                    "agent.lsp.client.asyncio.create_subprocess_exec",
                    side_effect=fake_exec,
                ),
            ):
                mock._server_task = asyncio.create_task(mock._serve())
                with pytest.raises(asyncio.TimeoutError):
                    await client.start()
            # start()'s except branch must have run _kill().
            assert client._process is None
            assert client._reader_task is None
            assert client._stderr_task is None
            assert not client._pending
        finally:
            pipe.close()

    async def test_protocol_error_ends_read_loop(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            # Oversized Content-Length violates the framing rules and makes
            # read_message raise LSPProtocolError inside the read loop.
            pipe.server_writer.write(b"Content-Length: 999999999999\r\n\r\n")
            await asyncio.sleep(0.05)
            assert client._reader_task is not None
            assert client._reader_task.done()
        finally:
            await client.stop()
            pipe.close()

    async def test_work_done_progress_create_acked(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await mock.send_request("window/workDoneProgress/create", {"token": "t"})
            await asyncio.sleep(0.05)
            responses = [
                m
                for m in mock.sent_to_client
                if m.get("id") is not None and "method" not in m
            ]
            assert len(responses) == 1
            assert responses[0]["result"] is None
        finally:
            await client.stop()
            pipe.close()

    async def test_semantic_tokens_refresh_acked(self, workspace: Path) -> None:
        client, mock, pipe = _wire_client_with_mock(workspace)
        try:
            mock.set_canned("initialize", {"capabilities": {}})
            await _start_wired(client, mock, pipe)
            await mock.send_request("workspace/semanticTokens/refresh", None)
            await asyncio.sleep(0.05)
            responses = [
                m
                for m in mock.sent_to_client
                if m.get("id") is not None and "method" not in m
            ]
            assert len(responses) == 1
            assert responses[0]["result"] is None
        finally:
            await client.stop()
            pipe.close()


class TestDefaultClientSingleton:
    async def test_get_and_reset_default_client(self, workspace: Path) -> None:
        import agent.lsp.client as client_module

        await client_module.reset_default_client()
        try:
            with (
                patch("agent.lsp.client.shutil.which", return_value="/fake/clangd"),
                patch("agent.lsp.client.asyncio.create_subprocess_exec") as fake_exec,
            ):
                # Make start() fail so we can observe the singleton state
                # without spawning a real server.
                fake_exec.side_effect = OSError("no exec in test")
                with pytest.raises(OSError):
                    await client_module.get_default_client()
                # A failed start must not leave a half-initialised singleton.
                assert client_module._default_client is not None
                await client_module.reset_default_client()
                assert client_module._default_client is None
        finally:
            await client_module.reset_default_client()


# ---------------------------------------------------------------------------
# Integration tests — require a real clangd
# ---------------------------------------------------------------------------


CLANGD_AVAILABLE = shutil.which("clangd") is not None


@pytest.mark.skipif(not CLANGD_AVAILABLE, reason="clangd binary not installed")
@pytest.mark.integration
class TestClangdIntegration:
    """Real end-to-end tests against the system clangd.

    Skipped automatically when ``clangd`` is not on ``$PATH``.
    """

    async def test_workspace_symbol_finds_main(self, tmp_path: Path) -> None:
        main_cpp = tmp_path / "main.cpp"
        main_cpp.write_text("int main() { return 0; }\n")
        client = LanguageServerClient(
            workspace_root=tmp_path,
            startup_timeout=20.0,
            request_timeout=15.0,
        )
        await client.start()
        try:
            # Without a compile_commands.json, clangd only indexes files that
            # have been explicitly opened. Open the file before querying.
            await client.did_open(str(main_cpp), main_cpp.read_text())
            found = False
            syms: list[WorkspaceSymbol] = []
            for _ in range(40):
                syms = await client.workspace_symbol("main")
                if any(s.get("name") == "main" for s in syms):
                    found = True
                    break
                await asyncio.sleep(0.5)
            assert found, f"clangd did not index 'main' in time: {syms!r}"
        finally:
            await client.stop()
