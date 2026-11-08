def test_actions_are_audited(services, customer, agent):
    t = services.tickets.create("A", "", customer.id)
    services.tickets.assign(t.id, agent.id, agent.id)
    assert [e.action for e in services.audit.list(t.id)] == ["created", "assigned"]
