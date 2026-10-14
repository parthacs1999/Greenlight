from intervals import merge_intervals


def test_overlapping_sorted():
    assert merge_intervals([[1, 4], [2, 6], [8, 10]]) == [[1, 6], [8, 10]]


def test_unsorted_input():
    assert merge_intervals([[8, 10], [1, 3], [2, 5]]) == [[1, 5], [8, 10]]


def test_touching_intervals_merge():
    assert merge_intervals([[1, 3], [3, 5]]) == [[1, 5]]
