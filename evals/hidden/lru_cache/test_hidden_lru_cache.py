import pytest

from lru import LRUCache


def test_update_counts_as_use():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 3)  # "b" is now the least recently used
    cache.put("c", 4)
    assert "b" not in cache and cache.get("a") == 3


def test_keys_order_tracks_recency():
    cache = LRUCache(3)
    for key in "abc":
        cache.put(key, key)
    cache.get("a")
    cache.put("b", "B")
    assert cache.keys() == ["c", "a", "b"]


def test_get_missing_does_not_change_order():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("zzz")
    cache.put("c", 3)
    assert cache.keys() == ["b", "c"]


def test_capacity_one_and_validation():
    cache = LRUCache(1)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.keys() == ["b"]
    with pytest.raises(ValueError):
        LRUCache(0)
