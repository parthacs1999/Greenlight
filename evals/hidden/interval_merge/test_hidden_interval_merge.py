from intervals import merge_intervals


def test_contained_interval():
    assert merge_intervals([[1, 10], [2, 3], [4, 12]]) == [[1, 12]]


def test_empty_and_single():
    assert merge_intervals([]) == []
    assert merge_intervals([[5, 7]]) == [[5, 7]]


def test_input_is_not_modified():
    data = [[6, 8], [1, 3], [2, 4]]
    snapshot = [list(pair) for pair in data]
    merge_intervals(data)
    assert data == snapshot


def test_tuples_and_negative_numbers():
    assert merge_intervals([(0, 2), (-5, -1), (-1, 0)]) == [[-5, 2]]
