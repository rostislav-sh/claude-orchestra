from datetime import date
from decimal import Decimal as D

import pytest

from shop.cart import Cart
from shop.models import Item, PromoCode

DAY = date(2026, 10, 29)


def make(promo, *prices):
    c = Cart()
    for i, p in enumerate(prices):
        c.add(Item(f"s{i}", "x", D(p)))
    c.apply_promo(promo)
    return c


def test_excluded():
    assert make(PromoCode("P", 10, excluded_skus=["s0"]), "100", "100").discount(DAY) == D("10.00")


def test_min_subtotal():
    assert make(PromoCode("P", 10, min_subtotal=D("500")), "100").discount(DAY) == 0


def test_cap():
    assert make(PromoCode("P", 50, max_discount=D("5")), "100").discount(DAY) == D("5")


def test_dates():
    p = PromoCode("P", 10, valid_from=DAY, valid_to=DAY)
    assert make(p, "100").discount(DAY) == D("10.00")
    assert make(p, "100").discount(date(2026, 10, 30)) == 0


def test_validation():
    with pytest.raises(ValueError):
        PromoCode("P", 10, max_discount=D("0"))


def test_total_with_today():
    assert make(PromoCode("P", 10), "100").total(DAY) == D("90.00")
