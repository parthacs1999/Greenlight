from billing import split_bill


def test_even_split():
    assert split_bill(1000, 4) == [250, 250, 250, 250]


def test_uneven_split_adds_up():
    assert split_bill(1000, 3) == [334, 333, 333]


def test_with_tip():
    # 1000 + 15% = 1150, split 4 ways
    assert split_bill(1000, 4, tip_percent=15) == [288, 288, 287, 287]
