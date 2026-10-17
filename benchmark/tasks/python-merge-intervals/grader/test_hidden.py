"""Hidden edge-case tests injected by the grader."""

import pytest

from intervals import merge_intervals


def test_empty_input() -> None:
    assert merge_intervals([]) == []


def test_single_interval() -> None:
    assert merge_intervals([(7, 7)]) == [(7, 7)]


def test_unsorted_overlapping() -> None:
    assert merge_intervals([(5, 7), (1, 3), (2, 4)]) == [(1, 4), (5, 7)]


def test_touching_endpoints_merge() -> None:
    assert merge_intervals([(1, 2), (2, 3), (3, 4)]) == [(1, 4)]


def test_strictly_separated_stay_distinct() -> None:
    assert merge_intervals([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]


def test_duplicates_collapse() -> None:
    assert merge_intervals([(2, 5), (2, 5), (2, 5)]) == [(2, 5)]


def test_nested_intervals() -> None:
    assert merge_intervals([(1, 10), (2, 3), (4, 5)]) == [(1, 10)]


def test_negative_coordinates() -> None:
    assert merge_intervals([(-10, -5), (-6, -1), (0, 2)]) == [(-10, -1), (0, 2)]


def test_chain_of_overlaps() -> None:
    assert merge_intervals([(1, 3), (3, 5), (5, 7), (8, 9)]) == [(1, 7), (8, 9)]


def test_invalid_interval_raises() -> None:
    with pytest.raises(ValueError):
        merge_intervals([(1, 2), (9, 4)])


def test_invalid_interval_raises_even_when_alone() -> None:
    with pytest.raises(ValueError):
        merge_intervals([(3, 1)])


def test_input_is_not_mutated() -> None:
    intervals = [(5, 7), (1, 3), (2, 4)]
    original = list(intervals)
    merge_intervals(intervals)
    assert intervals == original


def test_result_is_a_new_list() -> None:
    intervals = [(1, 2)]
    result = merge_intervals(intervals)
    assert result is not intervals
