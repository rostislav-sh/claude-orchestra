"""Модели предметной области."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

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
    """Промокод: скидка в процентах на всю корзину."""

    code: str
    percent: int

    def __post_init__(self) -> None:
        if not 0 < self.percent <= 100:
            raise ValueError("percent must be in 1..100")


@dataclass
class Order:
    """Оформленный заказ."""

    id: int
    created_at: datetime
    items: List[Item] = field(default_factory=list)
    promo: Optional[PromoCode] = None
