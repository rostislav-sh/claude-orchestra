"""Сборка всех сервисов приложения с общими зависимостями."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from helpdesk.core.clock import Clock, SystemClock
from helpdesk.core.config import Settings
from helpdesk.core.ids import IdGenerator
from helpdesk.notifications.base import Notifier
from helpdesk.notifications.memory import InMemoryNotifier
from helpdesk.repositories.audit import AuditRepository
from helpdesk.repositories.comments import CommentRepository
from helpdesk.repositories.tags import TagRepository
from helpdesk.repositories.tickets import TicketRepository
from helpdesk.repositories.users import UserRepository
from helpdesk.services.audit import AuditService
from helpdesk.services.comments import CommentService
from helpdesk.services.reports import ReportService
from helpdesk.services.search import SearchService
from helpdesk.services.tags import TagService
from helpdesk.services.tickets import TicketService
from helpdesk.services.users import UserService


@dataclass
class Services:
    settings: Settings
    clock: Clock
    notifier: Notifier
    users: UserService
    tickets: TicketService
    comments: CommentService
    tags: TagService
    audit: AuditService
    search: SearchService
    reports: ReportService


def build_services(clock: Optional[Clock] = None, notifier: Optional[Notifier] = None,
                   settings: Optional[Settings] = None) -> Services:
    settings = settings or Settings()
    clock = clock or SystemClock()
    notifier = notifier or InMemoryNotifier()
    ids = IdGenerator()
    user_repo, ticket_repo = UserRepository(ids), TicketRepository(ids)
    audit = AuditService(AuditRepository(ids), clock)
    users = UserService(user_repo)
    tickets = TicketService(ticket_repo, user_repo, audit, notifier, clock, settings)
    comments = CommentService(CommentRepository(ids), ticket_repo, audit, clock)
    tags = TagService(TagRepository(ids), ticket_repo)
    return Services(settings, clock, notifier, users, tickets, comments, tags, audit,
                    SearchService(ticket_repo), ReportService(ticket_repo, clock))
