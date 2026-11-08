"""Запись действий в журнал аудита."""
from __future__ import annotations

from typing import Dict, List, Optional

from helpdesk.core.clock import Clock
from helpdesk.models.audit import AuditEntry
from helpdesk.repositories.audit import AuditRepository


class AuditService:
    def __init__(self, repo: AuditRepository, clock: Clock) -> None:
        self.repo, self.clock = repo, clock

    def record(self, actor: str, action: str, ticket_id: Optional[int] = None,
               details: Optional[Dict[str, str]] = None) -> AuditEntry:
        entry = AuditEntry(self.repo.next_id(), self.clock.now(), actor, action, ticket_id, dict(details or {}))
        return self.repo.add(entry)

    def list(self, ticket_id: Optional[int] = None) -> List[AuditEntry]:
        return self.repo.for_ticket(ticket_id)
