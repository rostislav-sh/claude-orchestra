"""Жизненный цикл тикета: создание, назначение, смена статуса."""
from __future__ import annotations

from typing import Dict, List, Set

from helpdesk.core.clock import Clock
from helpdesk.core.config import Settings
from helpdesk.core.errors import Forbidden, InvalidTransition, ValidationError
from helpdesk.models.ticket import Priority, Status, Ticket
from helpdesk.models.user import Role
from helpdesk.notifications.base import Notification, Notifier
from helpdesk.notifications.templates import render
from helpdesk.repositories.tickets import TicketRepository
from helpdesk.repositories.users import UserRepository
from helpdesk.services.audit import AuditService

TRANSITIONS: Dict[Status, Set[Status]] = {
    Status.OPEN: {Status.IN_PROGRESS, Status.CLOSED},
    Status.IN_PROGRESS: {Status.RESOLVED, Status.OPEN},
    Status.RESOLVED: {Status.CLOSED, Status.OPEN},
    Status.CLOSED: set(),
}


class TicketService:
    def __init__(self, repo: TicketRepository, users: UserRepository, audit: AuditService,
                 notifier: Notifier, clock: Clock, settings: Settings) -> None:
        self.repo, self.users, self.audit = repo, users, audit
        self.notifier, self.clock, self.settings = notifier, clock, settings

    def create(self, title: str, description: str, author_id: int,
               priority: Priority = Priority.NORMAL) -> Ticket:
        title = title.strip()
        if not title:
            raise ValidationError("title is required")
        if len(title) > self.settings.max_title_length:
            raise ValidationError("title is too long")
        self.users.get(author_id)
        ticket = self.repo.add(Ticket(self.repo.next_id(), title, description, author_id, priority, self.clock.now()))
        self.audit.record(f"user:{author_id}", "created", ticket.id, {"priority": priority.value})
        return ticket

    def get(self, ticket_id: int) -> Ticket:
        return self.repo.get(ticket_id)

    def assign(self, ticket_id: int, agent_id: int, actor_id: int) -> Ticket:
        ticket, agent = self.repo.get(ticket_id), self.users.get(agent_id)
        if agent.role == Role.CUSTOMER or not agent.active:
            raise Forbidden("only active staff can be assigned")
        ticket.assignee_id = agent.id
        ticket.updated_at = self.clock.now()
        self.audit.record(f"user:{actor_id}", "assigned", ticket.id, {"agent": str(agent.id)})
        self._notify(agent.email, "assigned", ticket)
        return ticket

    def change_status(self, ticket_id: int, status: Status, actor_id: int) -> Ticket:
        ticket = self.repo.get(ticket_id)
        if status not in TRANSITIONS[ticket.status]:
            raise InvalidTransition(f"{ticket.status.value} -> {status.value}")
        old, ticket.status = ticket.status, status
        ticket.updated_at = self.clock.now()
        self.audit.record(f"user:{actor_id}", "status_changed", ticket.id, {"from": old.value, "to": status.value})
        author = self.users.get(ticket.author_id)
        self._notify(author.email, "status_changed", ticket)
        return ticket

    def list_open(self) -> List[Ticket]:
        return sorted(self.repo.by_status(Status.OPEN, Status.IN_PROGRESS), key=lambda t: t.id)

    def _notify(self, to: str, template: str, ticket: Ticket) -> None:
        subject, body = render(template, ticket)
        self.notifier.send(Notification(to=to, subject=subject, body=body))
