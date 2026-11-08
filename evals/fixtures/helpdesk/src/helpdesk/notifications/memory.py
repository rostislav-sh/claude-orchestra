"""Уведомления в память — для тестов и локального запуска."""
from __future__ import annotations

from typing import List

from helpdesk.notifications.base import Notification


class InMemoryNotifier:
    def __init__(self) -> None:
        self.sent: List[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)
