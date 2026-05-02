"""Tool definitions available to the agent.

Each tool is a plain Python function decorated with ``@tool`` from LangChain.
LangGraph's ``ToolNode`` discovers and executes them automatically.
"""

from __future__ import annotations

import ast
import operator
import os

from langchain_core.tools import BaseTool, tool
from os import getcwd
from pathlib import Path

def _is_subpath(file_path: str | Path, strict: bool = False) -> bool:
    file_path = Path(file_path).resolve(strict=strict)
    cwd = os.getcwd()
    project_path = Path(cwd).resolve(strict=strict)
    return file_path.is_relative_to(project_path)

def _entry_type(p: Path) -> str:
    if p.is_symlink():
        return "symlink"
    if p.is_dir():
        return "dir"
    if p.is_file():
        return "file"
    return "other"

# ---------------------------------------------------------------------------
# Filesystem tools
# ---------------------------------------------------------------------------

@tool
def read_file(path: str) -> str:
    """Reads a file from the given path and returns the content of the file

    Example
    -------
    read_file("docs/index.md") -> "# Index of docs folder"
    """
    # TODO: size limits, offset and length parameter
    try:
        if (_is_subpath(path, strict=True)):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return "Error: Path is not inside the project folder"
    except Exception as err:
        return "Error: " + str(err)

@tool
def write_file(path: str | Path, content: str) -> str:
    """Writes content to file in path

    Example
    -------
    write_file("docs/test.md", "# My new test docu")
    """
    # TODO: create parent folder if needed
    try:
        if (_is_subpath(path)):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
                return "Success"
            return "Error: Path is not inside the project folder"
    except Exception as err:
        return "Error: " + str(err)

@tool
def list_directory(path: str | Path) -> str:
    """Lists the content of a directory at the given path.

    Example
    -------
    list_directory("docs") -> "[file1, file2, file3]"
    """
    try:
        if (_is_subpath(path, strict=True)):
            directory = Path(path)
            entries = list((e.name, _entry_type(e)) for e in directory.iterdir()) 
            return str(entries)
        return "Error: Path is not inside the project folder"
    except Exception as err:
        return "Error: " + str(err)