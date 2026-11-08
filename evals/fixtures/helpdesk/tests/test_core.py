import pytest

from helpdesk.core.pagination import paginate
from helpdesk.core.text import slugify, truncate


def test_slugify():
    assert slugify("Оплата и Счета!") == "оплата-и-счета"


def test_truncate():
    assert truncate("abcdef", 4) == "abc…"


def test_paginate():
    page = paginate(list(range(5)), 2, 2)
    assert page.items == [2, 3] and page.total == 5


def test_paginate_bad():
    with pytest.raises(ValueError):
        paginate([], 0, 1)
