"""Public tests for intervals."""

from intervals import merge_intervals


def test_empty_input() -> None:
    assert merge_intervals([]) == []


def test_single_interval() -> None:
    assert merge_intervals([(1, 3)]) == [(1, 3)]


def test_already_sorted_disjoint() -> None:
    assert merge_intervals([(1, 2), (4, 5)]) == [(1, 2), (4, 5)]


def test_unsorted_input_is_sorted() -> None:
    assert merge_intervals([(4, 5), (1, 2)]) == [(1, 2), (4, 5)]


def test_overlapping_intervals_merge() -> None:
    assert merge_intervals([(1, 4), (2, 6)]) == [(1, 6)]


def test_touching_intervals_merge() -> None:
    assert merge_intervals([(1, 2), (2, 3)]) == [(1, 3)]


def test_nested_interval_collapses() -> None:
    assert merge_intervals([(1, 10), (3, 4)]) == [(1, 10)]


def test_duplicate_intervals_collapse() -> None:
    assert merge_intervals([(1, 3), (1, 3)]) == [(1, 3)]


def test_negative_coordinates() -> None:
    assert merge_intervals([(-5, -1), (-2, 3)]) == [(-5, 3)]


def test_invalid_interval_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        merge_intervals([(5, 1)])


def test_input_is_not_mutated() -> None:
    intervals = [(4, 5), (1, 2)]
    original = list(intervals)
    merge_intervals(intervals)
    assert intervals == original
