"""Conftest for the integration tests under ``tests/fixtures/lsp_cpp``.

Provides a function-scoped fixture that stages the C++ project into a
temp directory and rewrites ``compile_commands.json`` to point at the
real (absolute) workspace location, which clangd requires.  Each
integration test gets a pristine workspace, so state cannot leak
between tests.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lsp_cpp"
PYTHON_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lsp_python"

CLANG = shutil.which("clang") or shutil.which("clang++") or "clang++"


def _build_compile_commands(workspace: Path) -> list[dict[str, str]]:
    return [
        {
            "directory": str(workspace),
            "command": (f"{CLANG} -std=c++17 -c -x c++ {workspace / 'main.cpp'}"),
            "file": str(workspace / "main.cpp"),
        },
        {
            "directory": str(workspace),
            "command": (
                f"{CLANG} -std=c++17 -c -x c++ -I{workspace} {workspace / 'hello.cpp'}"
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


@pytest.fixture
def lsp_python_project(tmp_path: Path) -> Iterator[Path]:
    """Stage the Python fixture into *tmp_path* and return the project root."""
    project = tmp_path / "lsp_python"
    shutil.copytree(PYTHON_FIXTURE_DIR, project)

    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    interpreter = venv_bin / "python"
    try:
        interpreter.symlink_to(Path(sys.executable))
    except OSError:
        shutil.copy2(sys.executable, interpreter)

    yield project
