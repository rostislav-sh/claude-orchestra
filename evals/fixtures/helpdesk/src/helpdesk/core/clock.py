"""Источник времени: системный и фиксированный для тестов."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now().replace(microsecond=0)


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at = self.at + timedelta(**delta)
