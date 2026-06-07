"""Tests for the generic LSP tool wrappers."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from agent.lsp import LanguageServerClient, ServerSpec, WorkspaceContext
from agent.tools import get_tools
from agent.tools.tools_lsp import (
    lsp_definition,
    lsp_diagnostics,
    lsp_document_symbols,
    lsp_references,
    lsp_rename,
    lsp_workspace_symbols,
)
from tests.test_lsp_client import (  # noqa: PLC0415
    MockLspServer,
    _BidiClientPipe,
    _start_wired,
    _wire_client_with_mock,
)


def _cpp_spec() -> ServerSpec:
    return ServerSpec(
        server_id="clangd",
        language_id="cpp",
        file_extensions=frozenset({".cpp", ".h"}),
        root_markers=(".clangd", ".git"),
    )


def _cpp_context(path: Path, workspace_root: Path) -> WorkspaceContext:
    return WorkspaceContext(path=path, language="cpp", workspace_root=workspace_root)


class _DummyClient:
    def __getattr__(self, name: str) -> Any:  # pragma: no cover - defensive
        raise AssertionError(
            f"_DummyClient.{name} was called but the request should have been rejected first"
        )


class _UnsupportedClient:
    def server_capabilities(self) -> dict[str, Any]:
        return {}

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - defensive
        raise AssertionError(
            f"_UnsupportedClient.{name} was called despite missing capability"
        )


@pytest.fixture
async def wired_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[
    tuple[LanguageServerClient, MockLspServer, _BidiClientPipe, Path], None
]:
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        client, mock, pipe = _wire_client_with_mock(tmp_path)
        mock.set_canned(
            "initialize",
            {
                "capabilities": {
                    "definitionProvider": True,
                    "referencesProvider": True,
                    "documentSymbolProvider": True,
                    "workspaceSymbolProvider": True,
                    "renameProvider": True,
                }
            },
        )
        await _start_wired(client, mock, pipe)

        async def _resolve(path: str, language: str = "") -> Any:
            return client, _cpp_spec(), _cpp_context(Path(path), tmp_path)

        monkeypatch.setattr("agent.tools.tools_lsp._resolve_client", _resolve)
        yield client, mock, pipe, tmp_path
        await client.stop()
        pipe.close()
    finally:
        os.chdir(original)


@pytest.fixture
async def live_client(
    lsp_cpp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[LanguageServerClient, None]:
    original = os.getcwd()
    os.chdir(lsp_cpp_project)
    client = LanguageServerClient(
        workspace_root=lsp_cpp_project,
        startup_timeout=20.0,
        request_timeout=15.0,
        server_name="clangd",
    )
    await client.start()

    async def _resolve(path: str, language: str = "") -> Any:
        return client, _cpp_spec(), _cpp_context(Path(path), lsp_cpp_project)

    monkeypatch.setattr("agent.tools.tools_lsp._resolve_client", _resolve)
    try:
        yield client
    finally:
        await client.stop()
        os.chdir(original)


class TestLspToolsMocked:
    async def test_lsp_definition(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
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
        f = tmp / "a.cpp"
        f.write_text("//\n")

        result = await lsp_definition.ainvoke(
            {"path": str(f), "line": 1, "character": 0}
        )
        payload = json.loads(result)
        assert payload == [{"path": "/x.cpp", "line": 6, "character": 0}]

    async def test_lsp_references(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/references",
            [
                {
                    "uri": "file:///y.cpp",
                    "range": {
                        "start": {"line": 1, "character": 2},
                        "end": {"line": 1, "character": 5},
                    },
                }
            ],
        )
        f = tmp / "a.cpp"
        f.write_text("//\n")

        result = await lsp_references.ainvoke(
            {"path": str(f), "line": 1, "character": 0, "include_declaration": False}
        )
        payload = json.loads(result)
        assert payload[0]["path"] == "/y.cpp"
        sent = mock.requests_by_method["textDocument/references"]
        assert sent[0]["params"]["context"]["includeDeclaration"] is False

    async def test_lsp_document_symbols(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/documentSymbol",
            [
                {
                    "name": "Greeter",
                    "kind": 5,
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 4, "character": 1},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 6},
                        "end": {"line": 0, "character": 13},
                    },
                    "children": [
                        {
                            "name": "greet",
                            "kind": 6,
                            "range": {
                                "start": {"line": 1, "character": 2},
                                "end": {"line": 3, "character": 3},
                            },
                            "selectionRange": {
                                "start": {"line": 1, "character": 6},
                                "end": {"line": 1, "character": 11},
                            },
                        }
                    ],
                }
            ],
        )
        f = tmp / "hello.h"
        f.write_text("class Greeter { void greet(); };\n")

        result = await lsp_document_symbols.ainvoke({"path": str(f)})
        payload = json.loads(result)
        names = {
            (symbol["name"], symbol["kind"], symbol["depth"]) for symbol in payload
        }
        assert ("Greeter", "class", 0) in names
        assert ("greet", "method", 1) in names

    async def test_lsp_workspace_symbols(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "workspace/symbol",
            [
                {
                    "name": "main",
                    "kind": 12,
                    "containerName": "global",
                    "location": {
                        "uri": "file:///main.cpp",
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 8},
                        },
                    },
                }
            ],
        )
        f = tmp / "main.cpp"
        f.write_text("int main() { return 0; }\n")

        result = await lsp_workspace_symbols.ainvoke({"path": str(f), "query": "main"})
        payload = json.loads(result)
        assert payload[0]["name"] == "main"
        assert payload[0]["kind"] == "function"
        assert payload[0]["container_name"] == "global"

    async def test_lsp_workspace_symbols_passes_language_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.getcwd()
        os.chdir(tmp_path)
        try:
            anchor = tmp_path / "anchor.py"
            anchor.write_text("print('hi')\n")
            captured: dict[str, str] = {}

            class _Client:
                def server_capabilities(self) -> dict[str, Any]:
                    return {"workspaceSymbolProvider": True}

                async def sync_document(self, path: str) -> None:
                    captured["sync_path"] = path

                async def workspace_symbol(self, query: str) -> list[dict[str, Any]]:
                    captured["query"] = query
                    return []

            async def _resolve(path: str, language: str = "") -> Any:
                captured["path"] = path
                captured["language"] = language
                return _Client(), _cpp_spec(), _cpp_context(Path(path), tmp_path)

            monkeypatch.setattr("agent.tools.tools_lsp._resolve_client", _resolve)
            result = await lsp_workspace_symbols.ainvoke(
                {"path": str(anchor), "query": "main", "language": "clangd"}
            )
        finally:
            os.chdir(original)

        assert json.loads(result) == []
        assert captured["path"] == str(anchor)
        assert captured["language"] == "clangd"
        assert captured["query"] == "main"
        assert captured["sync_path"] == str(anchor)

    async def test_lsp_rename(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/rename",
            {"changes": {"file:///a.cpp": [{"range": {}, "newText": "bar"}]}},
        )
        f = tmp / "a.cpp"
        f.write_text("int foo;\n")

        result = await lsp_rename.ainvoke(
            {"path": str(f), "line": 1, "character": 5, "new_name": "bar"}
        )
        payload = json.loads(result)
        assert "file:///a.cpp" in payload["changes"]

    async def test_lsp_diagnostics(self, wired_client: Any) -> None:
        client, mock, _pipe, tmp = wired_client
        f = tmp / "a.cpp"
        f.write_text("int x;\n")

        waiter = asyncio.create_task(lsp_diagnostics.ainvoke({"path": str(f)}))
        await asyncio.sleep(0)
        await mock.send_notification(
            "textDocument/publishDiagnostics",
            {
                "uri": f.resolve().as_uri(),
                "diagnostics": [
                    {
                        "range": {"start": {"line": 0, "character": 4}, "end": {}},
                        "severity": 1,
                        "code": "E001",
                        "source": "clangd",
                        "message": "broken",
                    }
                ],
            },
        )

        payload = json.loads(await waiter)
        assert payload == [
            {
                "path": str(f.resolve()),
                "line": 1,
                "character": 4,
                "severity": "error",
                "code": "E001",
                "source": "clangd",
                "message": "broken",
            }
        ]
        assert client.get_diagnostics(str(f))


class TestToolPolicies:
    async def test_path_outside_project_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.cpp"
        target.write_text("int x;\n")

        monkeypatch.setattr(
            "agent.tools.tools_lsp._resolve_client",
            lambda path, language="": asyncio.sleep(
                0,
                result=(
                    _DummyClient(),
                    _cpp_spec(),
                    _cpp_context(Path(path), tmp_path),
                ),
            ),
        )
        result = await lsp_document_symbols.ainvoke({"path": str(target)})
        assert result.startswith("Error:")
        assert "not inside the project folder" in result

    async def test_unsupported_extension_returns_clear_error(
        self, tmp_path: Path
    ) -> None:
        original = os.getcwd()
        os.chdir(tmp_path)
        try:
            target = tmp_path / "notes.txt"
            target.write_text("hello\n")
            result = await lsp_document_symbols.ainvoke({"path": str(target)})
        finally:
            os.chdir(original)
        assert result.startswith("Error:")
        assert "unsupported LSP file extension" in result

    async def test_unsupported_capability_returns_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.getcwd()
        os.chdir(tmp_path)
        try:
            target = tmp_path / "main.py"
            target.write_text("print('hi')\n")

            async def _resolve(path: str, language: str = "") -> Any:
                return (
                    _UnsupportedClient(),
                    ServerSpec(
                        server_id="pyright",
                        language_id="python",
                        file_extensions=frozenset({".py"}),
                        root_markers=("pyproject.toml", ".git"),
                    ),
                    WorkspaceContext(
                        path=Path(path),
                        language="python",
                        workspace_root=tmp_path,
                    ),
                )

            monkeypatch.setattr("agent.tools.tools_lsp._resolve_client", _resolve)
            result = await lsp_workspace_symbols.ainvoke(
                {"path": str(target), "query": "demo"}
            )
        finally:
            os.chdir(original)
        assert result == "Error: workspace symbols are not supported by pyright"

    def test_only_generic_lsp_tools_registered(self) -> None:
        names = {tool.name for tool in get_tools()}
        expected = {
            "lsp_definition",
            "lsp_references",
            "lsp_document_symbols",
            "lsp_workspace_symbols",
            "lsp_rename",
            "lsp_diagnostics",
        }
        assert expected <= names
        assert not (
            {"clangd_definition", "clangd_workspace_symbols", "clangd_rename"} & names
        )


CLANGD_AVAILABLE = shutil.which("clangd") is not None
PYRIGHT_AVAILABLE = shutil.which("pyright-langserver") is not None


@pytest.mark.skipif(not CLANGD_AVAILABLE, reason="clangd binary not installed")
@pytest.mark.integration
class TestLspToolsIntegration:
    async def test_workspace_symbols_finds_function(
        self, live_client: LanguageServerClient, lsp_cpp_project: Path
    ) -> None:
        main_cpp = lsp_cpp_project / "main.cpp"
        payload: list[dict[str, Any]] = []
        for _ in range(40):
            result = await lsp_workspace_symbols.ainvoke(
                {"path": str(main_cpp), "query": "main"}
            )
            payload = json.loads(result)
            if any(symbol.get("name") == "main" for symbol in payload):
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail(f"clangd never indexed 'main': {payload!r}")

        assert "main" in {symbol["name"] for symbol in payload}

    async def test_definition_finds_function_definition(
        self, live_client: LanguageServerClient, lsp_cpp_project: Path
    ) -> None:
        main_cpp = lsp_cpp_project / "main.cpp"
        payload: list[dict[str, Any]] = []
        for _ in range(40):
            result = await lsp_definition.ainvoke(
                {"path": str(main_cpp), "line": 5, "character": 25}
            )
            payload = json.loads(result)
            if payload and "hello.cpp" in payload[0].get("path", ""):
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail(f"definition never resolved: {payload!r}")

        assert payload[0]["line"] >= 1

    async def test_diagnostics_returns_errors(
        self, live_client: LanguageServerClient, lsp_cpp_project: Path
    ) -> None:
        broken_cpp = lsp_cpp_project / "broken.cpp"
        broken_cpp.write_text("int main( { return 0; }\n")

        payload: list[dict[str, Any]] = []
        for _ in range(20):
            result = await lsp_diagnostics.ainvoke({"path": str(broken_cpp)})
            payload = json.loads(result)
            if payload:
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail("clangd did not publish diagnostics for broken.cpp")

        assert any(item["severity"] == "error" for item in payload)


@pytest.mark.skipif(
    not PYRIGHT_AVAILABLE, reason="pyright-langserver binary not installed"
)
@pytest.mark.integration
class TestLspToolsPyrightIntegration:
    @pytest.fixture(autouse=True)
    async def _reset_manager(self) -> AsyncGenerator[None, None]:
        from agent.lsp import reset_client_manager

        await reset_client_manager()
        yield
        await reset_client_manager()

    @pytest.fixture(autouse=True)
    def _chdir_project(self, lsp_python_project: Path) -> AsyncGenerator[None, None]:
        original = os.getcwd()
        os.chdir(lsp_python_project)
        try:
            yield
        finally:
            os.chdir(original)

    async def test_definition_resolves_python_symbol(
        self, lsp_python_project: Path
    ) -> None:
        app_py = lsp_python_project / "app.py"
        payload: list[dict[str, Any]] = []
        for _ in range(20):
            result = await lsp_definition.ainvoke(
                {"path": str(app_py), "line": 5, "character": 15}
            )
            payload = json.loads(result)
            if payload and payload[0]["path"].endswith("helpers.py"):
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail(f"pyright definition never resolved: {payload!r}")

        assert payload[0]["path"].endswith("helpers.py")

    async def test_references_include_definition_and_usage(
        self, lsp_python_project: Path
    ) -> None:
        app_py = lsp_python_project / "app.py"
        result = await lsp_references.ainvoke(
            {"path": str(app_py), "line": 5, "character": 15}
        )
        payload = json.loads(result)
        paths = {Path(item["path"]).name for item in payload}
        assert {"helpers.py", "app.py"} <= paths

    async def test_document_symbols_return_python_structure(
        self, lsp_python_project: Path
    ) -> None:
        helpers_py = lsp_python_project / "helpers.py"
        result = await lsp_document_symbols.ainvoke({"path": str(helpers_py)})
        payload = json.loads(result)
        names = {item["name"] for item in payload}
        assert {"Greeter", "__init__", "greet", "build_message"} <= names

    async def test_workspace_symbols_find_python_function(
        self, lsp_python_project: Path
    ) -> None:
        app_py = lsp_python_project / "app.py"
        payload: list[dict[str, Any]] = []
        for _ in range(20):
            result = await lsp_workspace_symbols.ainvoke(
                {"path": str(app_py), "query": "build_message"}
            )
            payload = json.loads(result)
            if any(item["name"] == "build_message" for item in payload):
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail(f"pyright workspace symbols never found target: {payload!r}")

        assert any(item["path"].endswith("helpers.py") for item in payload)

    async def test_rename_returns_workspace_edit(
        self, lsp_python_project: Path
    ) -> None:
        helpers_py = lsp_python_project / "helpers.py"
        result = await lsp_rename.ainvoke(
            {
                "path": str(helpers_py),
                "line": 9,
                "character": 5,
                "new_name": "build_label",
            }
        )
        payload = json.loads(result)
        edits = payload.get("changes") or payload.get("documentChanges") or []
        assert edits

    async def test_diagnostics_return_pyright_error(
        self, lsp_python_project: Path
    ) -> None:
        broken_py = lsp_python_project / "broken.py"
        payload: list[dict[str, Any]] = []
        for _ in range(20):
            result = await lsp_diagnostics.ainvoke({"path": str(broken_py)})
            payload = json.loads(result)
            if payload:
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail("pyright did not publish diagnostics for broken.py")

        assert any(item["severity"] == "error" for item in payload)
        assert any("pyright" in item["source"].lower() for item in payload)
