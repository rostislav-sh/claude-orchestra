"""Хранилище записей аудита (только добавление)."""
from __future__ import annotations

from typing import List, Optional

from helpdesk.models.audit import AuditEntry
from helpdesk.repositories.base import InMemoryRepository


class AuditRepository(InMemoryRepository[AuditEntry]):
    kind = "audit"

    def for_ticket(self, ticket_id: Optional[int]) -> List[AuditEntry]:
        items = self.all() if ticket_id is None else self.filter(lambda e: e.ticket_id == ticket_id)
        return sorted(items, key=lambda e: e.id)
