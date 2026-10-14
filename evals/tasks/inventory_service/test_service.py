import pytest

from models import Item
from repository import InMemoryRepository
from service import InventoryService, OutOfStock


def make_service():
    repo = InMemoryRepository()
    repo.add(Item("A1", "Apple", 10))
    repo.add(Item("B2", "Banana", 3))
    return InventoryService(repo)


def test_reserve_reduces_stock():
    service = make_service()
    assert service.reserve("A1", 4) == 6
    assert service.stock_of("A1") == 6


def test_reserve_many_is_all_or_nothing():
    service = make_service()
    with pytest.raises(OutOfStock):
        service.reserve_many({"A1": 2, "B2": 5})
    assert service.stock_of("A1") == 10
    assert service.stock_of("B2") == 3
