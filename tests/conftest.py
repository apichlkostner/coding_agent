"""Conftest for the integration tests under ``tests/fixtures/lsp_cpp``.

Provides a session-scoped fixture that stages the C++ project into a
temp directory and rewrites ``compile_commands.json`` to point at the
real (absolute) workspace location, which clangd requires.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lsp_cpp"

CLANG = shutil.which("clang") or shutil.which("clang++") or "clang++"


def _build_compile_commands(workspace: Path) -> list[dict[str, str]]:
    return [
        {
            "directory": str(workspace),
            "command": (
                f"{CLANG} -std=c++17 -c -x c++ {workspace / 'main.cpp'}"
            ),
            "file": str(workspace / "main.cpp"),
        },
        {
            "directory": str(workspace),
            "command": (
                f"{CLANG} -std=c++17 -c -x c++ "
                f"-I{workspace} {workspace / 'hello.cpp'}"
            ),
            "file": str(workspace / "hello.cpp"),
        },
    ]


@pytest.fixture
def lsp_cpp_project(tmp_path: Path) -> Iterator[Path]:
    """Stage the C++ fixture into *tmp_path* and return the project root."""
    import json

    project = tmp_path / "lsp_cpp"
    project.mkdir()
    for name in ("hello.h", "hello.cpp", "main.cpp"):
        src = FIXTURE_DIR / name
        if src.exists():
            (project / name).write_text(src.read_text())
    (project / "compile_commands.json").write_text(
        json.dumps(_build_compile_commands(project), indent=2)
    )
    yield project
