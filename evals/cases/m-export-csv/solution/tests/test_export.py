import csv
from datetime import datetime
from decimal import Decimal

from shop.export import export_orders_csv
from shop.models import Item, Order, PromoCode


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f, delimiter=";"))


def test_header(tmp_path):
    export_orders_csv([], tmp_path / "a.csv")
    assert rows(tmp_path / "a.csv") == [["id", "created_at", "items", "total", "promo"]]


def test_row(tmp_path):
    o = Order(1, datetime(2026, 1, 1, 10, 0, 0, 5), [Item("a", "Чай", Decimal("10"), 2)], PromoCode("P", 50))
    assert export_orders_csv([o], tmp_path / "a.csv") == 1
    assert rows(tmp_path / "a.csv")[1] == ["1", "2026-01-01T10:00:00", "2", "10.00", "P"]


def test_count(tmp_path):
    o = Order(1, datetime(2026, 1, 1), [Item("a", "Чай", Decimal("1"))])
    assert export_orders_csv([o, o], tmp_path / "a.csv") == 2


def test_escaping(tmp_path):
    o = Order(1, datetime(2026, 1, 1), [Item("a", "Чай", Decimal("1"))], PromoCode("A;B", 10))
    export_orders_csv([o], tmp_path / "a.csv")
    assert rows(tmp_path / "a.csv")[1][4] == "A;B"
