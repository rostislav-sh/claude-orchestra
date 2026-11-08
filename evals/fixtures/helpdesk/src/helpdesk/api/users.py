"""HTTP-эндпоинты пользователей."""
from __future__ import annotations

from helpdesk.api.router import Request, Router
from helpdesk.api.serializers import user_to_dict
from helpdesk.models.user import Role


def register(router: Router, services) -> None:
    @router.post("/users")
    def create_user(req: Request):
        return user_to_dict(services.users.create(req.body["name"], req.body["email"], Role(req.body.get("role", "customer"))))

    @router.get("/users/staff")
    def list_staff(req: Request):
        return [user_to_dict(u) for u in services.users.staff()]
