"""Генератор последовательных идентификаторов по типам сущностей."""
from collections import defaultdict


class IdGenerator:
    def __init__(self) -> None:
        self._counters = defaultdict(int)

    def next(self, kind: str) -> int:
        self._counters[kind] += 1
        return self._counters[kind]
