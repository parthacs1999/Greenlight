from cart import Cart
from pricing import apply_discount


def test_apply_discount_directly():
    assert apply_discount(50.0, 15) == 42.5
    assert apply_discount(80.0, 0) == 80.0
    assert apply_discount(80.0, 100) == 0.0


def test_other_cart_and_discount():
    cart = Cart()
    cart.add("mug", 12.50, qty=4)
    # 50.00 - 20% = 40.00, plus 8% tax = 43.20
    assert cart.total(discount_percent=20) == 43.2
