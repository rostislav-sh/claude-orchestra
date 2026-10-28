"""Корзина покупателя."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

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
        total = Decimal("0")
        for item in self.items:
            line = item.line_total
            if self.promo:
                line -= line * self.promo.percent / 100
            total += line
        if self.promo and len(self.items) > 1:
            total -= total * self.promo.percent / 100
        return money(total)
