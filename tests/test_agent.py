"""Tests for the agent package.

These tests focus on the parts that can run *without* a real LLM:
- Tool correctness
- Config logic
- Graph structure (nodes, edges)
- State reducers

Tests that require a live API key are marked ``@pytest.mark.integration``
and skipped by default.  Run them with:

    uv run pytest -m integration
"""

from __future__ import annotations

import ast
import json
import os
import types
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.config import Settings, get_llm, get_settings
from agent.tools import *

# ---------------------------------------------------------------------------
# Tool tests — no LLM required
# ---------------------------------------------------------------------------


class TestCalculateTool:
    def test_basic_arithmetic(self) -> None:
        assert calculate.invoke("2 + 2") == "4"

    def test_operator_precedence(self) -> None:
        assert calculate.invoke("2 + 3 * 4") == "14"

    def test_parentheses(self) -> None:
        assert calculate.invoke("(2 + 3) * 4") == "20"

    def test_exponentiation(self) -> None:
        assert calculate.invoke("2 ** 10") == "1024"

    def test_float_result(self) -> None:
        result = calculate.invoke("7 / 2")
        assert result == "3.5"

    def test_floor_division(self) -> None:
        assert calculate.invoke("7 // 2") == "3"

    def test_modulo(self) -> None:
        assert calculate.invoke("10 % 3") == "1"

    def test_unary_negation(self) -> None:
        assert calculate.invoke("-5 + 10") == "5"

    def test_invalid_expression_returns_error(self) -> None:
        result = calculate.invoke("import os")
        assert result.startswith("Error:")

    def test_division_by_zero(self) -> None:
        result = calculate.invoke("1 / 0")
        assert result.startswith("Error:")


class TestGetCurrentDatetimeTool:
    def test_returns_iso_string(self) -> None:
        result = get_current_datetime.invoke({})
        # Should be parseable as an ISO-8601 datetime with timezone info.
        from datetime import datetime

        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_returns_utc(self) -> None:
        result = get_current_datetime.invoke({})
        assert "+00:00" in result


class TestReadFileTool:
    def test_returns_file_content(self) -> None:
        result = read_file.invoke("tests/testfile.md")
        assert result == "Hello World 0815"

    def test_path_outside_project(self) -> None:
        result = read_file.invoke("../testfile.md")
        assert result.startswith("Error:")


class TestWriteReadFileTool:
    def test_roundtrip(self) -> None:
        test_string: str = "Hello world 1234"
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})
        result = read_file.invoke(file_path)
        assert result == test_string

    def test_roundtrip_with_offset(self) -> None:
        test_string: str = """All that glitters is not gold.
To be, or not to be, that is the question.
A rose by any other name would smell as sweet.
        """
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})
        result = read_file.invoke({"path": file_path, "offset": 1, "lines": 1})
        assert result == "To be, or not to be, that is the question.\n"


class TestReplaceInFileTool:
    def test_roundtrip(self) -> None:
        test_string: str = "Hello world 1234"
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})

        result_replace = replace_in_file.invoke(
            {"path": file_path, "old_string": "world", "new_string": "sun"}
        )
        assert result_replace == "Replaced 1 times"

        result = read_file.invoke(file_path)
        assert result == "Hello sun 1234"

    def test_roundtrip_multiple(self) -> None:
        test_string: str = """All that glitters is not gold.
To be, or not to be, that is the question.
A rose by any other name would smell as sweet.
        """
        file_path: str = "tests/readwrite.md"
        write_file.invoke({"path": file_path, "content": test_string})

        result_replace = replace_in_file.invoke(
            {"path": file_path, "old_string": "be", "new_string": "see"}
        )
        assert result_replace.startswith("Error:")

        result_replace = replace_in_file.invoke(
            {"path": file_path, "old_string": "123456", "new_string": "654321"}
        )
        assert result_replace.startswith("Error:")

        result = read_file.invoke({"path": file_path})
        assert result == test_string

        result_replace = replace_in_file.invoke(
            {
                "path": file_path,
                "old_string": "be",
                "new_string": "see",
                "replace_all": True,
            }
        )
        assert result_replace == "Replaced 2 times"

        result = read_file.invoke({"path": file_path})
        assert (
            result
            == """All that glitters is not gold.
To see, or not to see, that is the question.
A rose by any other name would smell as sweet.
        """
        )


