from paginate import paginate


def test_middle_page():
    items, pages = paginate(list(range(1, 51)), 3, per_page=7)
    assert items == [15, 16, 17, 18, 19, 20, 21]
    assert pages == 8


def test_page_past_the_end_is_empty():
    items, pages = paginate(list(range(5)), 4, per_page=2)
    assert items == [] and pages == 3


def test_one_per_page():
    items, pages = paginate(["x", "y", "z"], 2, per_page=1)
    assert items == ["y"] and pages == 3
