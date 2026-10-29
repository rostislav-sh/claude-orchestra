"""Модели предметной области."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import FrozenSet, Iterable, List, Optional

__all__ = ["Item", "PromoCode", "Order"]


@dataclass(frozen=True)
class Item:
    """Позиция: товар и количество."""

    sku: str
    title: str
    price: Decimal
    qty: int = 1

    @property
    def line_total(self) -> Decimal:
        return self.price * self.qty


@dataclass(frozen=True)
class PromoCode:
    """Промокод: скидка в процентах с ограничениями."""

    code: str
    percent: int
    min_subtotal: Decimal = Decimal("0")
    max_discount: Optional[Decimal] = None
    excluded_skus: FrozenSet[str] = frozenset()
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None

    def __post_init__(self) -> None:
        if not 0 < self.percent <= 100:
            raise ValueError("percent must be in 1..100")
        if self.min_subtotal < 0:
            raise ValueError("min_subtotal must be >= 0")
        if self.max_discount is not None and self.max_discount <= 0:
            raise ValueError("max_discount must be positive")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        skus: Iterable[str] = self.excluded_skus
        object.__setattr__(self, "excluded_skus", frozenset(skus))

    def is_valid_on(self, day: date) -> bool:
        if self.valid_from and day < self.valid_from:
            return False
        if self.valid_to and day > self.valid_to:
            return False
        return True


@dataclass
class Order:
    """Оформленный заказ."""

    id: int
    created_at: datetime
    items: List[Item] = field(default_factory=list)
    promo: Optional[PromoCode] = None
