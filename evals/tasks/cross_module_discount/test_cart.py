import pytest

from cart import Cart


def make_cart():
    cart = Cart()
    cart.add("book", 20.00, qty=2)
    cart.add("pen", 5.00)
    return cart


def test_subtotal():
    assert make_cart().subtotal() == 45.00


def test_total_without_discount():
    assert make_cart().total() == 48.60


def test_total_with_discount():
    # 45.00 - 10% = 40.50, plus 8% tax = 43.74
    assert make_cart().total(discount_percent=10) == 43.74


def test_invalid_discount():
    with pytest.raises(ValueError):
        make_cart().total(discount_percent=150)
