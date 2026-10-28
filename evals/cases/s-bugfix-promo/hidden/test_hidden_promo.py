from decimal import Decimal

from shop.cart import Cart
from shop.models import Item, PromoCode


def cart_of(*prices, promo=None):
    cart = Cart()
    for i, p in enumerate(prices):
        cart.add(Item(f"sku{i}", f"Товар {i}", Decimal(p)))
    if promo:
        cart.apply_promo(PromoCode("P", promo))
    return cart


def test_two_items_discount_applied_once():
    assert cart_of("100.00", "100.00", promo=10).total() == Decimal("180.00")


def test_three_items_with_rounding():
    assert cart_of("33.33", "33.33", "33.33", promo=15).total() == Decimal("84.99")


def test_single_item_still_works():
    assert cart_of("100.00", promo=10).total() == Decimal("90.00")


def test_many_items_without_promo():
    assert cart_of("10.10", "20.20", "30.30").total() == Decimal("60.60")


def test_full_discount():
    assert cart_of("10.00", "5.00", promo=100).total() == Decimal("0.00")


def test_quantity_counts_with_promo():
    cart = Cart()
    cart.add(Item("a", "Чай", Decimal("100.00"), 3))
    cart.add(Item("b", "Кофе", Decimal("50.00"), 2))
    cart.apply_promo(PromoCode("P", 20))
    assert cart.total() == Decimal("320.00")
