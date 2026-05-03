"""Tool definitions available to the agent.

Each tool is a plain Python function decorated with ``@tool`` from LangChain.
LangGraph's ``ToolNode`` discovers and executes them automatically.
"""

from __future__ import annotations

import os
import re

from langchain_core.tools import BaseTool, tool
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
def list_directory(path: str | Path) -> list:
    """Lists the content of a directory at the given path.

    Example
    -------
    list_directory("docs") -> "[file1, file2, file3]"
    """
    try:
        if (_is_subpath(path, strict=True)):
            directory = Path(path)
            entries = list((e.name, _entry_type(e)) for e in directory.iterdir()) 
            return entries
        return "Error: Path is not inside the project folder"
    except Exception as err:
        return "Error: " + str(err)
    
@tool
def grep(pattern: str, directory: str | Path, file_pattern: list = ["*"], case_sensitive: bool = True, skip_dirs: set = None) -> list:
    """Greps for given pattern

    Args:
        pattern: Regex pattern to match
        directory: Directory to search
        file_pattern: List like ['*.py', '*.js'] — None means all files
        case_sensitive: If False, use re.IGNORECASE
        skip_dirs: Set of directory names to skip like {'.git', 'node_modules', '__pycache__'}

    Example
    -------
    grep("test", ".", ["*.py"], False, {".git", ".venv"}) -> ['tests/testfolder/folder1/test.py:2:def test():']
    """
    try:
        if (_is_subpath(directory, strict=True)):
            directory = Path(directory)
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
            matches = []
            for fp in file_pattern:
                for filepath in Path(directory).rglob(fp):
                    if not filepath.is_file():
                        continue
                    if skip_dirs and any(part in skip_dirs for part in filepath.parts):
                        continue
                    
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            for line_num, line in enumerate(f, 1):
                                if regex.search(line):
                                    matches.append(f"{filepath}:{line_num}:{line.rstrip()}")
                    except Exception as e:
                        matches.append(f"Error: " + str(e))

            return matches
        return ["Error: Directory is not inside the project folder"]
    except Exception as err:
        return ["Error: " + str(err)]