from lru import LRUCache


def test_put_and_get():
    cache = LRUCache(2)
    cache.put("a", 1)
    assert cache.get("a") == 1
    assert cache.get("missing", "default") == "default"


def test_get_counts_as_use():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # "b" is now the least recently used
    cache.put("c", 3)
    assert "b" not in cache
    assert "a" in cache and "c" in cache


def test_updating_existing_key_does_not_evict():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    assert len(cache) == 2
    assert cache.get("a") == 10 and cache.get("b") == 2
