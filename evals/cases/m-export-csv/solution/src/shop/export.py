"""Экспорт заказов в CSV."""
from __future__ import annotations

import csv
import os
from typing import Iterable, Union

from .cart import Cart
from .models import Order

__all__ = ["export_orders_csv"]

HEADER = ("id", "created_at", "items", "total", "promo")


def _order_total(order: Order) -> str:
    cart = Cart()
    for item in order.items:
        cart.add(item)
    if order.promo:
        cart.apply_promo(order.promo)
    return f"{cart.total():.2f}"


def export_orders_csv(orders: Iterable[Order], path: Union[str, os.PathLike]) -> int:
    """Записать заказы в CSV и вернуть их число."""
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(HEADER)
        for order in orders:
            writer.writerow([
                order.id,
                order.created_at.replace(microsecond=0).isoformat(),
                sum(i.qty for i in order.items),
                _order_total(order),
                order.promo.code if order.promo else "",
            ])
            count += 1
    return count
