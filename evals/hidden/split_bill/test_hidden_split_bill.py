import pytest

from billing import split_bill


@pytest.mark.parametrize("total", [0, 1, 7, 99, 1001, 12345])
@pytest.mark.parametrize("people", [1, 2, 3, 7])
@pytest.mark.parametrize("tip", [0, 10, 18])
def test_shares_always_add_up(total, people, tip):
    shares = split_bill(total, people, tip_percent=tip)
    expected_total = total + round(total * tip / 100)
    assert len(shares) == people
    assert sum(shares) == expected_total
    assert max(shares) - min(shares) <= 1
    assert shares == sorted(shares, reverse=True)  # extra cents go to the first people


def test_tiny_bill():
    assert split_bill(1, 3) == [1, 0, 0]


def test_invalid_people():
    with pytest.raises(ValueError):
        split_bill(100, 0)
