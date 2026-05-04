"""Tool definitions available to the agent.

Each tool is a plain Python function decorated with ``@tool`` from LangChain.
LangGraph's ``ToolNode`` discovers and executes them automatically.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

from langchain_core.tools import BaseTool, tool
from pathlib import Path

@tool
def bash(command: str, timeout: int = 60, description: str = "") -> str:
    """Run a shell command and return its stdout, stderr, and exit code.

    Args:
        command: the comman to be executed
        timeout: timout in seconds
        description: description for the user to understand the command

    Example
    -------
    bash("mkdir build && cd build && cmake ..") -> "Output of commands"
    """
    try:
        # no need to split the command, AI agent is allowed to call arbitrary commands
        # agent should run in a sandbox
        # TODO: permission system needed later
        result = subprocess.run(command, timeout=timeout, shell=True, text=True, capture_output=True)
        return f"exit_code: {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    except Exception as err:
        return f"Error: {err}"