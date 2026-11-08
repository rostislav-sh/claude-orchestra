"""Запись журнала аудита."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class AuditEntry:
    id: int
    at: datetime
    actor: str
    action: str
    ticket_id: Optional[int] = None
    details: Dict[str, str] = field(default_factory=dict)
