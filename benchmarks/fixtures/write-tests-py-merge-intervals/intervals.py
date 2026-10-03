"""Interval merging."""


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge closed intervals that overlap or touch and return them sorted by start.

    The input may be unsorted and is not modified. An interval whose start is
    greater than its end raises ValueError.
    """
    for start, end in intervals:
        if start > end:
            raise ValueError(f"invalid interval: ({start}, {end})")
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
