from datetime import datetime

import pytest

from helpdesk.bootstrap import build_services
from helpdesk.core.clock import FixedClock
from helpdesk.models.user import Role
from helpdesk.notifications.memory import InMemoryNotifier


@pytest.fixture
def clock():
    """Фиксированное время 2026-11-02 10:00."""
    return FixedClock(datetime(2026, 11, 2, 10, 0))


@pytest.fixture
def notifier():
    return InMemoryNotifier()


@pytest.fixture
def services(clock, notifier):
    """Все сервисы с фиксированным временем и уведомлениями в память."""
    return build_services(clock=clock, notifier=notifier)


@pytest.fixture
def customer(services):
    return services.users.create("Анна", "anna@example.com", Role.CUSTOMER)


@pytest.fixture
def agent(services):
    return services.users.create("Борис", "boris@example.com", Role.AGENT)
