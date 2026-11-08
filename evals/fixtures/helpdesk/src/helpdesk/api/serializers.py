"""Преобразование моделей в JSON-совместимые словари."""
from __future__ import annotations

from typing import Any, Dict

from helpdesk.models.comment import Comment
from helpdesk.models.ticket import Ticket
from helpdesk.models.user import User


def ticket_to_dict(t: Ticket) -> Dict[str, Any]:
    return {"id": t.id, "title": t.title, "status": t.status.value, "priority": t.priority.value,
            "assignee_id": t.assignee_id, "created_at": t.created_at.isoformat(), "tags": sorted(t.tags)}


def user_to_dict(u: User) -> Dict[str, Any]:
    return {"id": u.id, "name": u.name, "role": u.role.value}


def comment_to_dict(c: Comment) -> Dict[str, Any]:
    return {"id": c.id, "author_id": c.author_id, "text": c.text, "internal": c.internal}
