"""Хранилище: tags."""
from __future__ import annotations

from typing import List, Optional

from helpdesk.models.tag import Tag
from helpdesk.repositories.base import InMemoryRepository


class TagRepository(InMemoryRepository[Tag]):
    kind = "tag"

    def by_slug(self, slug: str) -> Optional[Tag]:
        found = self.filter(lambda t: t.slug == slug)
        return found[0] if found else None
