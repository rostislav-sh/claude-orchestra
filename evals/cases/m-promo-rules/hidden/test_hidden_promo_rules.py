from datetime import date
from decimal import Decimal as D

import pytest

from shop.cart import Cart
from shop.models import Item, PromoCode

TODAY = date(2026, 10, 29)


def cart(*items, promo=None):
    c = Cart()
    for sku, price, qty in items:
        c.add(Item(sku, sku.upper(), D(price), qty))
    if promo:
        c.apply_promo(promo)
    return c


# ---- совместимость

def test_old_constructor_and_behavior():
    p = PromoCode("TEN", 10)
    assert p.min_subtotal == D("0") and p.max_discount is None
    assert p.excluded_skus == frozenset() and p.valid_from is None and p.valid_to is None
    c = cart(("a", "100.00", 1), ("b", "100.00", 1), promo=p)
    assert c.total() == D("180.00")
    assert c.total(TODAY) == D("180.00")


def test_no_promo_discount_is_zero():
    assert cart(("a", "10", 1)).discount(TODAY) == D("0")


# ---- исключённые товары

def test_excluded_skus_not_discounted():
    c = cart(("a", "100.00", 1), ("b", "50.00", 2), promo=PromoCode("P", 10, excluded_skus={"b"}))
    assert c.discount(TODAY) == D("10.00")
    assert c.total(TODAY) == D("190.00")


def test_excluded_skus_any_iterable_stored_as_frozenset():
    p = PromoCode("P", 10, excluded_skus=["a", "b", "a"])
    assert isinstance(p.excluded_skus, frozenset) and p.excluded_skus == frozenset({"a", "b"})
    assert hash(p) == hash(PromoCode("P", 10, excluded_skus=("b", "a")))
    assert p == PromoCode("P", 10, excluded_skus={"a", "b"})
    with pytest.raises(Exception):
        p.percent = 20  # неизменяемый


def test_all_items_excluded():
    c = cart(("a", "100.00", 1), promo=PromoCode("P", 50, excluded_skus=["a"]))
    assert c.discount(TODAY) == D("0") and c.total(TODAY) == D("100.00")


# ---- минимальная база

def test_min_subtotal_uses_discount_base_not_full_subtotal():
    # subtotal 300 >= 200, но скидочная база 100 < 200 — скидки нет
    p = PromoCode("P", 10, min_subtotal=D("200"), excluded_skus={"b"})
    c = cart(("a", "100.00", 1), ("b", "200.00", 1), promo=p)
    assert c.discount(TODAY) == D("0")


def test_min_subtotal_boundary_inclusive():
    p = PromoCode("P", 10, min_subtotal=D("100.00"))
    assert cart(("a", "100.00", 1), promo=p).discount(TODAY) == D("10.00")
    assert cart(("a", "99.99", 1), promo=p).discount(TODAY) == D("0")


# ---- потолок и порядок округления

def test_max_discount_caps():
    p = PromoCode("P", 50, max_discount=D("30"))
    c = cart(("a", "100.00", 1), promo=p)
    assert c.discount(TODAY) == D("30")
    assert c.total(TODAY) == D("70.00")


def test_round_before_cap():
    # 0.15 * 10% = 0.015 -> money() = 0.02; потолок 0.015 срабатывает только после округления
    p = PromoCode("P", 10, max_discount=D("0.015"))
    c = cart(("a", "0.15", 1), promo=p)
    assert c.discount(TODAY) == D("0.015")
    assert c.total(TODAY) == D("0.14")  # money(0.15 - 0.015) = money(0.135) = 0.14


def test_discount_rounded_half_up():
    c = cart(("a", "0.05", 1), promo=PromoCode("P", 10))
    assert c.discount(TODAY) == D("0.01")  # 0.005 -> 0.01
    assert c.total(TODAY) == D("0.04")


def test_cap_not_applied_when_discount_smaller():
    p = PromoCode("P", 10, max_discount=D("1000"))
    assert cart(("a", "100.00", 1), promo=p).discount(TODAY) == D("10.00")


# ---- срок действия

@pytest.mark.parametrize("today, expected", [
    (date(2026, 10, 1), D("10.00")),    # первый день
    (date(2026, 10, 31), D("10.00")),   # последний день
    (date(2026, 9, 30), D("0")),
    (date(2026, 11, 1), D("0")),
])
def test_validity_inclusive(today, expected):
    p = PromoCode("P", 10, valid_from=date(2026, 10, 1), valid_to=date(2026, 10, 31))
    assert cart(("a", "100.00", 1), promo=p).discount(today) == expected


def test_open_ended_validity():
    p = PromoCode("P", 10, valid_from=date(2026, 10, 1))
    c = cart(("a", "100.00", 1), promo=p)
    assert c.discount(date(2030, 1, 1)) == D("10.00")
    assert c.discount(date(2026, 9, 1)) == D("0")


def test_today_defaults_to_current_date():
    p = PromoCode("P", 10, valid_to=date(2000, 1, 1))  # давно истёк
    c = cart(("a", "100.00", 1), promo=p)
    assert c.discount() == D("0")
    assert c.total() == D("100.00")


# ---- валидация

@pytest.mark.parametrize("kwargs", [
    {"min_subtotal": D("-1")},
    {"max_discount": D("0")},
    {"max_discount": D("-5")},
    {"valid_from": date(2026, 2, 1), "valid_to": date(2026, 1, 1)},
])
def test_validation(kwargs):
    with pytest.raises(ValueError):
        PromoCode("P", 10, **kwargs)


def test_same_day_validity_allowed():
    PromoCode("P", 10, valid_from=TODAY, valid_to=TODAY)
