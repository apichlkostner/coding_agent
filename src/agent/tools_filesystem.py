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
def read_file(path: str, offset: int = 0, lines: int = 0) -> str:
    """Reads a file from the given path and returns the content of the file

    Args:
        path: path of the file
        offset: line offset starting with 0
        lines: nuber of lines to read, 0 equals all

    Example
    -------
    read_file("docs/index.md") -> "# Index of docs folder"
    read_file("docs/index.md", 1, 1) -> "## Introduction"
    """
    try:
        if (_is_subpath(path, strict=True)):
            with open(path, "r", encoding="utf-8") as f:
                count = 0
                result = ""
                for line in f:
                    if count >= offset:
                        result += line
                    count += 1
                    if (lines > 0 and count >= offset + lines):
                        break
                return result
        else:
            return "Error: Path is not inside the project folder"
    except Exception as err:
        return "Error: " + str(err)

@tool
def write_file(path: str | Path, content: str) -> str:
    """Writes content to file in path.
    Creates a new file if it doesn't exist.
    If the parent folder doesn't exist, call create_folder before.

    Example
    -------
    write_file("docs/test.md", "# My new test docu")
    """
    # TODO: create parent folder if needed
    try:
        if (_is_subpath(path, strict=False)):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
                return "Success"
        else:
            return "Error: Path " + path.as_posix() + " is not inside the project folder"
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
def create_directory(path: str | Path) -> str:
    """Creates a directory, including parents

    Example
    -------
    create_folder("docs/internal") -> "Success
    """
    try:
        if (_is_subpath(path, strict=False)):
            path = Path(path)
            path.mkdir(parents=True, exist_ok=True)
            return "Success"
        return "Error: Path is not inside the project folder"
    except Exception as err:
        return "Error: " + str(err)

@tool
def replace_in_file(path: str | Path, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replaces a string in a file with another string.
    Returns "Replaced {number of replaces} times" on success
    Returns an Error string if the old_string is not unique and replace_all is not True

    Example
    -------
    replace_in_file("docs/test.txt", "old text", "new text") -> "Success"
    """
    try:
        if (_is_subpath(path, strict=True)):
            path = Path(path)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()                
                count = content.count(old_string)
                
                if count == 0:
                    return "Error: " + old_string + " not found in file " + path.as_posix()
                elif count > 1 and not replace_all:
                    return "Error: " + old_string + " found " + str(count) + " times in file " + path.as_posix()
                
                new_content = content.replace(old_string, new_string)
                
                with open(path, 'w') as f:
                    f.write(new_content)

                return "Replaced " + str(count) + " times"
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