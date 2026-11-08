"""Сборка роутера со всеми эндпоинтами."""
from __future__ import annotations

from helpdesk.api import comments, reports, tickets, users
from helpdesk.api.router import Router


def build_router(services) -> Router:
    router = Router()
    for module in (tickets, users, comments, reports):
        module.register(router, services)
    return router
