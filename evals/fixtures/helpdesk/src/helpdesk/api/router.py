"""Минимальный HTTP-роутер без зависимостей."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

from helpdesk.core.errors import Forbidden, NotFound, ValidationError


@dataclass
class Request:
    params: Dict[str, Any] = field(default_factory=dict)
    body: Dict[str, Any] = field(default_factory=dict)
    user_id: int = 0


@dataclass
class Response:
    status: int
    body: Any


class Router:
    def __init__(self) -> None:
        self._routes: Dict[Tuple[str, str], Callable[[Request], Any]] = {}

    def _add(self, method: str, path: str):
        def deco(fn):
            self._routes[(method, path)] = fn
            return fn
        return deco

    def get(self, path: str):
        return self._add("GET", path)

    def post(self, path: str):
        return self._add("POST", path)

    def routes(self) -> List[Tuple[str, str]]:
        return sorted(self._routes)

    def handle(self, method: str, path: str, request: Request) -> Response:
        fn = self._routes.get((method, path))
        if fn is None:
            return Response(404, {"error": "route not found"})
        try:
            return Response(200, fn(request))
        except NotFound as exc:
            return Response(404, {"error": str(exc)})
        except Forbidden as exc:
            return Response(403, {"error": str(exc)})
        except ValidationError as exc:
            return Response(422, {"error": str(exc)})
