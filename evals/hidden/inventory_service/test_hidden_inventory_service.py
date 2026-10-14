import pytest

from models import Item
from repository import InMemoryRepository
from service import InventoryService, OutOfStock


def make_service():
    repo = InMemoryRepository()
    repo.add(Item("A1", "Apple", 10))
    repo.add(Item("B2", "Banana", 3))
    repo.add(Item("C3", "Cherry", 0))
    return InventoryService(repo)


def test_reserve_down_to_zero_then_out_of_stock():
    service = make_service()
    service.reserve("B2", 3)
    assert service.stock_of("B2") == 0
    with pytest.raises(OutOfStock):
        service.reserve("B2", 1)
    assert service.stock_of("B2") == 0


def test_reserve_many_success():
    service = make_service()
    service.reserve_many({"A1": 4, "B2": 1})
    assert service.stock_of("A1") == 6 and service.stock_of("B2") == 2


def test_reserve_many_unknown_sku_reserves_nothing():
    service = make_service()
    with pytest.raises(KeyError):
        service.reserve_many({"A1": 1, "NOPE": 1})
    assert service.stock_of("A1") == 10


def test_reserve_many_failure_late_in_order():
    service = make_service()
    with pytest.raises(OutOfStock):
        service.reserve_many({"A1": 1, "B2": 1, "C3": 1})
    assert (service.stock_of("A1"), service.stock_of("B2")) == (10, 3)
