"""Deterministic dependency ordering."""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping


def dependency_order(dependencies: Mapping[str, Iterable[str]]) -> list[str]:
    """Return a deterministic topological order of tasks.

    Each key is a task and its value lists the tasks that must run before it.
    The result contains every task, including names that appear only as
    prerequisites, and is the lexicographically smallest valid topological
    order. A cycle raises ``ValueError``. The input is not mutated.
    """
    successors: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}

    def register(task: str) -> None:
        successors.setdefault(task, set())
        indegree.setdefault(task, 0)

    for task, prerequisites in dependencies.items():
        register(task)
        for prerequisite in prerequisites:
            register(prerequisite)
            if task not in successors[prerequisite]:
                successors[prerequisite].add(task)
                indegree[task] += 1

    available = [task for task, degree in indegree.items() if degree == 0]
    heapq.heapify(available)

    order: list[str] = []
    while available:
        task = heapq.heappop(available)
        order.append(task)
        for successor in successors[task]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(available, successor)

    if len(order) != len(indegree):
        raise ValueError("dependency graph contains a cycle")

    return order
