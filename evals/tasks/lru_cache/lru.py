class LRUCache:
    """Least-recently-used cache with a fixed capacity.

    Both get() of an existing key and put() count as a "use". When the cache is full,
    put() of a *new* key evicts the least recently used entry.
    """

    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def put(self, key, value):
        if len(self._data) >= self.capacity:
            oldest = next(iter(self._data))
            del self._data[oldest]
        self._data[key] = value

    def keys(self):
        """Keys ordered from least to most recently used."""
        return list(self._data)

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data
