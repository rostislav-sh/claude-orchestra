"""Корзина покупателя."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

from dataclasses import replace

from .models import Item, PromoCode

__all__ = ["Cart", "money"]

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Округление до копеек по правилам бухгалтерии."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Cart:
    def __init__(self) -> None:
        self.items: List[Item] = []
        self.promo: Optional[PromoCode] = None

    def add(self, item: Item) -> None:
        if item.qty <= 0:
            raise ValueError("qty must be positive")
        self.items.append(item)

    def apply_promo(self, promo: PromoCode) -> None:
        self.promo = promo

    def subtotal(self) -> Decimal:
        return money(sum((i.line_total for i in self.items), Decimal("0")))

    def total(self) -> Decimal:
        """Сумма к оплате с учётом промокода."""
        total = sum((i.line_total for i in self.items), Decimal("0"))
        if self.promo:
            total -= total * self.promo.percent / 100
        return money(total)

    def remove(self, sku: str, qty: Optional[int] = None) -> None:
        """Удалить позицию целиком или уменьшить её количество."""
        if qty is not None and qty <= 0:
            raise ValueError("qty must be positive")
        for idx, item in enumerate(self.items):
            if item.sku != sku:
                continue
            if qty is None or item.qty - qty <= 0:
                del self.items[idx]
            else:
                self.items[idx] = replace(item, qty=item.qty - qty)
            return
        raise KeyError(sku)
