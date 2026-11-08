"""Пользователь и роли."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    MANAGER = "manager"


@dataclass
class User:
    id: int
    name: str
    email: str
    role: Role
    active: bool = True
