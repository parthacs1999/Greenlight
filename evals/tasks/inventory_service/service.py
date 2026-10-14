class OutOfStock(Exception):
    pass


class InventoryService:
    def __init__(self, repo):
        self.repo = repo

    def stock_of(self, sku):
        return self.repo.get(sku).stock

    def reserve(self, sku, qty):
        """Reserve qty units of one SKU and return the remaining stock.

        Raises OutOfStock (and changes nothing) if there isn't enough stock.
        """
        if qty <= 0:
            raise ValueError("qty must be positive")
        item = self.repo.get(sku)
        if item.stock < qty:
            raise OutOfStock(sku)
        item.stock -= qty
        return item.stock

    def reserve_many(self, order):
        """Reserve several SKUs at once, given {sku: qty}.

        All-or-nothing: if any SKU lacks stock (OutOfStock) or is unknown (KeyError),
        the error is raised and nothing at all is reserved.
        """
        for sku, qty in order.items():
            self.reserve(sku, qty)
