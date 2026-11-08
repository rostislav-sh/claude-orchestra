"""Постраничная выдача списков."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, List, Sequence, TypeVar

T = TypeVar("T")


@dataclass
class Page(Generic[T]):
    items: List[T]
    page: int
    total: int

    @property
    def has_next(self) -> bool:
        return self.page * max(len(self.items), 1) < self.total


def paginate(items: Sequence[T], page: int, size: int) -> Page[T]:
    if page < 1 or size < 1:
        raise ValueError("page and size must be positive")
    start = (page - 1) * size
    return Page(list(items[start:start + size]), page, len(items))
