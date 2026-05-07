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

import os
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.config import Settings, get_settings
from agent.state import AgentState
from agent.tools import calculate, get_current_datetime, get_tools
from agent.tools_filesystem import read_file, write_file, list_directory, grep, replace_in_file, create_directory
from agent.tools_cmd import bash


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

        result_replace = replace_in_file.invoke({"path": file_path, "old_string": "world", "new_string": "sun"})
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

        result_replace = replace_in_file.invoke({"path": file_path, "old_string": "be", "new_string": "see"})
        assert result_replace.startswith("Error:")

        result_replace = replace_in_file.invoke({"path": file_path, "old_string": "123456", "new_string": "654321"})
        assert result_replace.startswith("Error:")

        result = read_file.invoke({"path": file_path})
        assert result == test_string

        result_replace = replace_in_file.invoke({"path": file_path, "old_string": "be", "new_string": "see", "replace_all": True})
        assert result_replace == "Replaced 2 times"

        result = read_file.invoke({"path": file_path})
        assert result == """All that glitters is not gold.
To see, or not to see, that is the question.
A rose by any other name would smell as sweet.
        """

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
        result = grep.invoke({"pattern": "def", "directory": "tests/testfolder", "file_pattern": ["*.py"], "case_sensitive": False, "skip_dirs": {".venv"}})
        
        assert result == ['tests/testfolder/folder1/test.py:2:def test():']

    def test_grep_multi_file_extensions(self) -> None:
        result = grep.invoke({"pattern": "def", "directory": "tests/testfolder", "file_pattern": ["*.py", "*.cpp"], "case_sensitive": False, "skip_dirs": {".venv"}})
        
        assert result == ['tests/testfolder/folder1/test.py:2:def test():', 'tests/testfolder/folder1/test.cpp:2:#define MAX 1000']

class TestListDirectoryTool:
    def test_list_directory(self) -> None:
        dir_path: str = "tests/testfolder"
        result = list_directory.invoke({"path": dir_path})
        
        assert result == [('.venv', 'dir'), ('file1', 'file'), ('folder1', 'dir'), ('file2', 'file')]

class TestGetTools:
    def test_always_includes_builtins(self) -> None:
        tools = get_tools()
        names = {t.name for t in tools}
        assert "calculate" in names
        assert "get_current_datetime" in names

    def test_web_search_absent_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_explicit_model_name_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("MODEL_NAME", "gpt-5.4-mini")
        s = get_settings()
        assert s.resolved_model == "gpt-5.4-mini"

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "cohere")
        with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
            get_settings()


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