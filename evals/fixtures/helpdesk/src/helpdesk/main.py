"""Точка входа: собирает сервисы и печатает маршруты API."""
from helpdesk.api.app import build_router
from helpdesk.bootstrap import build_services


def main() -> None:
    router = build_router(build_services())
    for method, path in router.routes():
        print(method, path)


if __name__ == "__main__":
    main()
