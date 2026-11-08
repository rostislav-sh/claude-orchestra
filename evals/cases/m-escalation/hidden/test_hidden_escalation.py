from datetime import datetime, timedelta

import pytest

from helpdesk.bootstrap import build_services
from helpdesk.core.clock import FixedClock
from helpdesk.core.config import Settings
from helpdesk.core.errors import InvalidTransition
from helpdesk.models.ticket import Priority, Status
from helpdesk.models.user import Role
from helpdesk.notifications.memory import InMemoryNotifier

T0 = datetime(2026, 11, 2, 10, 0)


@pytest.fixture
def env():
    clock, notifier = FixedClock(T0), InMemoryNotifier()
    s = build_services(clock=clock, notifier=notifier)
    customer = s.users.create("Анна", "anna@example.com", Role.CUSTOMER)
    agent = s.users.create("Борис", "boris@example.com", Role.AGENT)
    m1 = s.users.create("Вера", "vera@example.com", Role.MANAGER)
    m2 = s.users.create("Глеб", "gleb@example.com", Role.MANAGER)
    return s, clock, notifier, customer, agent, m1, m2


def test_default_setting():
    assert Settings().escalation_hours == 4


def test_escalates_only_matching(env):
    s, clock, notifier, customer, agent, *_ = env
    high = s.tickets.create("Высокий", "", customer.id, Priority.HIGH)
    urgent = s.tickets.create("Срочный", "", customer.id, Priority.URGENT)
    normal = s.tickets.create("Обычный", "", customer.id, Priority.NORMAL)
    taken = s.tickets.create("Взят", "", customer.id, Priority.HIGH)
    s.tickets.change_status(taken.id, Status.IN_PROGRESS, agent.id)
    clock.advance(hours=5)
    fresh = s.tickets.create("Свежий", "", customer.id, Priority.URGENT)
    assert s.tickets.escalate_overdue() == [high.id, urgent.id]
    assert s.tickets.get(high.id).status == Status.ESCALATED
    assert Status.ESCALATED.value == "escalated"
    for t in (normal, taken, fresh):
        assert s.tickets.get(t.id).status != Status.ESCALATED


def test_boundary_inclusive_and_explicit_now(env):
    s, clock, notifier, customer, *_ = env
    t = s.tickets.create("A", "", customer.id, Priority.HIGH)
    assert s.tickets.escalate_overdue(T0 + timedelta(hours=3, minutes=59)) == []
    assert s.tickets.escalate_overdue(T0 + timedelta(hours=4)) == [t.id]


def test_not_escalated_twice(env):
    s, clock, notifier, customer, *_ = env
    s.tickets.create("A", "", customer.id, Priority.HIGH)
    clock.advance(hours=10)
    assert len(s.tickets.escalate_overdue()) == 1
    sent = len(notifier.sent)
    assert s.tickets.escalate_overdue() == []
    assert len(notifier.sent) == sent


def test_managers_notified(env):
    s, clock, notifier, customer, agent, m1, m2 = env
    s.users.deactivate(m2.id)
    t = s.tickets.create("Не грузится касса", "", customer.id, Priority.URGENT)
    clock.advance(hours=4)
    notifier.sent.clear()
    s.tickets.escalate_overdue()
    assert [n.to for n in notifier.sent] == [m1.email]
    assert notifier.sent[0].subject == f"[Эскалация] #{t.id} Не грузится касса"
    assert "urgent" in notifier.sent[0].body


def test_audit_entry(env):
    s, clock, notifier, customer, *_ = env
    t = s.tickets.create("A", "", customer.id, Priority.HIGH)
    clock.advance(hours=6)
    s.tickets.escalate_overdue()
    entry = s.audit.list(t.id)[-1]
    assert (entry.actor, entry.action, entry.ticket_id, entry.details) == ("system", "escalated", t.id, {"hours": "4"})
    assert entry.at == clock.now()


def test_custom_hours():
    clock = FixedClock(T0)
    s = build_services(clock=clock, settings=Settings(escalation_hours=1))
    c = s.users.create("Анна", "anna@example.com")
    t = s.tickets.create("A", "", c.id, Priority.HIGH)
    clock.advance(hours=1)
    assert s.tickets.escalate_overdue() == [t.id]
    assert s.audit.list(t.id)[-1].details == {"hours": "1"}


def test_transitions(env):
    s, clock, notifier, customer, agent, *_ = env
    a = s.tickets.create("A", "", customer.id, Priority.HIGH)
    b = s.tickets.create("B", "", customer.id, Priority.HIGH)
    c = s.tickets.create("C", "", customer.id, Priority.LOW)
    clock.advance(hours=5)
    s.tickets.escalate_overdue()
    assert s.tickets.change_status(a.id, Status.IN_PROGRESS, agent.id).status == Status.IN_PROGRESS
    assert s.tickets.change_status(b.id, Status.CLOSED, agent.id).status == Status.CLOSED
    with pytest.raises(InvalidTransition):
        s.tickets.change_status(c.id, Status.ESCALATED, agent.id)


def test_escalated_in_open_list(env):
    s, clock, notifier, customer, *_ = env
    t = s.tickets.create("A", "", customer.id, Priority.HIGH)
    clock.advance(hours=5)
    s.tickets.escalate_overdue()
    assert t.id in [x.id for x in s.tickets.list_open()]
