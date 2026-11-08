"""Хранилище: users."""
from __future__ import annotations

from typing import List, Optional

from helpdesk.models.user import Role, User
from helpdesk.repositories.base import InMemoryRepository


class UserRepository(InMemoryRepository[User]):
    kind = "user"

    def by_email(self, email: str) -> Optional[User]:
        found = self.filter(lambda u: u.email.lower() == email.lower())
        return found[0] if found else None
    
    def by_role(self, role: Role) -> List[User]:
        return self.filter(lambda u: u.role == role and u.active)
