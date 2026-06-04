"""Tests for the :mod:`agent.tools.tools_clangd` tool wrappers.

Two test classes are provided:

- ``TestClangdToolsMocked`` — exercises each tool against an in-process
  mock LSP server (no ``clangd`` binary required).  Confirms that the
  tool layer sends the right LSP request, applies the path-safety
  policy, and surfaces ``"Error: ..."`` strings on failure.
- ``TestClangdToolsIntegration`` — gated on the presence of ``clangd``
  on ``$PATH``.  Runs end-to-end against the small fixture project at
  ``tests/fixtures/lsp_cpp/``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from agent.lsp import ClangdClient
from agent.tools import get_tools
from agent.tools.tools_clangd import (
    clangd_call_hierarchy,
    clangd_completion,
    clangd_definition,
    clangd_document_symbols,
    clangd_references,
    clangd_rename,
    clangd_type_hierarchy,
    clangd_workspace_symbols,
)

# Re-use the mock server + wiring from the client tests.
from tests.test_lsp_client import (  # noqa: PLC0415
    MockLspServer,
    _BidiClientPipe,
    _start_wired,
    _wire_client_with_mock,
)

# ---------------------------------------------------------------------------
# Helpers + fixtures
# ---------------------------------------------------------------------------


async def _async_return(value: Any) -> Any:
    return value


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """``os.getcwd()`` is the project root for the path-safety policy.

    The tools' :func:`_is_subpath` checks paths against ``os.getcwd()``;
    we change into ``tmp_path`` so the fixture files we create are
    inside the "project".
    """
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        return tmp_path
    finally:
        # We don't restore here — pytest fixture finalisation order
        # can call this late.  The test's own teardown will chdir
        # back to a safe location if needed.
        os.chdir(original)  # noqa: F841 - placeholder for clarity


@pytest.fixture
async def wired_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ClangdClient, MockLspServer, _BidiClientPipe, Path]:
    """Yield a started ``ClangdClient`` wired to a ``MockLspServer``.

    Also patches ``agent.tools.tools_clangd.get_default_client`` so the
    tools route through the wired client.  ``monkeypatch`` undoes the
    patch automatically when the test ends.
    """
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        client, mock, pipe = _wire_client_with_mock(tmp_path)
        mock.set_canned("initialize", {"capabilities": {}})
        await _start_wired(client, mock, pipe)

        monkeypatch.setattr(
            "agent.tools.tools_clangd.get_default_client",
            lambda: _async_return(client),
        )
        yield client, mock, pipe, tmp_path
        await client.stop()
        pipe.close()
    finally:
        os.chdir(original)


class _DummyClient:
    """Stand-in client for policy tests — should never be called."""

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - defensive
        raise AssertionError(
            f"_DummyClient.{name} was called but the path policy should "
            "have rejected the request first."
        )


# ---------------------------------------------------------------------------
# Per-tool happy path
# ---------------------------------------------------------------------------


class TestClangdToolsMocked:
    """All tools work end-to-end against the in-process mock server."""

    async def test_clangd_completion(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/completion",
            {
                "isIncomplete": False,
                "items": [{"label": "foo", "kind": 12, "detail": "void foo()"}],
            },
        )
        f = tmp / "a.cpp"
        f.write_text("int main() {}\n")

        result = await clangd_completion.ainvoke(
            {"path": str(f), "line": 1, "character": 0}
        )
        payload = json.loads(result)
        assert payload[0]["label"] == "foo"
        assert payload[0]["kind"] == "function"

        sent = mock.requests_by_method["textDocument/completion"]
        assert sent[0]["params"]["textDocument"]["uri"].endswith("a.cpp")
        assert sent[0]["params"]["position"] == {"line": 0, "character": 0}

    async def test_clangd_definition(self, wired_client: Any) -> None:
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

        result = await clangd_definition.ainvoke(
            {"path": str(f), "line": 1, "character": 0}
        )
        payload = json.loads(result)
        assert len(payload) == 1
        assert payload[0]["line"] == 6  # 0-based 5 → 1-based 6
        assert payload[0]["character"] == 0
        assert payload[0]["path"] == "/x.cpp"

    async def test_clangd_references(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/references",
            [
                {
                    "uri": "file:///y.cpp",
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 3},
                    },
                }
            ],
        )
        f = tmp / "a.cpp"
        f.write_text("//\n")

        result = await clangd_references.ainvoke(
            {"path": str(f), "line": 1, "character": 0, "include_declaration": False}
        )
        payload = json.loads(result)
        assert payload[0]["path"] == "/y.cpp"
        sent = mock.requests_by_method["textDocument/references"]
        assert sent[0]["params"]["context"]["includeDeclaration"] is False

    async def test_clangd_document_symbols(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/documentSymbol",
            [
                {
                    "name": "Greeter",
                    "kind": 5,  # class
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
                            "kind": 6,  # method
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

        result = await clangd_document_symbols.ainvoke({"path": str(f)})
        payload = json.loads(result)
        names = [(s["name"], s["kind"], s["depth"]) for s in payload]
        assert ("Greeter", "class", 0) in names
        assert ("greet", "method", 1) in names

    async def test_clangd_workspace_symbols(self, wired_client: Any) -> None:
        _client, mock, _pipe, _tmp = wired_client
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

        result = await clangd_workspace_symbols.ainvoke({"query": "main"})
        payload = json.loads(result)
        assert payload[0]["name"] == "main"
        assert payload[0]["kind"] == "function"
        assert payload[0]["container_name"] == "global"
        assert payload[0]["line"] == 1

    async def test_clangd_rename(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/rename",
            {
                "changes": {
                    "file:///a.cpp": [
                        {"range": {}, "newText": "bar"}
                    ]
                }
            },
        )
        f = tmp / "a.cpp"
        f.write_text("int foo;\n")

        result = await clangd_rename.ainvoke(
            {"path": str(f), "line": 1, "character": 5, "new_name": "bar"}
        )
        payload = json.loads(result)
        assert "changes" in payload
        # ``clangd_rename`` returns the raw WorkspaceEdit (URIs intact)
        # so the LLM can apply the edits verbatim with textDocument
        # version checks.
        assert "file:///a.cpp" in payload["changes"]

    async def test_clangd_type_hierarchy_subtypes(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/prepareTypeHierarchy",
            [
                {
                    "name": "Base",
                    "kind": 5,
                    "uri": "file:///a.h",
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 11},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 6},
                        "end": {"line": 0, "character": 10},
                    },
                }
            ],
        )
        mock.set_canned(
            "typeHierarchy/subtypes",
            [
                {
                    "name": "Derived",
                    "kind": 5,
                    "uri": "file:///b.h",
                    "range": {},
                }
            ],
        )
        mock.set_canned("typeHierarchy/supertypes", [])
        f = tmp / "a.h"
        f.write_text("class Base {};\n")

        result = await clangd_type_hierarchy.ainvoke(
            {"path": str(f), "line": 1, "character": 6, "direction": "subtypes"}
        )
        payload = json.loads(result)
        assert payload["item"]["name"] == "Base"
        assert payload["subtypes"][0]["name"] == "Derived"
        assert payload["supertypes"] == []

    async def test_clangd_type_hierarchy_both(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/prepareTypeHierarchy",
            [
                {
                    "name": "Mid",
                    "kind": 5,
                    "uri": "file:///m.h",
                    "range": {"start": {"line": 0, "character": 0}, "end": {}},
                    "selectionRange": {"start": {"line": 0, "character": 6}, "end": {}},
                }
            ],
        )
        mock.set_canned(
            "typeHierarchy/supertypes",
            [{"name": "Base", "kind": 5, "uri": "file:///a.h", "range": {}}],
        )
        mock.set_canned(
            "typeHierarchy/subtypes",
            [{"name": "Leaf", "kind": 5, "uri": "file:///l.h", "range": {}}],
        )
        f = tmp / "m.h"
        f.write_text("class Mid : public Base {};\n")

        result = await clangd_type_hierarchy.ainvoke(
            {"path": str(f), "line": 1, "character": 6, "direction": "both"}
        )
        payload = json.loads(result)
        assert payload["supertypes"][0]["name"] == "Base"
        assert payload["subtypes"][0]["name"] == "Leaf"

    async def test_clangd_type_hierarchy_invalid_direction(
        self, wired_client: Any
    ) -> None:
        _client, _mock, _pipe, tmp = wired_client
        f = tmp / "a.h"
        f.write_text("class Base {};\n")
        result = await clangd_type_hierarchy.ainvoke(
            {"path": str(f), "line": 1, "character": 6, "direction": "bogus"}
        )
        assert result.startswith("Error:")
        assert "direction" in result

    async def test_clangd_call_hierarchy_outgoing(self, wired_client: Any) -> None:
        _client, mock, _pipe, tmp = wired_client
        mock.set_canned(
            "textDocument/prepareCallHierarchy",
            [
                {
                    "name": "main",
                    "kind": 12,
                    "uri": "file:///main.cpp",
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 8},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 8},
                    },
                }
            ],
        )
        mock.set_canned(
            "callHierarchy/outgoingCalls",
            [
                {
                    "to": {
                        "name": "helper",
                        "kind": 12,
                        "uri": "file:///a.cpp",
                        "range": {
                            "start": {"line": 1, "character": 4},
                            "end": {"line": 1, "character": 10},
                        },
                        "selectionRange": {
                            "start": {"line": 1, "character": 4},
                            "end": {"line": 1, "character": 10},
                        },
                    },
                    "fromRanges": [
                        {
                            "start": {"line": 0, "character": 9},
                            "end": {"line": 0, "character": 17},
                        }
                    ],
                }
            ],
        )
        mock.set_canned("callHierarchy/incomingCalls", [])
        f = tmp / "main.cpp"
        f.write_text("int main(){helper();}\n")

        result = await clangd_call_hierarchy.ainvoke(
            {"path": str(f), "line": 1, "character": 9, "direction": "outgoing"}
        )
        payload = json.loads(result)
        assert payload["item"]["name"] == "main"
        assert payload["outgoing"][0]["to"]["name"] == "helper"
        assert payload["incoming"] == []

    async def test_clangd_call_hierarchy_invalid_direction(
        self, wired_client: Any
    ) -> None:
        _client, _mock, _pipe, tmp = wired_client
        f = tmp / "main.cpp"
        f.write_text("int main(){}\n")
        result = await clangd_call_hierarchy.ainvoke(
            {"path": str(f), "line": 1, "character": 0, "direction": "wrong"}
        )
        assert result.startswith("Error:")
        assert "direction" in result


# ---------------------------------------------------------------------------
# Policy + registration
# ---------------------------------------------------------------------------


class TestToolPolicies:
    """The path policy and tool registration are enforced at the tool layer."""

    async def test_path_outside_project_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.cpp"
        target.write_text("int x;\n")

        # The path policy fires before the client is touched, so the
        # _DummyClient is unreachable.  Wire it in just in case.
        monkeypatch.setattr(
            "agent.tools.tools_clangd.get_default_client",
            lambda: _async_return(_DummyClient()),
        )
        result = await clangd_document_symbols.ainvoke({"path": str(target)})
        assert result.startswith("Error:")
        assert "not inside the project folder" in result

    async def test_clangd_missing_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.getcwd()
        os.chdir(tmp_path)
        try:
            f = tmp_path / "a.cpp"
            f.write_text("int x;\n")

            async def _raise() -> None:
                raise FileNotFoundError(
                    "clangd not found on PATH (looked for 'clangd')."
                )

            monkeypatch.setattr(
                "agent.tools.tools_clangd.get_default_client", _raise
            )
            result = await clangd_document_symbols.ainvoke({"path": str(f)})
        finally:
            os.chdir(original)
        assert result.startswith("Error:")
        assert "clangd not found" in result

    def test_all_clangd_tools_registered_in_get_tools(self) -> None:
        names = {t.name for t in get_tools()}
        expected = {
            "clangd_completion",
            "clangd_definition",
            "clangd_references",
            "clangd_document_symbols",
            "clangd_workspace_symbols",
            "clangd_rename",
            "clangd_type_hierarchy",
            "clangd_call_hierarchy",
        }
        missing = expected - names
        assert not missing, f"Missing tool registrations: {missing}"


# ---------------------------------------------------------------------------
# Integration tests — require a real clangd
# ---------------------------------------------------------------------------


CLANGD_AVAILABLE = shutil.which("clangd") is not None


@pytest.mark.skipif(not CLANGD_AVAILABLE, reason="clangd binary not installed")
@pytest.mark.integration
class TestClangdToolsIntegration:
    """End-to-end tests against the system clangd and a small C++ fixture."""

    @pytest.fixture(autouse=True)
    async def _reset_singleton(self) -> Any:
        from agent.lsp import reset_default_client

        await reset_default_client()
        yield
        await reset_default_client()

    async def test_workspace_symbols_finds_function(
        self, lsp_cpp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = ClangdClient(
            workspace_root=lsp_cpp_project,
            startup_timeout=20.0,
            request_timeout=15.0,
        )
        monkeypatch.setattr(
            "agent.tools.tools_clangd.get_default_client",
            lambda: _async_return(client),
        )

        original = os.getcwd()
        os.chdir(lsp_cpp_project)
        try:
            await client.start()
            try:
                # Open the file so clangd indexes it. The tool layer
                # only triggers did_open for cursor-relative queries;
                # workspace_symbol relies on the workspace index, but
                # clangd won't index a file that hasn't been opened.
                main_cpp = lsp_cpp_project / "main.cpp"
                await client.did_open(
                    str(main_cpp), main_cpp.read_text(encoding="utf-8")
                )

                payload: list[dict[str, Any]] = []
                for _ in range(40):
                    result = await clangd_workspace_symbols.ainvoke({"query": "main"})
                    payload = json.loads(result)
                    if any(s.get("name") == "main" for s in payload):
                        break
                    await asyncio.sleep(0.5)
                else:
                    pytest.fail(f"clangd never indexed 'main': {payload!r}")

                names = {s["name"] for s in payload}
                assert "main" in names
            finally:
                await client.stop()
        finally:
            os.chdir(original)

    async def test_definition_finds_function_definition(
        self, lsp_cpp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = ClangdClient(
            workspace_root=lsp_cpp_project,
            startup_timeout=20.0,
            request_timeout=15.0,
        )
        monkeypatch.setattr(
            "agent.tools.tools_clangd.get_default_client",
            lambda: _async_return(client),
        )

        original = os.getcwd()
        os.chdir(lsp_cpp_project)
        try:
            main_cpp = lsp_cpp_project / "main.cpp"
            await client.start()
            try:
                # Position the cursor inside `greet` on the call site.
                # The file is:
                #   line 1: #include "hello.h"
                #   line 2: (empty)
                #   line 3: int main() {
                #   line 4:     Greeter g("world");
                #   line 5:     std::string msg = g.greet();
                #   ...
                # 1-based line=5, 0-based character inside `greet`
                # (position 24 of "    std::string msg = g.greet();")
                # should resolve to Greeter::greet() in hello.cpp.
                payload: list[dict[str, Any]] = []
                for _ in range(40):
                    result = await clangd_definition.ainvoke(
                        {
                            "path": str(main_cpp),
                            "line": 5,
                            "character": 25,
                        }
                    )
                    payload = json.loads(result)
                    if payload and "hello.cpp" in payload[0].get("path", ""):
                        break
                    await asyncio.sleep(0.5)
                else:
                    pytest.fail(f"definition never resolved: {payload!r}")
                assert payload[0]["line"] >= 1
            finally:
                await client.stop()
        finally:
            os.chdir(original)
