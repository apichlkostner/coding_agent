"""Hidden edge-case tests injected by the grader."""

import pytest

from dependency_order import dependency_order


def test_empty_mapping() -> None:
    assert dependency_order({}) == []


def test_single_task() -> None:
    assert dependency_order({"only": []}) == ["only"]


def test_disconnected_nodes_are_ordered_lexicographically() -> None:
    assert dependency_order({"c": [], "a": [], "b": []}) == ["a", "b", "c"]


def test_chain() -> None:
    assert dependency_order({"d": ["c"], "c": ["b"], "b": ["a"]}) == [
        "a",
        "b",
        "c",
        "d",
    ]


def test_branching_prerequisites() -> None:
    dependencies = {"d": ["b", "c"], "b": ["a"], "c": ["a"]}
    assert dependency_order(dependencies) == ["a", "b", "c", "d"]


def test_branching_dependents() -> None:
    dependencies = {"a": [], "b": ["a"], "c": ["a"]}
    assert dependency_order(dependencies) == ["a", "b", "c"]


def test_duplicate_edges_have_no_effect() -> None:
    assert dependency_order({"b": ["a", "a", "a"]}) == ["a", "b"]


def test_prerequisite_only_nodes_are_included() -> None:
    assert dependency_order({"c": ["b"], "b": ["a"]}) == ["a", "b", "c"]


def test_prerequisite_only_node_without_key() -> None:
    assert dependency_order({"b": ["a"], "d": ["c"]}) == ["a", "b", "c", "d"]


def test_deterministic_tie_break_prefers_smallest() -> None:
    dependencies = {"m": [], "a": [], "z": []}
    assert dependency_order(dependencies) == ["a", "m", "z"]


def test_tie_break_after_unlock() -> None:
    dependencies = {"b": ["a"], "c": [], "a": []}
    assert dependency_order(dependencies) == ["a", "b", "c"]


def test_self_cycle_raises() -> None:
    with pytest.raises(ValueError):
        dependency_order({"a": ["a"]})


def test_two_node_cycle_raises() -> None:
    with pytest.raises(ValueError):
        dependency_order({"a": ["b"], "b": ["a"]})


def test_longer_cycle_raises() -> None:
    with pytest.raises(ValueError):
        dependency_order({"a": ["c"], "b": ["a"], "c": ["b"]})


def test_cycle_with_acyclic_prefix_raises() -> None:
    dependencies = {"a": [], "b": ["a"], "c": ["d"], "d": ["c"]}
    with pytest.raises(ValueError):
        dependency_order(dependencies)


def test_input_is_not_mutated() -> None:
    dependencies = {"b": ["a"], "c": ["b"]}
    original = {task: list(prereqs) for task, prereqs in dependencies.items()}
    dependency_order(dependencies)
    assert dependencies == original


def test_result_is_a_new_list() -> None:
    dependencies = {"a": []}
    result = dependency_order(dependencies)
    assert result is not dependencies
