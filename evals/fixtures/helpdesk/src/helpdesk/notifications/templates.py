"""Шаблоны писем по событиям тикета."""
from __future__ import annotations

from typing import Tuple

from helpdesk.models.ticket import Ticket

TEMPLATES = {
    "assigned": ("Вам назначен тикет #{id}", "Тикет «{title}» назначен вам. Приоритет: {priority}."),
    "status_changed": ("Тикет #{id}: статус {status}", "Статус тикета «{title}» изменён на {status}."),
}


def render(name: str, ticket: Ticket) -> Tuple[str, str]:
    subject, body = TEMPLATES[name]
    values = {"id": ticket.id, "title": ticket.title, "priority": ticket.priority.value, "status": ticket.status.value}
    return subject.format(**values), body.format(**values)
