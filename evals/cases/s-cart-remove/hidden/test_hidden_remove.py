from decimal import Decimal

import pytest

from shop.cart import Cart
from shop.models import Item


def make():
    cart = Cart()
    cart.add(Item("a", "Чай", Decimal("10"), 3))
    cart.add(Item("b", "Кофе", Decimal("20"), 1))
    cart.add(Item("a", "Чай", Decimal("10"), 5))
    return cart


def skus(cart):
    return [(i.sku, i.qty) for i in cart.items]


def test_remove_whole_first_position():
    cart = make()
    cart.remove("a")
    assert skus(cart) == [("b", 1), ("a", 5)]


def test_remove_part_of_quantity():
    cart = make()
    cart.remove("a", 2)
    assert skus(cart) == [("a", 1), ("b", 1), ("a", 5)]


def test_remove_exact_quantity_deletes_position():
    cart = make()
    cart.remove("a", 3)
    assert skus(cart) == [("b", 1), ("a", 5)]


def test_remove_more_than_quantity_deletes_position():
    cart = make()
    cart.remove("b", 10)
    assert skus(cart) == [("a", 3), ("a", 5)]


def test_missing_sku():
    with pytest.raises(KeyError):
        make().remove("zzz")


@pytest.mark.parametrize("qty", [0, -1])
def test_bad_qty(qty):
    cart = make()
    with pytest.raises(ValueError):
        cart.remove("a", qty)
    assert skus(cart) == [("a", 3), ("b", 1), ("a", 5)]


def test_total_follows_removal():
    cart = make()
    cart.remove("a", 1)
    assert cart.total() == Decimal("90.00")
