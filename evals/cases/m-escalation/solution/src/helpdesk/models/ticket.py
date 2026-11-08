"""Тикет и его статусы."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Set


class Status(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ESCALATED = "escalated"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Ticket:
    id: int
    title: str
    description: str
    author_id: int
    priority: Priority
    created_at: datetime
    status: Status = Status.OPEN
    assignee_id: Optional[int] = None
    updated_at: Optional[datetime] = None
    tags: Set[str] = field(default_factory=set)
