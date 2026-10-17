"""Public tests for dependency_order."""

import pytest

from dependency_order import dependency_order


def test_empty_mapping() -> None:
    assert dependency_order({}) == []


def test_single_task_no_prerequisites() -> None:
    assert dependency_order({"a": []}) == ["a"]


def test_simple_chain() -> None:
    assert dependency_order({"b": ["a"], "c": ["b"]}) == ["a", "b", "c"]


def test_prerequisite_only_node_is_included() -> None:
    assert dependency_order({"b": ["a"]}) == ["a", "b"]


def test_deterministic_tie_break() -> None:
    assert dependency_order({"b": [], "a": []}) == ["a", "b"]


def test_duplicate_edges_have_no_effect() -> None:
    assert dependency_order({"b": ["a", "a"]}) == ["a", "b"]


def test_self_cycle_raises() -> None:
    with pytest.raises(ValueError):
        dependency_order({"a": ["a"]})


def test_multi_node_cycle_raises() -> None:
    with pytest.raises(ValueError):
        dependency_order({"a": ["b"], "b": ["a"]})


def test_input_is_not_mutated() -> None:
    dependencies = {"b": ["a"], "c": ["b"]}
    original = {task: list(prereqs) for task, prereqs in dependencies.items()}
    dependency_order(dependencies)
    assert dependencies == original
