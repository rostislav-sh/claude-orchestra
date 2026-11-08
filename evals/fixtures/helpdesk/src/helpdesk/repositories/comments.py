"""Хранилище: comments."""
from __future__ import annotations

from typing import List, Optional

from helpdesk.models.comment import Comment
from helpdesk.repositories.base import InMemoryRepository


class CommentRepository(InMemoryRepository[Comment]):
    kind = "comment"

    def for_ticket(self, ticket_id: int) -> List[Comment]:
        return sorted(self.filter(lambda c: c.ticket_id == ticket_id), key=lambda c: c.id)
