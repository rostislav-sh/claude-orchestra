"""Хранилище: tickets."""
from __future__ import annotations

from typing import List, Optional

from helpdesk.models.ticket import Status, Ticket
from helpdesk.repositories.base import InMemoryRepository


class TicketRepository(InMemoryRepository[Ticket]):
    kind = "ticket"

    def by_status(self, *statuses: Status) -> List[Ticket]:
        return self.filter(lambda t: t.status in statuses)
    
    def by_assignee(self, user_id: int) -> List[Ticket]:
        return self.filter(lambda t: t.assignee_id == user_id)
