"""Корзина покупателя."""
from __future__ import annotations

from datetime import date
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

    def discount(self, today: Optional[date] = None) -> Decimal:
        """Скидка по промокоду: округляется до копеек, затем ограничивается потолком."""
        promo = self.promo
        if promo is None or not promo.is_valid_on(today or date.today()):
            return Decimal("0")
        base = sum((i.line_total for i in self.items if i.sku not in promo.excluded_skus), Decimal("0"))
        if base < promo.min_subtotal:
            return Decimal("0")
        value = money(base * promo.percent / 100)
        if promo.max_discount is not None:
            value = min(value, promo.max_discount)
        return value

    def total(self, today: Optional[date] = None) -> Decimal:
        """Сумма к оплате с учётом промокода."""
        subtotal = sum((i.line_total for i in self.items), Decimal("0"))
        return money(subtotal - self.discount(today))
