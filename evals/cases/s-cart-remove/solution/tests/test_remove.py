from decimal import Decimal

import pytest

from shop.cart import Cart
from shop.models import Item


def test_remove_whole():
    cart = Cart()
    cart.add(Item("a", "Чай", Decimal("1"), 2))
    cart.remove("a")
    assert cart.items == []


def test_remove_part():
    cart = Cart()
    cart.add(Item("a", "Чай", Decimal("1"), 2))
    cart.remove("a", 1)
    assert cart.items[0].qty == 1


def test_remove_missing():
    with pytest.raises(KeyError):
        Cart().remove("a")
