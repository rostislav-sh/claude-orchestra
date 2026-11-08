import pytest

from helpdesk.core.errors import Forbidden, InvalidTransition, ValidationError
from helpdesk.models.ticket import Priority, Status


def test_create_ticket(services, customer, clock):
    t = services.tickets.create("Не работает вход", "...", customer.id, Priority.HIGH)
    assert t.status == Status.OPEN and t.created_at == clock.now()


def test_title_required(services, customer):
    with pytest.raises(ValidationError):
        services.tickets.create("  ", "", customer.id)


def test_assign_notifies_agent(services, customer, agent, notifier):
    t = services.tickets.create("A", "", customer.id)
    services.tickets.assign(t.id, agent.id, agent.id)
    assert notifier.sent[-1].to == agent.email


def test_customer_cannot_be_assigned(services, customer):
    t = services.tickets.create("A", "", customer.id)
    with pytest.raises(Forbidden):
        services.tickets.assign(t.id, customer.id, customer.id)


def test_invalid_transition(services, customer):
    t = services.tickets.create("A", "", customer.id)
    with pytest.raises(InvalidTransition):
        services.tickets.change_status(t.id, Status.RESOLVED, customer.id)


def test_list_open(services, customer, agent):
    a = services.tickets.create("A", "", customer.id)
    b = services.tickets.create("B", "", customer.id)
    services.tickets.change_status(b.id, Status.CLOSED, agent.id)
    assert [t.id for t in services.tickets.list_open()] == [a.id]
