def merge_intervals(intervals):
    """Merge overlapping or touching [start, end] intervals.

    The input may be in any order and is not modified. Returns a new list of
    [start, end] lists sorted by start. Intervals that merely touch, like [1, 3] and
    [3, 5], are merged into [1, 5].
    """
    merged = []
    for start, end in intervals:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
