"""Интерфейс отправки уведомлений."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Notification:
    to: str
    subject: str
    body: str


class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...