class TestCreateDirectoryTool:
    def test_existing_parent_folder(self) -> None:
        new_folder = "tests/testfolder42"
        result = create_directory.invoke({"path": new_folder})
        assert result == "Success"
        assert os.path.isdir(new_folder)
        os.rmdir(new_folder)

    def test_recursive_creation(self) -> None:
        new_folder = "notexist/testfolder42"
        result = create_directory.invoke({"path": new_folder})
        assert result == "Success"
        assert os.path.isdir(new_folder)
        os.removedirs(new_folder)


class TestGrepTool:
    def test_grep(self) -> None:
        result = grep.invoke(
            {
                "pattern": "def",
                "directory": "tests/testfolder",
                "file_pattern": ["*.py"],
                "case_sensitive": False,
                "skip_dirs": {".venv"},
            }
        )

        assert result == "['tests/testfolder/folder1/test.py:2:def test():']"

    def test_grep_multi_file_extensions(self) -> None:
        result = grep.invoke(
            {
                "pattern": "def",
                "directory": "tests/testfolder",
                "file_pattern": ["*.py", "*.cpp"],
                "case_sensitive": False,
                "skip_dirs": {".venv"},
            }
        )

        assert (
            result
            == "['tests/testfolder/folder1/test.py:2:def test():', 'tests/testfolder/folder1/test.cpp: lines 2, 3 (2 matches)']"
        )

    def test_grep_too_many_lines(self) -> None:
        result = grep.invoke(
            {
                "pattern": "search_pattern",
                "directory": "tests/testfiles/",
                "file_pattern": ["long_file.txt"],
                "case_sensitive": False,
                "skip_dirs": {".venv"},
            }
        )

        parsed = ast.literal_eval(result)

        assert parsed["truncated"] is True
        assert parsed["total_matches"] == 1050
        assert parsed["shown"] == 1
        assert len(parsed["results"]) == 1
        assert parsed["results"][0].startswith(
            "tests/testfiles/long_file.txt:1-1050:search_pattern"
        )


class TestListDirectoryTool:
    def test_list_directory(self) -> None:
        dir_path: str = "tests/testfolder"
        result = list_directory.invoke({"path": dir_path})

        assert (
            result
            == "[('.venv', 'dir'), ('file1', 'file'), ('folder1', 'dir'), ('file2', 'file')]"
        )


class TestGetTools:
    def test_always_includes_builtins(self) -> None:
        tools = get_tools()
        names = {t.name for t in tools}
        assert "calculate" in names
        assert "get_current_datetime" in names

    def test_web_search_absent_without_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tools = get_tools()
        names = {t.name for t in tools}
        assert "tavily_search_results_json" not in names


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSettings:
    def test_default_provider_is_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MODEL_NAME", "")
        s = get_settings()
        assert s.llm_provider == "openai"
        assert s.resolved_model == "gpt-5.4-nano"

    def test_anthropic_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("MODEL_NAME", "")
        s = get_settings()
        assert s.llm_provider == "anthropic"
        assert "claude" in s.resolved_model

    def test_ollama_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("MODEL_NAME", "")
        s = get_settings()
        assert s.llm_provider == "ollama"
        assert s.resolved_model == "qwen2.5-coder:14b"

    def test_ollama_base_url_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        s = get_settings()
        assert s.ollama_base_url == "http://localhost:11434"

    def test_explicit_model_name_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MODEL_NAME", "gpt-5.4-mini")
        s = get_settings()
        assert s.resolved_model == "gpt-5.4-mini"

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "cohere")
        with pytest.raises(
            ValueError,
            match="Choose 'openai', 'anthropic', or 'ollama'",
        ):
            get_settings()


