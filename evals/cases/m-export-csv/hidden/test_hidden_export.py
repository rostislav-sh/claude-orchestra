import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from shop.export import export_orders_csv
from shop.models import Item, Order, PromoCode

HEADER = ["id", "created_at", "items", "total", "promo"]


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f, delimiter=";"))


def order(oid, *items, promo=None, at=None):
    return Order(oid, at or datetime(2026, 10, 29, 14, 5, 0, 123456), list(items), promo)


def test_header_and_rows(tmp_path):
    path = tmp_path / "orders.csv"
    n = export_orders_csv([
        order(1, Item("a", "Чай", Decimal("100.00"), 2), Item("b", "Кофе", Decimal("50.50"))),
        order(2, Item("c", "Сок", Decimal("100.00")), Item("d", "Вода", Decimal("100.00")), promo=PromoCode("TEN", 10)),
    ], path)
    assert n == 2
    rows = read(path)
    assert rows[0] == HEADER
    assert rows[1] == ["1", "2026-10-29T14:05:00", "3", "250.50", ""]
    assert rows[2] == ["2", "2026-10-29T14:05:00", "2", "180.00", "TEN"]


def test_empty_list_writes_header_only(tmp_path):
    path = tmp_path / "empty.csv"
    assert export_orders_csv([], path) == 0
    assert read(path) == [HEADER]


def test_accepts_str_path_and_overwrites(tmp_path):
    path = tmp_path / "o.csv"
    path.write_text("old content\nmore\nlines\n", encoding="utf-8")
    assert export_orders_csv([order(7, Item("a", "Чай", Decimal("1.005")))], str(path)) == 1
    rows = read(path)
    assert len(rows) == 2
    assert rows[1][3] == "1.01"


def test_semicolon_in_promo_is_escaped(tmp_path):
    path = tmp_path / "o.csv"
    export_orders_csv([order(3, Item("a", "Чай", Decimal("10")), promo=PromoCode('A;"B"', 50))], path)
    rows = read(path)
    assert rows[1][4] == 'A;"B"'
    assert rows[1][3] == "5.00"


def test_timezone_kept(tmp_path):
    path = tmp_path / "o.csv"
    tz = timezone(timedelta(hours=3))
    export_orders_csv([order(4, Item("a", "Чай", Decimal("1")), at=datetime(2026, 1, 2, 3, 4, 5, 999, tzinfo=tz))], path)
    assert read(path)[1][1] == "2026-01-02T03:04:05+03:00"


def test_utf8_and_generator_input(tmp_path):
    path = tmp_path / "o.csv"
    gen = (order(i, Item("a", "Чай", Decimal("1"))) for i in range(3))
    assert export_orders_csv(gen, path) == 3
    assert Path(path).read_bytes().decode("utf-8").startswith("id;created_at")
