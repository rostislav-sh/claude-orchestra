"""Комментарии к тикетам, включая внутренние заметки сотрудников."""
from __future__ import annotations

from typing import List

from helpdesk.core.clock import Clock
from helpdesk.core.errors import ValidationError
from helpdesk.models.comment import Comment
from helpdesk.repositories.comments import CommentRepository
from helpdesk.repositories.tickets import TicketRepository
from helpdesk.services.audit import AuditService


class CommentService:
    def __init__(self, repo: CommentRepository, tickets: TicketRepository, audit: AuditService, clock: Clock) -> None:
        self.repo, self.tickets, self.audit, self.clock = repo, tickets, audit, clock

    def add(self, ticket_id: int, author_id: int, text: str, internal: bool = False) -> Comment:
        if not text.strip():
            raise ValidationError("empty comment")
        self.tickets.get(ticket_id)
        comment = self.repo.add(Comment(self.repo.next_id(), ticket_id, author_id, text.strip(), self.clock.now(), internal))
        self.audit.record(f"user:{author_id}", "commented", ticket_id)
        return comment

    def visible(self, ticket_id: int, staff: bool) -> List[Comment]:
        return [c for c in self.repo.for_ticket(ticket_id) if staff or not c.internal]
