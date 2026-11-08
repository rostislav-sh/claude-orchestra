"""Ошибки предметной области."""


class HelpdeskError(Exception):
    """Базовая ошибка сервиса."""


class NotFound(HelpdeskError):
    pass


class Forbidden(HelpdeskError):
    pass


class ValidationError(HelpdeskError):
    pass


class InvalidTransition(HelpdeskError):
    """Недопустимая смена статуса тикета."""
