import pytest

from helpdesk.core.errors import ValidationError
from helpdesk.models.user import Role


def test_duplicate_email(services, customer):
    with pytest.raises(ValidationError):
        services.users.create("Другая", "ANNA@example.com")


def test_staff(services, customer, agent):
    manager = services.users.create("Вера", "vera@example.com", Role.MANAGER)
    assert {u.id for u in services.users.staff()} == {agent.id, manager.id}
