"""Поиск тикетов по тексту и фильтрам."""
from __future__ import annotations

from typing import List, Optional

from helpdesk.models.ticket import Priority, Status, Ticket
from helpdesk.repositories.tickets import TicketRepository


class SearchService:
    def __init__(self, tickets: TicketRepository) -> None:
        self.tickets = tickets

    def search(self, text: str = "", status: Optional[Status] = None,
               priority: Optional[Priority] = None) -> List[Ticket]:
        needle = text.lower().strip()

        def match(t: Ticket) -> bool:
            return ((not needle or needle in t.title.lower() or needle in t.description.lower())
                    and (status is None or t.status == status)
                    and (priority is None or t.priority == priority))

        return sorted(self.tickets.filter(match), key=lambda t: t.id)
