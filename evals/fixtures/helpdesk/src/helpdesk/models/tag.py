"""Тег для группировки тикетов."""
from dataclasses import dataclass


@dataclass
class Tag:
    id: int
    name: str
    slug: str
