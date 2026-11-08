"""Комментарий к тикету."""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Comment:
    id: int
    ticket_id: int
    author_id: int
    text: str
    created_at: datetime
    internal: bool = False
