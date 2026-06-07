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
    LanguageServerClientManager,
    LanguageServerClient,
    ServerSpec,
    WorkspaceContext,
    detect_workspace_context,
    get_server_spec_for_path,
    path_to_uri,
    uri_to_path,
)
from agent.lsp.registry import _configure_client
from agent.lsp.framing import (
    LSPProtocolError,
    read_message,
    write_message,
)

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
        self.delay_seconds: float = 0.0
        self.client_streams: _BidiClientPipe | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._next_id = 1000

    def set_canned(self, method: str, result: Any) -> None:
        self.canned[method] = result

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
    await client._do_handshake()
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
    created: list["_FakeManagedClient"] = []

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

        await _configure_client(client, spec, context)

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
            assert result["items"] == [{"label": "foo"}]
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
            assert result["items"] == [{"label": "bar"}]
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
            assert result[0]["uri"] == "file:///x.cpp"
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
            assert syms and syms[0]["name"] == "foo"
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
            assert result[0]["name"] == "global_fn"
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
            assert items[0]["name"] == "Base"
            sub = await client.type_hierarchy_subtypes(items[0])
            assert sub[0]["name"] == "Derived"
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
            assert items[0]["name"] == "main"
            out = await client.call_hierarchy_outgoing(items[0])
            assert out[0]["to"]["name"] == "helper"
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
            assert diags[0]["message"] == "oops"
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
            assert diags[0]["message"] == "oops again"
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
            for _ in range(40):
                syms = await client.workspace_symbol("main")
                if any(s.get("name") == "main" for s in syms):
                    found = True
                    break
                await asyncio.sleep(0.5)
            assert found, f"clangd did not index 'main' in time: {syms!r}"
        finally:
            await client.stop()
