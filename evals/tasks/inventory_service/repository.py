import copy


class InMemoryRepository:
    """Stores items by SKU.

    get() returns a copy, so callers can't change stored data by accident.
    Call save() to persist a changed item.
    """

    def __init__(self):
        self._items = {}

    def add(self, item):
        self._items[item.sku] = copy.copy(item)

    def get(self, sku):
        try:
            return copy.copy(self._items[sku])
        except KeyError:
            raise KeyError(f"unknown sku: {sku}") from None

    def save(self, item):
        self._items[item.sku] = copy.copy(item)
