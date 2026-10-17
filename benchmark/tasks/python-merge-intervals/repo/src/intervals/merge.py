"""Interval merging helpers."""

from __future__ import annotations

from collections.abc import Sequence


def merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping and touching intervals into a sorted list.

    Each interval is an inclusive ``(start, end)`` pair. The result is sorted by
    ascending ``start`` and contains only disjoint intervals. Intervals that
    overlap or touch (``next_start <= current_end``) are combined. An interval
    with ``start > end`` raises ``ValueError``. The input is not mutated.
    """
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if start > end:
            raise ValueError(f"invalid interval: ({start}, {end})")
        if merged and start <= merged[-1][1]:
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