class TestGetLlmFactory:
    def test_openai_provider_branch(self) -> None:
        mock_chat_openai = MagicMock(name="ChatOpenAI")
        fake_module = types.SimpleNamespace(ChatOpenAI=mock_chat_openai)

        with patch.dict("sys.modules", {"langchain_openai": fake_module}):
            settings = Settings(
                llm_provider="openai",
                model_name="gpt-5.4-mini",
                temperature=0.3,
            )
            get_llm(settings)

        mock_chat_openai.assert_called_once_with(
            model="gpt-5.4-mini",
            temperature=0.3,
        )

    def test_anthropic_provider_branch(self) -> None:
        mock_chat_anthropic = MagicMock(name="ChatAnthropic")
        fake_module = types.SimpleNamespace(ChatAnthropic=mock_chat_anthropic)

        with patch.dict("sys.modules", {"langchain_anthropic": fake_module}):
            settings = Settings(
                llm_provider="anthropic",
                model_name="claude-haiku-4-5-20251001",
                temperature=0.1,
            )
            get_llm(settings)

        mock_chat_anthropic.assert_called_once_with(
            model="claude-haiku-4-5-20251001",
            temperature=0.1,
        )

    def test_ollama_provider_branch(self) -> None:
        mock_chat_ollama = MagicMock(name="ChatOllama")
        fake_module = types.SimpleNamespace(ChatOllama=mock_chat_ollama)

        with patch.dict("sys.modules", {"langchain_ollama": fake_module}):
            settings = Settings(
                llm_provider="ollama",
                model_name="qwen2.5-coder:14b",
                temperature=0.2,
                ollama_base_url="http://127.0.0.1:11434",
            )
            get_llm(settings)

        mock_chat_ollama.assert_called_once_with(
            model="qwen2.5-coder:14b",
            temperature=0.2,
            base_url="http://127.0.0.1:11434",
        )


# ---------------------------------------------------------------------------
# Graph structure tests — mock the LLM to avoid real API calls
# ---------------------------------------------------------------------------


class TestGraphStructure:
    def test_graph_compiles(self) -> None:
        """Graph should compile without errors (no API calls made)."""
        from agent.graph import build_graph

        g = build_graph()
        assert g is not None

    def test_graph_nodes(self) -> None:
        from agent.graph import build_graph

        g = build_graph()
        assert "agent" in g.nodes
        assert "tools" in g.nodes

    def test_graph_invoke_with_mock_llm(self) -> None:
        """Verify the full graph loop with a mocked LLM that returns immediately."""
        from agent.graph import build_graph
        from agent.nodes import _get_llm_with_tools

        # Create an AI message with NO tool calls → graph should go to END.
        mock_response = AIMessage(content="The answer is 42.")

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            # Clear the cache so the patch takes effect.
            _get_llm_with_tools.cache_clear()
            g = build_graph()
            result = g.invoke(
                {"messages": [HumanMessage(content="What is 6 * 7?")]},
                config={"configurable": {"thread_id": "test"}},
            )

        messages = result["messages"]
        # The last message should be the AI response.
        assert isinstance(messages[-1], AIMessage)
        assert messages[-1].content == "The answer is 42."

    def test_system_prompt_prepended_once(self) -> None:
        """SystemMessage should be injected before the first HumanMessage."""
        from agent.nodes import call_model

        mock_response = AIMessage(content="Hello!")
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with patch("agent.nodes._get_llm_with_tools", return_value=mock_llm):
            from agent.nodes import _get_llm_with_tools

            _get_llm_with_tools.cache_clear()
            state: AgentState = {"messages": [HumanMessage(content="Hi")]}
            call_model(state)

        call_args = mock_llm.invoke.call_args[0][0]
        assert isinstance(call_args[0], SystemMessage)


class TestCBashTool:
    def test_existing_parent_folder(self) -> None:
        command = "uname && ls"
        result = bash.invoke({"command": command})

        assert result.startswith("exit_code:")


# ---------------------------------------------------------------------------
# Tree-sitter tool tests — no LLM required
# ---------------------------------------------------------------------------


