"""HTTP-эндпоинты тикетов."""
from __future__ import annotations

from helpdesk.api.router import Request, Router
from helpdesk.api.serializers import ticket_to_dict
from helpdesk.models.ticket import Priority, Status


def register(router: Router, services) -> None:
    @router.post("/tickets")
    def create_ticket(req: Request):
        t = services.tickets.create(req.body["title"], req.body.get("description", ""), req.user_id,
                                    Priority(req.body.get("priority", "normal")))
        return ticket_to_dict(t)

    @router.get("/tickets")
    def list_tickets(req: Request):
        return [ticket_to_dict(t) for t in services.tickets.list_open()]

    @router.post("/tickets/status")
    def change_status(req: Request):
        t = services.tickets.change_status(int(req.body["id"]), Status(req.body["status"]), req.user_id)
        return ticket_to_dict(t)
