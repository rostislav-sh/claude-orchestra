"""Базовое хранилище в памяти."""
from __future__ import annotations

from typing import Callable, Dict, Generic, List, Optional, TypeVar

from helpdesk.core.errors import NotFound
from helpdesk.core.ids import IdGenerator

T = TypeVar("T")


class InMemoryRepository(Generic[T]):
    kind = "entity"

    def __init__(self, ids: IdGenerator) -> None:
        self._ids = ids
        self._items: Dict[int, T] = {}

    def next_id(self) -> int:
        return self._ids.next(self.kind)

    def add(self, item: T) -> T:
        self._items[item.id] = item  # type: ignore[attr-defined]
        return item

    def get(self, item_id: int) -> T:
        try:
            return self._items[item_id]
        except KeyError:
            raise NotFound(f"{self.kind} {item_id}") from None

    def find(self, item_id: int) -> Optional[T]:
        return self._items.get(item_id)

    def all(self) -> List[T]:
        return list(self._items.values())

    def filter(self, pred: Callable[[T], bool]) -> List[T]:
        return [i for i in self._items.values() if pred(i)]
