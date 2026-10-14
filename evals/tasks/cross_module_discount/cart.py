from pricing import add_tax, apply_discount


class Cart:
    def __init__(self):
        self.lines = []

    def add(self, name, unit_price, qty=1):
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.lines.append((name, unit_price, qty))

    def subtotal(self):
        return round(sum(price * qty for _, price, qty in self.lines), 2)

    def total(self, discount_percent=0):
        return add_tax(apply_discount(self.subtotal(), discount_percent))
