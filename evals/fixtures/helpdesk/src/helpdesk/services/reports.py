"""Сводные отчёты по тикетам."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Dict

from helpdesk.core.clock import Clock
from helpdesk.repositories.tickets import TicketRepository


class ReportService:
    def __init__(self, tickets: TicketRepository, clock: Clock) -> None:
        self.tickets, self.clock = tickets, clock

    def by_status(self) -> Dict[str, int]:
        return dict(Counter(t.status.value for t in self.tickets.all()))

    def created_last_days(self, days: int) -> int:
        since = self.clock.now() - timedelta(days=days)
        return sum(1 for t in self.tickets.all() if t.created_at >= since)
