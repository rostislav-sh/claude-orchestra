"""HTTP-эндпоинты отчётов."""
from __future__ import annotations

from helpdesk.api.router import Request, Router


def register(router: Router, services) -> None:
    @router.get("/reports/status")
    def status_report(req: Request):
        return services.reports.by_status()
