import pytest

from paginate import paginate


def test_first_page():
    items, pages = paginate(list(range(25)), 1, per_page=10)
    assert items == list(range(10))
    assert pages == 3


def test_last_partial_page():
    items, _ = paginate(list(range(25)), 3, per_page=10)
    assert items == [20, 21, 22, 23, 24]


def test_exact_multiple():
    _, pages = paginate(list(range(20)), 1, per_page=10)
    assert pages == 2


def test_empty():
    items, pages = paginate([], 1)
    assert items == [] and pages == 0


def test_invalid_page():
    with pytest.raises(ValueError):
        paginate([1, 2], 0)