class TestTreeSitterTools:
    # -- treesitter_parse --

    def test_parse_python_file(self) -> None:
        """Parsing a real project file should return a JSON tree with a module root."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke({"path": "src/agent/tools/tools.py"})
        assert not result.startswith("Error:")
        # The output may be truncated for large files; check the opening JSON fragment.
        assert result.lstrip().startswith("{")
        assert '"type": "module"' in result
        assert '"start"' in result
        assert '"end"' in result

    def test_parse_inline_python_code(self) -> None:
        """Parsing an inline Python snippet should produce a function_definition
        node.
        """
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke(
            {"code": "def foo(): pass", "language": "python"}
        )
        assert not result.startswith("Error:")
        assert "function_definition" in result

    def test_parse_unsupported_language_returns_error(self) -> None:
        """An unknown language name must return an Error string."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke({"code": "test", "language": "cobol"})
        assert result.startswith("Error:")
        assert "cobol" in result

    def test_parse_path_outside_project_returns_error(self) -> None:
        """Paths outside the project root must be rejected."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke({"path": "/etc/passwd"})
        assert result.startswith("Error:")

    def test_parse_max_depth_respected(self) -> None:
        """Depth-0 parse should render the root as a leaf with a text field."""
        from agent.tools.tools_treesitter import treesitter_parse

        result = treesitter_parse.invoke(
            {"code": "x = 1", "language": "python", "max_depth": 0}
        )
        assert not result.startswith("Error:")
        data = json.loads(result)
        # At depth 0 no children should be expanded.
        assert "children" not in data
        assert "text" in data

    # -- treesitter_query --

    def test_query_captures_function_names(self) -> None:
        """A function-name query should return both defined function names."""
        from agent.tools.tools_treesitter import treesitter_query

        result = treesitter_query.invoke(
            {
                "query_pattern": "(function_definition name: (identifier) @fn_name)",
                "code": "def foo(): pass\ndef bar(): pass",
                "language": "python",
            }
        )
        assert not result.startswith("Error:")
        assert "foo" in result
        assert "bar" in result

    def test_query_on_file(self) -> None:
        """A query against a real file should return at least one match."""
        from agent.tools.tools_treesitter import treesitter_query

        result = treesitter_query.invoke(
            {
                "query_pattern": "(function_definition name: (identifier) @fn_name)",
                "path": "src/agent/tools/general.py",
            }
        )
        assert not result.startswith("Error:")
        assert "calculate" in result

    def test_query_invalid_pattern_returns_error(self) -> None:
        """A malformed query pattern must return an Error string."""
        from agent.tools.tools_treesitter import treesitter_query

        result = treesitter_query.invoke(
            {
                "query_pattern": "(((not_valid_syntax",
                "code": "def foo(): pass",
                "language": "python",
            }
        )
        assert result.startswith("Error:")

    # -- treesitter_get_symbols --

    def test_get_symbols_python_file(self) -> None:
        """Symbol extraction on a project Python file must include known functions."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        result = treesitter_get_symbols.invoke({"path": "src/agent/tools/general.py"})
        assert not result.startswith("Error:")
        assert "calculate" in result
        assert "get_current_datetime" in result

    def test_get_symbols_inline_rust(self) -> None:
        """Symbol extraction on inline Rust code must return a function entry."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        result = treesitter_get_symbols.invoke(
            {"code": 'fn main() { println!("hello"); }', "language": "rust"}
        )
        assert not result.startswith("Error:")
        assert "main" in result

    def test_get_symbols_no_query_for_language_returns_error(self) -> None:
        """A language with no pre-built symbol query must return an Error string."""
        from agent.tools.tools_treesitter import (
            _SYMBOL_QUERIES,
            treesitter_get_symbols,
        )

        # Temporarily remove a language from the symbol query map.
        original = _SYMBOL_QUERIES.pop("python", None)
        try:
            result = treesitter_get_symbols.invoke(
                {"code": "def foo(): pass", "language": "python"}
            )
            assert result.startswith("Error:")
            assert "python" in result
        finally:
            if original is not None:
                _SYMBOL_QUERIES["python"] = original

    def test_get_symbols_excludes_nested_symbols(self) -> None:
        """Symbols nested inside a function body must not appear in the result."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        code = "def outer():\n    def inner():\n        pass\n"
        result = treesitter_get_symbols.invoke({"code": code, "language": "python"})
        assert not result.startswith("Error:")
        symbols = json.loads(result)
        names = [s["name"] for s in symbols if isinstance(s, dict) and "name" in s]
        assert "outer" in names
        assert "inner" not in names

    def test_get_symbols_line_numbers_are_one_based(self) -> None:
        """start_line / end_line must use 1-based indexing."""
        from agent.tools.tools_treesitter import treesitter_get_symbols

        # Single function on lines 1-2 of the snippet.
        code = "def foo():\n    pass\n"
        result = treesitter_get_symbols.invoke({"code": code, "language": "python"})
        assert not result.startswith("Error:")
        symbols = json.loads(result)
        fn = next(s for s in symbols if s.get("name") == "foo")
        assert fn["start_line"] == 1
        assert fn["end_line"] == 2

    # -- get_tools integration --

    def test_get_tools_includes_treesitter(self) -> None:
        """All three tree-sitter tools must appear in the agent's tool list."""
        names = {t.name for t in get_tools()}
        assert "treesitter_parse" in names
        assert "treesitter_query" in names
        assert "treesitter_get_symbols" in names
