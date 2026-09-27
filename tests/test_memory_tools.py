"""Tests for the persistent key-value memory tools (tools_memory.py)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agent.tools import get_tools
from agent.tools.tools_memory import read_memory, store_memory


@pytest.fixture
def chdir_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the test in an isolated temporary working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestStoreMemory:
    def test_returns_ok(self, chdir_tmp: Path) -> None:
        assert store_memory.invoke({"key": "last_weather", "value": "sunny"}) == "ok"

    def test_writes_json_file(self, chdir_tmp: Path) -> None:
        store_memory.invoke({"key": "last_weather", "value": "sunny"})

        path = chdir_tmp / ".agent_memory.json"
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == {"last_weather": "sunny"}

    def test_overwrite_existing_key(self, chdir_tmp: Path) -> None:
        store_memory.invoke({"key": "k", "value": "old"})
        store_memory.invoke({"key": "k", "value": "new"})

        assert read_memory.invoke({"key": "k"}) == "new"

    def test_empty_value_clears(self, chdir_tmp: Path) -> None:
        store_memory.invoke({"key": "k", "value": "something"})
        store_memory.invoke({"key": "k", "value": ""})

        assert read_memory.invoke({"key": "k"}) == ""

    def test_multiple_keys_persist(self, chdir_tmp: Path) -> None:
        store_memory.invoke({"key": "a", "value": "1"})
        store_memory.invoke({"key": "b", "value": "2"})

        data = json.loads((chdir_tmp / ".agent_memory.json").read_text("utf-8"))
        assert data == {"a": "1", "b": "2"}

    def test_unicode_value_roundtrip(self, chdir_tmp: Path) -> None:
        store_memory.invoke({"key": "k", "value": "wetter: schön ☀"})

        assert read_memory.invoke({"key": "k"}) == "wetter: schön ☀"


class TestReadMemory:
    def test_missing_file_returns_empty(self, chdir_tmp: Path) -> None:
        assert read_memory.invoke({"key": "anything"}) == ""

    def test_missing_key_returns_empty(self, chdir_tmp: Path) -> None:
        store_memory.invoke({"key": "a", "value": "1"})

        assert read_memory.invoke({"key": "b"}) == ""

    def test_roundtrip(self, chdir_tmp: Path) -> None:
        store_memory.invoke({"key": "last_weather", "value": "rainy"})

        assert read_memory.invoke({"key": "last_weather"}) == "rainy"

    def test_corrupt_file_returns_empty(
        self, chdir_tmp: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (chdir_tmp / ".agent_memory.json").write_text("{not json", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="agent.tools.tools_memory"):
            result = read_memory.invoke({"key": "k"})

        assert result == ""
        assert "Failed to load agent memory" in caplog.text

    def test_store_recovers_after_corrupt_file(self, chdir_tmp: Path) -> None:
        (chdir_tmp / ".agent_memory.json").write_text("garbage", encoding="utf-8")

        store_memory.invoke({"key": "k", "value": "v"})

        assert read_memory.invoke({"key": "k"}) == "v"


class TestGetTools:
    def test_memory_tools_registered(self) -> None:
        names = {t.name for t in get_tools()}
        assert "store_memory" in names
        assert "read_memory" in names
