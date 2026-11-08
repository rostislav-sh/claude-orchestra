"""Теги тикетов."""
from __future__ import annotations

from typing import List

from helpdesk.core.text import slugify
from helpdesk.models.tag import Tag
from helpdesk.repositories.tags import TagRepository
from helpdesk.repositories.tickets import TicketRepository


class TagService:
    def __init__(self, repo: TagRepository, tickets: TicketRepository) -> None:
        self.repo, self.tickets = repo, tickets

    def ensure(self, name: str) -> Tag:
        slug = slugify(name)
        return self.repo.by_slug(slug) or self.repo.add(Tag(self.repo.next_id(), name.strip(), slug))

    def tag(self, ticket_id: int, name: str) -> None:
        self.tickets.get(ticket_id).tags.add(self.ensure(name).slug)

    def tickets_with(self, name: str) -> List[int]:
        slug = slugify(name)
        return sorted(t.id for t in self.tickets.all() if slug in t.tags)
