from backoff import backoff_delays


def test_basic_sequence():
    assert backoff_delays(1, 4) == [1, 2, 4, 8]


def test_cap_applied():
    assert backoff_delays(1, 6, cap=10) == [1, 2, 4, 8, 10, 10]


def test_custom_factor():
    assert backoff_delays(0.5, 3, factor=3) == [0.5, 1.5, 4.5]


def test_zero_retries():
    assert backoff_delays(1, 0) == []
