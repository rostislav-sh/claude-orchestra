from decimal import Decimal

import pytest

from shop.cart import Cart
from shop.models import Item, PromoCode


def test_total_without_promo():
    cart = Cart()
    cart.add(Item("a", "Чай", Decimal("100.00"), 2))
    cart.add(Item("b", "Кофе", Decimal("50.50")))
    assert cart.total() == Decimal("250.50")


def test_promo_single_item():
    cart = Cart()
    cart.add(Item("a", "Чай", Decimal("100.00")))
    cart.apply_promo(PromoCode("TEA10", 10))
    assert cart.total() == Decimal("90.00")


def test_add_rejects_zero_qty():
    with pytest.raises(ValueError):
        Cart().add(Item("a", "Чай", Decimal("1"), 0))
