from helpdesk.models.ticket import Priority, Status
from helpdesk.models.user import Role


def test_escalates_overdue(services, customer, clock):
    t = services.tickets.create("A", "", customer.id, Priority.HIGH)
    clock.advance(hours=4)
    assert services.tickets.escalate_overdue() == [t.id]
    assert services.tickets.get(t.id).status == Status.ESCALATED


def test_skips_normal(services, customer, clock):
    services.tickets.create("A", "", customer.id, Priority.NORMAL)
    clock.advance(hours=10)
    assert services.tickets.escalate_overdue() == []


def test_skips_fresh(services, customer, clock):
    services.tickets.create("A", "", customer.id, Priority.URGENT)
    clock.advance(hours=3)
    assert services.tickets.escalate_overdue() == []


def test_notifies_managers(services, customer, clock, notifier):
    m = services.users.create("Вера", "vera@example.com", Role.MANAGER)
    services.tickets.create("A", "", customer.id, Priority.HIGH)
    clock.advance(hours=5)
    services.tickets.escalate_overdue()
    assert notifier.sent[-1].to == m.email


def test_audited(services, customer, clock):
    t = services.tickets.create("A", "", customer.id, Priority.HIGH)
    clock.advance(hours=5)
    services.tickets.escalate_overdue()
    assert services.audit.list(t.id)[-1].action == "escalated"
