from decimal import Decimal

from shop.cart import Cart
from shop.models import Item, PromoCode


def test_promo_applied_once_for_several_items():
    cart = Cart()
    cart.add(Item("a", "Чай", Decimal("100.00")))
    cart.add(Item("b", "Кофе", Decimal("100.00")))
    cart.apply_promo(PromoCode("TEN", 10))
    assert cart.total() == Decimal("180.00")
