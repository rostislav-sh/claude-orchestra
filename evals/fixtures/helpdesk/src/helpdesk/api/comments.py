"""HTTP-эндпоинты комментариев."""
from __future__ import annotations

from helpdesk.api.router import Request, Router
from helpdesk.api.serializers import comment_to_dict


def register(router: Router, services) -> None:
    @router.post("/comments")
    def add_comment(req: Request):
        c = services.comments.add(int(req.body["ticket_id"]), req.user_id, req.body["text"],
                                  bool(req.body.get("internal", False)))
        return comment_to_dict(c)
