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
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import SecretStr

from agent.config import Config, load_config
from agent.nodes import get_llm_from_config
from agent.state import AgentState
from agent.tools import (
    bash,
    calculate,
    create_directory,
    get_current_datetime,
    get_tools,
    grep,
    list_directory,
    read_file,
    replace_in_file,
    write_file,
)

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

        assert result == (
            "['tests/testfolder/folder1/test.py:2:def test():', "
            "'tests/testfolder/folder1/test.cpp: lines 2, 3 (2 matches)']"
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

        assert result == (
            "[('.venv', 'dir'), ('file1', 'file'), "
            "('folder1', 'dir'), ('file2', 'file')]"
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


def _config_dict(**overrides: Any) -> dict[str, Any]:
    """Baseline YAML config payload; tests override single sections as needed."""
    data: dict[str, Any] = {
        "model_provider": {
            "name": "litellm",
            "api": "openai_compatible",
            "api_key": "${TEST_AGENT_API_KEY}",
            "endpoint": "https://api.example.com",
        },
        "model": {"name": "gpt-5.6-luna", "effort": "high"},
        "discord_adapter": {"bot_token": "${TEST_AGENT_BOT_TOKEN}"},
        "heartbeat": {
            "interval": 30,
            "prompt_file": "HEARTBEAT.md",
            "output_adapter": "discord",
            "output_channel": "12345",
        },
        "matrix_adapter": {
            "homeserver_url": "https://matrix.example.com",
            "access_token": "${TEST_AGENT_ACCESS_TOKEN}",
            "user_id": "@bot:matrix.example.com",
        },
    }
    data.update(overrides)
    return data


def _write_config(tmp_path: Path, data: Any) -> str:
    """Serialise *data* to ``config.yaml`` under *tmp_path* and return its path."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


class TestConfigLoading:
    def test_loads_model_and_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_AGENT_API_KEY", "sk-test")
        monkeypatch.setenv("TEST_AGENT_BOT_TOKEN", "token")
        monkeypatch.setenv("TEST_AGENT_ACCESS_TOKEN", "syt_token")
        config = load_config(_write_config(tmp_path, _config_dict()))

        assert isinstance(config, Config)
        assert config.model.name == "gpt-5.6-luna"
        assert config.model.effort == "high"
        assert config.model_provider.api == "openai_compatible"
        assert config.model_provider.endpoint == "https://api.example.com"

    def test_loads_adapter_sections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_AGENT_API_KEY", "sk-test")
        monkeypatch.setenv("TEST_AGENT_BOT_TOKEN", "token")
        monkeypatch.setenv("TEST_AGENT_ACCESS_TOKEN", "syt_token")
        config = load_config(_write_config(tmp_path, _config_dict()))

        assert config.discord_adapter.bot_token == "token"
        assert config.heartbeat.interval == 30
        assert config.heartbeat.prompt_file == "HEARTBEAT.md"
        assert config.heartbeat.output_adapter == "discord"
        assert config.heartbeat.output_channel == "12345"
        assert config.matrix_adapter.user_id == "@bot:matrix.example.com"

    def test_env_var_expanded_in_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_AGENT_API_KEY", "sk-from-env")
        monkeypatch.setenv("TEST_AGENT_BOT_TOKEN", "token")
        monkeypatch.setenv("TEST_AGENT_ACCESS_TOKEN", "syt_token")
        data = _config_dict()
        provider = data["model_provider"]
        assert isinstance(provider, dict)
        provider["api_key"] = "${TEST_AGENT_API_KEY}"

        config = load_config(_write_config(tmp_path, data))

        assert config.model_provider.api_key == "sk-from-env"


class TestGetLlmFactory:
    def test_openai_provider_branch(self) -> None:
        mock_chat_openai = MagicMock(name="ChatOpenAI")
        fake_module = types.SimpleNamespace(ChatOpenAI=mock_chat_openai)

        config = Config(
            **_config_dict(
                model_provider={
                    "name": "openai",
                    "api": "openai",
                    "api_key": "sk-test",
                    "endpoint": "https://api.openai.com/v1",
                },
                model={"name": "gpt-5.4-mini", "effort": "low"},
            )
        )

        with patch.dict("sys.modules", {"langchain_openai": fake_module}):
            get_llm_from_config(config)

        mock_chat_openai.assert_called_once_with(
            model="gpt-5.4-mini",
            api_key=SecretStr("sk-test"),
            base_url="https://api.openai.com/v1",
        )

    def test_anthropic_provider_branch(self) -> None:
        mock_chat_anthropic = MagicMock(name="ChatAnthropic")
        fake_module = types.SimpleNamespace(ChatAnthropic=mock_chat_anthropic)

        config = Config(
            **_config_dict(
                model_provider={
                    "name": "anthropic",
                    "api": "anthropic",
                    "api_key": "sk-ant-test",
                    "endpoint": "https://api.anthropic.com",
                },
                model={"name": "claude-haiku-4-5-20251001", "effort": "low"},
            )
        )

        with patch.dict("sys.modules", {"langchain_anthropic": fake_module}):
            get_llm_from_config(config)

        mock_chat_anthropic.assert_called_once_with(
            model="claude-haiku-4-5-20251001",
            api_key=SecretStr("sk-ant-test"),
            base_url="https://api.anthropic.com",
        )

    def test_openai_compatible_provider_branch(self) -> None:
        mock_chat_openai = MagicMock(name="ChatOpenAI")
        fake_module = types.SimpleNamespace(ChatOpenAI=mock_chat_openai)

        config = Config(
            **_config_dict(
                model_provider={
                    "name": "litellm",
                    "api": "openai_compatible",
                    "api_key": "sk-test",
                    "endpoint": "http://litellm:4000/v1",
                },
                model={"name": "gpt-5.6-luna", "effort": "high"},
            )
        )

        with patch.dict("sys.modules", {"langchain_openai": fake_module}):
            get_llm_from_config(config)

        mock_chat_openai.assert_called_once_with(
            model="gpt-5.6-luna",
            api_key=SecretStr("sk-test"),
            base_url="http://litellm:4000/v1",
        )

    def test_unsupported_api_raises(self) -> None:
        config = Config(
            **_config_dict(
                model_provider={
                    "name": "vertex",
                    "api": "openai",
                    "api_key": "sk-test",
                    "endpoint": "https://example.com",
                }
            )
        )
        # Bypass the schema allow-list to exercise the factory's own guard.
        config.model_provider.api = "vertex"

        with pytest.raises(ValueError, match="Unsupported provider api"):
            get_llm_from_config(config)


# ---------------------------------------------------------------------------
# Graph structure tests — mock the LLM to avoid real API calls
# ---------------------------------------------------------------------------


class TestGraphStructure:
    def test_prompt_builder_appends_agents_md(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Prompt builder should append AGENTS.md content after the base system
        prompt."""
        from pathlib import Path

        from agent.prompts import PromptBuilder

        tmp_path = Path("/tmp/agent-prompt-builder-test")
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "AGENTS.md").write_text(
            "Use tests and keep changes small.\n", encoding="utf-8"
        )

        monkeypatch.chdir(tmp_path)

        with patch("agent.prompts.datetime") as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "2026-06-14"
            prompt = PromptBuilder().build()

        assert "You are an expert software engineering assistant." in prompt
        assert "Use tests and keep changes small." in prompt
        assert "Current date: 2026-06-14" in prompt

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
