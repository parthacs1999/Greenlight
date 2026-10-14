from backoff import backoff_delays


def test_other_base():
    assert backoff_delays(2, 3) == [2, 4, 8]


def test_cap_below_base():
    assert backoff_delays(5, 3, cap=1) == [1, 1, 1]


def test_cap_with_custom_factor():
    assert backoff_delays(1, 5, cap=20, factor=3) == [1, 3, 9, 20, 20]
