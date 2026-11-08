"""Регистрация и поиск пользователей."""
from __future__ import annotations

from typing import List

from helpdesk.core.errors import ValidationError
from helpdesk.models.user import Role, User
from helpdesk.repositories.users import UserRepository


class UserService:
    def __init__(self, repo: UserRepository) -> None:
        self.repo = repo

    def create(self, name: str, email: str, role: Role = Role.CUSTOMER) -> User:
        if "@" not in email:
            raise ValidationError("invalid email")
        if self.repo.by_email(email):
            raise ValidationError("email already registered")
        return self.repo.add(User(self.repo.next_id(), name.strip(), email.strip(), role))

    def deactivate(self, user_id: int) -> User:
        user = self.repo.get(user_id)
        user.active = False
        return user

    def staff(self) -> List[User]:
        return self.repo.by_role(Role.AGENT) + self.repo.by_role(Role.MANAGER)
