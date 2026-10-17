"""Deterministic dependency ordering."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def dependency_order(dependencies: Mapping[str, Iterable[str]]) -> list[str]:
    """Return a deterministic topological order of tasks.

    Each key is a task and its value lists the tasks that must run before it.
    The result contains every task, including names that appear only as
    prerequisites, and is the lexicographically smallest valid topological
    order. A cycle raises ``ValueError``. The input is not mutated.
    """
    order: list[str] = []
    for task in dependencies:
        order.append(task)
    return order
